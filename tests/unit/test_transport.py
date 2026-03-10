"""Unit tests for mfp/federation/transport.py (I-17)."""

import asyncio

import pytest

from mfp.core.types import (
    ENVELOPE_HEADER_SIZE,
    Block,
    ChannelId,
    EnvelopeFlags,
    Frame,
    ProtocolMessage,
)
from mfp.federation.transport import (
    ConnectionPool,
    TransportClient,
    TransportConfig,
    TransportServer,
    read_message,
    write_message,
)
from mfp.federation.wire import build_envelope_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel_id() -> ChannelId:
    return ChannelId(b"\x01" * 16)


def _frame(depth: int = 2) -> Frame:
    return Frame(tuple(Block(bytes([i + 1]) * 16) for i in range(depth)))


def _protocol_msg(depth: int = 2, payload: bytes = b"test") -> ProtocolMessage:
    f = _frame(depth)
    return ProtocolMessage(frame_open=f, encoded_payload=payload, frame_close=f.mirror())


def _header(depth: int = 2, payload_len: int = 4):
    return build_envelope_header(
        channel_id=_channel_id(),
        step=1,
        frame_depth=depth,
        payload_len=payload_len,
        sender_runtime=b"\xaa" * 16,
    )


# ---------------------------------------------------------------------------
# TransportConfig
# ---------------------------------------------------------------------------

class TestTransportConfig:
    def test_defaults(self):
        c = TransportConfig()
        assert c.host == "0.0.0.0"
        assert c.port == 9876
        assert c.connect_timeout == 30.0
        assert c.max_reconnect_attempts == 10

    def test_custom_values(self):
        c = TransportConfig(host="127.0.0.1", port=1234, backoff_base=0.01)
        assert c.host == "127.0.0.1"
        assert c.port == 1234
        assert c.backoff_base == 0.01


# ---------------------------------------------------------------------------
# read_message / write_message (in-memory stream roundtrip)
# ---------------------------------------------------------------------------

class TestMessageIO:
    @pytest.mark.asyncio
    async def test_roundtrip(self):
        """Write then read a message through an in-memory stream pair."""
        reader = asyncio.StreamReader()
        mock_transport = _MockTransport()
        protocol = asyncio.StreamReaderProtocol(reader)
        protocol.connection_made(mock_transport)
        writer = asyncio.StreamWriter(
            mock_transport, protocol, reader, asyncio.get_event_loop()
        )

        h = _header()
        msg = _protocol_msg()

        await write_message(writer, h, msg, timeout=5.0)

        # Feed written data into a new reader for parsing
        parse_reader = asyncio.StreamReader()
        parse_reader.feed_data(mock_transport.written)
        parse_reader.feed_eof()

        h2, msg2 = await read_message(parse_reader, timeout=5.0)
        assert h2.step == h.step
        assert h2.frame_depth == h.frame_depth
        assert msg2.encoded_payload == b"test"

    @pytest.mark.asyncio
    async def test_read_timeout(self):
        """Reading from an empty stream should timeout."""
        reader = asyncio.StreamReader()
        # Don't feed any data
        with pytest.raises(asyncio.TimeoutError):
            await read_message(reader, timeout=0.05)

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        """Multiple messages written sequentially can be read back."""
        reader = asyncio.StreamReader()
        mock_transport = _MockTransport()
        protocol = asyncio.StreamReaderProtocol(reader)
        protocol.connection_made(mock_transport)
        writer = asyncio.StreamWriter(
            mock_transport, protocol, reader, asyncio.get_event_loop()
        )

        payloads = [b"msg1", b"msg2", b"msg3"]
        for payload in payloads:
            h = _header(payload_len=len(payload))
            msg = _protocol_msg(payload=payload)
            await write_message(writer, h, msg, timeout=5.0)

        parse_reader = asyncio.StreamReader()
        parse_reader.feed_data(mock_transport.written)
        parse_reader.feed_eof()

        for payload in payloads:
            _, msg2 = await read_message(parse_reader, timeout=5.0)
            assert msg2.encoded_payload == payload


# ---------------------------------------------------------------------------
# TransportClient
# ---------------------------------------------------------------------------

class TestTransportClient:
    def test_not_connected_initially(self):
        client = TransportClient("localhost", 9999, TransportConfig())
        assert not client.connected

    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self):
        client = TransportClient("localhost", 9999, TransportConfig())
        with pytest.raises(ConnectionError, match="Not connected"):
            await client.send(_header(), _protocol_msg())

    @pytest.mark.asyncio
    async def test_receive_raises_when_not_connected(self):
        client = TransportClient("localhost", 9999, TransportConfig())
        with pytest.raises(ConnectionError, match="Not connected"):
            await client.receive()

    @pytest.mark.asyncio
    async def test_close_when_not_connected(self):
        """Closing when not connected should not raise."""
        client = TransportClient("localhost", 9999, TransportConfig())
        await client.close()  # should be no-op


# ---------------------------------------------------------------------------
# ConnectionPool
# ---------------------------------------------------------------------------

class TestConnectionPool:
    @pytest.mark.asyncio
    async def test_close_all_empty(self):
        pool = ConnectionPool(TransportConfig())
        await pool.close_all()  # should not raise

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        pool = ConnectionPool(TransportConfig())
        await pool.remove(b"\x01" * 16)  # should not raise


# ---------------------------------------------------------------------------
# TransportServer
# ---------------------------------------------------------------------------

class TestTransportServer:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Server should start and stop without error on a free port."""
        handler_called = asyncio.Event()

        async def dummy_handler(header, msg):
            handler_called.set()

        config = TransportConfig(host="127.0.0.1", port=0)
        # Use port=0 — but TransportServer uses config.port directly
        # Let's just test start/stop on a specific port
        config = TransportConfig(host="127.0.0.1", port=19876)
        server = TransportServer(config, dummy_handler)
        await server.start()
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        async def dummy_handler(header, msg):
            pass
        config = TransportConfig(host="127.0.0.1", port=19877)
        server = TransportServer(config, dummy_handler)
        await server.stop()  # should not raise


# ---------------------------------------------------------------------------
# Mock transport for in-memory testing
# ---------------------------------------------------------------------------

class _MockTransport(asyncio.Transport):
    """Captures written bytes instead of sending over a real socket."""

    def __init__(self):
        super().__init__()
        self.written = b""
        self._closing = False

    def write(self, data: bytes) -> None:
        self.written += data

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    def get_extra_info(self, name, default=None):
        return default
