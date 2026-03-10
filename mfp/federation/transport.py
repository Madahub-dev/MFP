"""MFP TCP Transport — asyncio server, client, connection pool.

Carries wire-formatted messages (I-16) between runtimes over persistent
TCP connections. One connection per bilateral channel.

Maps to: impl/I-17_transport.md
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mfp.core.types import (
    BLOCK_SIZE,
    ENVELOPE_HEADER_SIZE,
    EnvelopeHeader,
    ProtocolMessage,
)
from mfp.observability.logging import LogContext, get_logger, log_audit_event

logger = get_logger(__name__)

MessageHandler = Callable[[EnvelopeHeader, ProtocolMessage], Awaitable[None]]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportConfig:
    """TCP transport configuration."""
    host: str = "0.0.0.0"
    port: int = 9876
    connect_timeout: float = 30.0
    read_timeout: float = 30.0
    write_timeout: float = 30.0
    keepalive_interval: float = 60.0
    backoff_base: float = 0.1       # 100ms
    backoff_max: float = 30.0       # 30s
    max_reconnect_attempts: int = 10
    max_read_buffer: int = 1_048_576  # 1 MB


# ---------------------------------------------------------------------------
# Message I/O
# ---------------------------------------------------------------------------

async def read_message(
    reader: asyncio.StreamReader,
    timeout: float = 30.0,
) -> tuple[EnvelopeHeader, ProtocolMessage]:
    """Read one complete wire message from the stream.

    1. Read 64-byte header.
    2. Compute body size from header fields.
    3. Read body.
    4. Parse protocol message.
    """
    header_bytes = await asyncio.wait_for(
        reader.readexactly(ENVELOPE_HEADER_SIZE),
        timeout=timeout,
    )
    header = EnvelopeHeader.from_bytes(header_bytes)

    frame_size = header.frame_depth * BLOCK_SIZE
    body_size = 2 * frame_size + header.payload_len

    body_bytes = await asyncio.wait_for(
        reader.readexactly(body_size),
        timeout=timeout,
    )

    msg = ProtocolMessage.from_bytes(body_bytes, header.frame_depth)
    return header, msg


async def write_message(
    writer: asyncio.StreamWriter,
    header: EnvelopeHeader,
    msg: ProtocolMessage,
    timeout: float = 30.0,
) -> None:
    """Write one complete wire message to the stream."""
    data = header.to_bytes() + msg.to_bytes()
    writer.write(data)
    await asyncio.wait_for(writer.drain(), timeout=timeout)


# ---------------------------------------------------------------------------
# Server (Listener)
# ---------------------------------------------------------------------------

class TransportServer:
    """Asyncio TCP server accepting bilateral connections."""

    def __init__(
        self,
        config: TransportConfig,
        message_handler: MessageHandler,
    ) -> None:
        self._config = config
        self._handler = message_handler
        self._server: asyncio.AbstractServer | None = None
        self._connections: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start listening for connections."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self._config.host,
            self._config.port,
        )
        context = LogContext(
            correlation_id="transport_start",
            runtime_id="",
            operation="server_start",
        )
        logger.info(
            f"Transport server listening on {self._config.host}:{self._config.port}",
            context=context,
        )

    async def stop(self) -> None:
        """Stop accepting, close all connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for task in self._connections:
            task.cancel()
        self._connections.clear()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming bilateral connection."""
        peer = writer.get_extra_info("peername", "unknown")
        logger.info(f"Accepted connection from {peer}")
        task = asyncio.current_task()
        if task:
            self._connections.append(task)
        try:
            while True:
                header, msg = await read_message(
                    reader, timeout=self._config.read_timeout,
                )
                await self._handler(header, msg)
        except asyncio.IncompleteReadError:
            logger.info(f"Peer {peer} disconnected")
        except asyncio.TimeoutError:
            logger.warning(f"Read timeout from {peer}")
        except Exception as e:
            logger.error(f"Connection error from {peer}: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if task and task in self._connections:
                self._connections.remove(task)


# ---------------------------------------------------------------------------
# Client (Initiator)
# ---------------------------------------------------------------------------

class TransportClient:
    """Outbound connection to a peer runtime."""

    def __init__(self, host: str, port: int, config: TransportConfig) -> None:
        self._host = host
        self._port = port
        self._config = config
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Establish TCP connection."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=self._config.connect_timeout,
        )
        logger.info(f"Connected to {self._host}:{self._port}")

    async def send(
        self,
        header: EnvelopeHeader,
        msg: ProtocolMessage,
    ) -> None:
        """Send a wire message. Raises if not connected."""
        if self._writer is None:
            raise ConnectionError("Not connected")
        await write_message(
            self._writer, header, msg,
            timeout=self._config.write_timeout,
        )

    async def receive(self) -> tuple[EnvelopeHeader, ProtocolMessage]:
        """Receive a wire message. Raises if not connected."""
        if self._reader is None:
            raise ConnectionError("Not connected")
        return await read_message(
            self._reader, timeout=self._config.read_timeout,
        )

    async def close(self) -> None:
        """Close connection gracefully."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None


# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------

class ConnectionPool:
    """Manages one connection per bilateral channel (runtime pair)."""

    def __init__(self, config: TransportConfig) -> None:
        self._config = config
        self._clients: dict[bytes, TransportClient] = {}

    async def get_or_create(
        self,
        peer_runtime_id: bytes,
        host: str,
        port: int,
    ) -> TransportClient:
        """Get existing or create new connection to peer."""
        client = self._clients.get(peer_runtime_id)
        if client and client.connected:
            return client

        client = TransportClient(host, port, self._config)
        await self._connect_with_backoff(client)
        self._clients[peer_runtime_id] = client
        return client

    async def remove(self, peer_runtime_id: bytes) -> None:
        """Close and remove a connection."""
        client = self._clients.pop(peer_runtime_id, None)
        if client:
            await client.close()

    async def close_all(self) -> None:
        """Close all connections."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    async def _connect_with_backoff(self, client: TransportClient) -> None:
        """Connect with exponential backoff on failure."""
        delay = self._config.backoff_base
        for attempt in range(self._config.max_reconnect_attempts):
            try:
                await client.connect()
                return
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < self._config.max_reconnect_attempts - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._config.backoff_max)
        raise ConnectionError(
            f"Failed to connect after {self._config.max_reconnect_attempts} attempts"
        )
