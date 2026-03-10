"""MFP Standalone Server — configured process for managing agents and channels.

Entry point: python -m mfp.server --config runtime.yaml

Composes Runtime + StorageEngine + TransportServer into a single process.
Library-first: every operation is a call to the library API.

Maps to: impl/I-19_api.md
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mfp.config.validator import ConfigValidator

from mfp.core.primitives import sha256
from mfp.core.types import (
    AgentId,
    ChannelId,
    RuntimeId,
    StateValue,
)
from mfp.federation.bilateral import (
    BilateralChannel,
    bootstrap_ceremonial,
    bootstrap_deterministic,
    derive_bilateral_id,
)
from mfp.federation.recovery import RecoveryConfig
from mfp.federation.transport import TransportConfig, TransportServer
from mfp.federation.wire import build_envelope_header, validate_envelope
from mfp.runtime.pipeline import AgentCallable, RuntimeConfig
from mfp.runtime.runtime import Runtime
from mfp.storage.engine import (
    RuntimeMeta,
    StorageConfig,
    StorageEngine,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentConfig:
    """Agent configuration from YAML."""
    name: str
    type: str = "callback"  # callback, subprocess, webhook, llm_api


@dataclass(frozen=True)
class ChannelConfig:
    """Channel configuration from YAML."""
    agents: tuple[str, str] = ("", "")
    depth: int = 4


@dataclass(frozen=True)
class PeerConfig:
    """Federation peer configuration."""
    runtime_id: str = ""
    endpoint: str = ""
    bootstrap: str = "deterministic"  # deterministic or ceremonial


@dataclass(frozen=True)
class FederationConfig:
    """Federation configuration."""
    peers: tuple[PeerConfig, ...] = ()


@dataclass(frozen=True)
class ServerConfig:
    """Complete server configuration."""
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    agents: tuple[AgentConfig, ...] = ()
    channels: tuple[ChannelConfig, ...] = ()
    federation: FederationConfig = field(default_factory=FederationConfig)
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, raw: dict) -> ServerConfig:
        """Parse a configuration dictionary (from YAML)."""
        rt = raw.get("runtime", {})
        runtime_config = RuntimeConfig(
            deployment_id=rt.get("deployment_id", "").encode()
                if isinstance(rt.get("deployment_id"), str)
                else rt.get("deployment_id", b""),
            instance_id=rt.get("instance_id", "").encode()
                if isinstance(rt.get("instance_id"), str)
                else rt.get("instance_id", b""),
            default_frame_depth=rt.get("default_frame_depth", 4),
            validation_failure_threshold=raw.get("quarantine", {}).get(
                "validation_failure_threshold", 3,
            ),
            max_message_rate=raw.get("quarantine", {}).get("max_message_rate", 0),
            max_payload_size=raw.get("quarantine", {}).get("max_payload_size", 0),
        )

        st = raw.get("storage", {})
        storage_config = StorageConfig(
            db_path=st.get("path", ""),
            encrypt_at_rest=st.get("encrypt_at_rest", False),
            master_key=_load_master_key(st.get("master_key_file", "")),
            wal_mode=st.get("wal_mode", True),
        )

        tp = raw.get("transport", {})
        transport_config = TransportConfig(
            host=tp.get("host", "0.0.0.0"),
            port=tp.get("port", 9876),
            connect_timeout=tp.get("connect_timeout", 30.0),
            read_timeout=tp.get("read_timeout", 30.0),
            write_timeout=tp.get("write_timeout", 30.0),
        )

        rc = raw.get("recovery", {})
        recovery_config = RecoveryConfig(
            max_step_gap=rc.get("max_step_gap", 5),
            max_attempts=rc.get("max_attempts", 3),
            timeout_seconds=rc.get("timeout_seconds", 30),
        )

        agents = tuple(
            AgentConfig(
                name=a.get("name", ""),
                type=a.get("type", "callback"),
            )
            for a in raw.get("agents", [])
        )

        channels = tuple(
            ChannelConfig(
                agents=tuple(c.get("agents", ["", ""])[:2]),
                depth=c.get("depth", 4),
            )
            for c in raw.get("channels", [])
        )

        peers = tuple(
            PeerConfig(
                runtime_id=p.get("runtime_id", ""),
                endpoint=p.get("endpoint", ""),
                bootstrap=p.get("bootstrap", "deterministic"),
            )
            for p in raw.get("federation", {}).get("peers", [])
        )
        federation_config = FederationConfig(peers=peers)

        return cls(
            runtime=runtime_config,
            storage=storage_config,
            transport=transport_config,
            recovery=recovery_config,
            agents=agents,
            channels=channels,
            federation=federation_config,
            log_level=raw.get("log_level", "INFO"),
        )

    @classmethod
    def from_yaml(cls, path: str) -> ServerConfig:
        """Load configuration from a YAML file."""
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return cls.from_dict(raw)


def _load_yaml(path: str) -> dict:
    """Load YAML configuration file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_master_key(path: str) -> bytes:
    """Load encryption master key from file. Returns empty if no path."""
    if not path:
        return b""
    return Path(path).read_bytes().strip()


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class MFPServer:
    """Standalone MFP server process.

    Composes Runtime + StorageEngine + TransportServer.
    Manages agent binding, channel establishment, and federation
    based on YAML configuration.

    Maps to: I-19 §4.
    """

    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._runtime: Runtime | None = None
        self._storage: StorageEngine | None = None
        self._transport: TransportServer | None = None
        self._agent_handles: dict[str, AgentId] = {}  # name → AgentId
        self._running = False

    @property
    def runtime(self) -> Runtime | None:
        return self._runtime

    @property
    def storage(self) -> StorageEngine | None:
        return self._storage

    @property
    def agent_ids(self) -> dict[str, AgentId]:
        """Map of agent names to their IDs."""
        return dict(self._agent_handles)

    async def start(self) -> None:
        """Start the server.

        1. Initialize Runtime.
        2. Initialize StorageEngine (if configured).
        3. Recover state from storage (if DB exists).
        4. Bind agents from config.
        5. Establish channels from config.
        6. Start federation transport (if configured).
        """
        logger.info("Starting MFP server")

        # 1. Initialize Runtime
        self._runtime = Runtime(self._config.runtime)
        logger.info(
            "Runtime initialized, identity=%s",
            self._runtime.identity.data[:8].hex(),
        )

        # 2. Initialize StorageEngine
        if self._config.storage.db_path:
            self._storage = StorageEngine(self._config.storage)
            self._try_recover()
            self._persist_meta()

        # 3. Bind agents
        for agent_conf in self._config.agents:
            callable_ = _make_agent_callable(agent_conf)
            agent_id = self._runtime.bind_agent(callable_)
            self._agent_handles[agent_conf.name] = agent_id
            logger.info(
                "Bound agent %r → %s",
                agent_conf.name, agent_id.value[:8].hex(),
            )

        # 4. Establish channels
        for ch_conf in self._config.channels:
            name_a, name_b = ch_conf.agents
            id_a = self._agent_handles.get(name_a)
            id_b = self._agent_handles.get(name_b)
            if id_a is None or id_b is None:
                logger.warning(
                    "Skipping channel %s↔%s: agent not found", name_a, name_b,
                )
                continue
            ch_id = self._runtime.establish_channel(id_a, id_b, ch_conf.depth)
            logger.info(
                "Established channel %s↔%s → %s",
                name_a, name_b, ch_id.value.hex()[:8],
            )

        # 5. Start federation transport
        if self._config.federation.peers:
            await self._start_federation()

        self._running = True
        logger.info("MFP server started")

    async def stop(self) -> None:
        """Graceful shutdown.

        1. Stop accepting connections.
        2. Close bilateral connections.
        3. Shutdown Runtime (zeros all state).
        4. Persist final state.
        5. Close storage.
        """
        logger.info("Stopping MFP server")
        self._running = False

        # Stop transport
        if self._transport:
            await self._transport.stop()
            self._transport = None

        # Persist final state before shutdown
        if self._storage and self._runtime:
            self._persist_final_state()

        # Shutdown runtime (zeros state)
        if self._runtime:
            self._runtime.shutdown()
            self._runtime = None

        # Close storage
        if self._storage:
            self._storage.close()
            self._storage = None

        self._agent_handles.clear()
        logger.info("MFP server stopped")

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _try_recover(self) -> None:
        """Attempt crash recovery from storage."""
        if self._storage is None:
            return
        result = self._storage.recover()
        if result is None:
            logger.info("No existing state — fresh start")
            return
        for warning in result.warnings:
            logger.warning("Recovery: %s", warning)
        logger.info(
            "Recovered: %d agents, %d channels, %d bilateral",
            len(result.agents),
            len(result.channels),
            len(result.bilateral_channels),
        )

    def _persist_meta(self) -> None:
        """Persist runtime metadata to storage."""
        if self._storage is None or self._runtime is None:
            return
        meta = RuntimeMeta(
            runtime_id=self._runtime.identity.data,
            deployment_id=self._config.runtime.deployment_id,
            instance_id=self._config.runtime.instance_id,
            agent_counter=0,
            schema_version=1,
            created_at=int(time.time()),
        )
        self._storage.save_runtime_meta(meta)

    def _persist_final_state(self) -> None:
        """Persist Sg cache before shutdown."""
        if self._storage is None or self._runtime is None:
            return
        sg = self._runtime.global_state
        if sg is not None:
            self._storage.save_sg_cache(sg)

    # ------------------------------------------------------------------
    # Federation
    # ------------------------------------------------------------------

    async def _start_federation(self) -> None:
        """Initialize bilateral channels and start transport server."""
        if self._runtime is None:
            return

        local_rid = RuntimeId(value=self._runtime.identity)

        async def _handle_message(header, msg):
            errors = validate_envelope(header)
            if errors:
                logger.warning("Invalid envelope: %s", errors)
                return
            logger.debug(
                "Received federated message on channel %s step %d",
                header.channel_id.value.hex()[:8], header.step,
            )

        self._transport = TransportServer(self._config.transport, _handle_message)
        await self._transport.start()
        logger.info(
            "Federation transport listening on %s:%d",
            self._config.transport.host, self._config.transport.port,
        )


# ---------------------------------------------------------------------------
# Agent Callable Factory
# ---------------------------------------------------------------------------

def _make_agent_callable(agent_config: AgentConfig) -> AgentCallable:
    """Create an agent callable from configuration.

    For now, all agent types use a logging callback. Subprocess, webhook,
    and LLM API backends are extension points for future implementation.
    """
    name = agent_config.name

    def callback(msg):
        logger.debug("Agent %r received message: %d bytes", name, len(msg.payload))

    return callback


# ---------------------------------------------------------------------------
# Signal Handling
# ---------------------------------------------------------------------------

def _setup_signals(server: MFPServer, loop: asyncio.AbstractEventLoop) -> None:
    """Register signal handlers for graceful shutdown."""
    def _request_shutdown(sig):
        logger.info("Received %s, shutting down", sig.name)
        loop.create_task(server.stop())
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown, sig)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="mfp.server",
        description="MFP Standalone Server",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    return parser.parse_args(argv)


async def _run_server(config: ServerConfig) -> None:
    """Run the server until shutdown signal."""
    server = MFPServer(config)
    loop = asyncio.get_event_loop()
    _setup_signals(server, loop)

    await server.start()

    # Keep running until stopped
    try:
        while server._running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        if server._running:
            await server.stop()


def main(argv: list[str] | None = None) -> None:
    """Main entry point for `python -m mfp.server`."""
    args = parse_args(argv)

    # Load configuration
    if args.config:
        raw_config = _load_yaml(args.config)

        # Validate configuration
        validator = ConfigValidator(strict=False)
        warnings = validator.validate(raw_config)

        # Parse after validation
        config = ServerConfig.from_dict(raw_config)
    else:
        config = ServerConfig()
        warnings = []

    # Setup logging
    log_level = args.log_level or config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Print configuration warnings
    if warnings:
        logger.warning("Configuration validation warnings:")
        for w in warnings:
            level_str = w.severity.upper()
            logger.log(
                logging.ERROR if w.severity == "error" else logging.WARNING,
                f"  [{level_str}] {w.field}: {w.message}",
            )

        # Exit if critical errors found
        errors = [w for w in warnings if w.severity == "error"]
        if errors:
            logger.error("Critical configuration errors found, exiting")
            sys.exit(1)

    logger.info("MFP Server starting")
    asyncio.run(_run_server(config))


if __name__ == "__main__":
    main()
