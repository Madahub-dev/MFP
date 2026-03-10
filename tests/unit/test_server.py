"""Unit tests for mfp/server.py (I-19)."""

import asyncio
import tempfile

import pytest
import yaml

from mfp.server import (
    AgentConfig,
    ChannelConfig,
    FederationConfig,
    MFPServer,
    PeerConfig,
    ServerConfig,
    parse_args,
)


# ---------------------------------------------------------------------------
# ServerConfig.from_dict
# ---------------------------------------------------------------------------

class TestServerConfigFromDict:
    def test_empty_dict(self):
        config = ServerConfig.from_dict({})
        assert config.runtime.default_frame_depth == 4
        assert config.storage.db_path == ""
        assert config.transport.port == 9876
        assert config.agents == ()
        assert config.channels == ()

    def test_runtime_fields(self):
        config = ServerConfig.from_dict({
            "runtime": {
                "deployment_id": "my-deploy",
                "instance_id": "inst-1",
                "default_frame_depth": 8,
            },
        })
        assert config.runtime.deployment_id == b"my-deploy"
        assert config.runtime.instance_id == b"inst-1"
        assert config.runtime.default_frame_depth == 8

    def test_quarantine_fields(self):
        config = ServerConfig.from_dict({
            "quarantine": {
                "validation_failure_threshold": 10,
                "max_message_rate": 100,
                "max_payload_size": 4096,
            },
        })
        assert config.runtime.validation_failure_threshold == 10
        assert config.runtime.max_message_rate == 100
        assert config.runtime.max_payload_size == 4096

    def test_storage_fields(self):
        config = ServerConfig.from_dict({
            "storage": {
                "path": "/tmp/test.db",
                "encrypt_at_rest": True,
                "wal_mode": False,
            },
        })
        assert config.storage.db_path == "/tmp/test.db"
        assert config.storage.encrypt_at_rest is True
        assert config.storage.wal_mode is False

    def test_transport_fields(self):
        config = ServerConfig.from_dict({
            "transport": {
                "host": "127.0.0.1",
                "port": 1234,
                "connect_timeout": 10,
            },
        })
        assert config.transport.host == "127.0.0.1"
        assert config.transport.port == 1234
        assert config.transport.connect_timeout == 10

    def test_recovery_fields(self):
        config = ServerConfig.from_dict({
            "recovery": {
                "max_step_gap": 10,
                "max_attempts": 5,
                "timeout_seconds": 60,
            },
        })
        assert config.recovery.max_step_gap == 10
        assert config.recovery.max_attempts == 5
        assert config.recovery.timeout_seconds == 60

    def test_agents(self):
        config = ServerConfig.from_dict({
            "agents": [
                {"name": "alice", "type": "callback"},
                {"name": "bob", "type": "webhook"},
            ],
        })
        assert len(config.agents) == 2
        assert config.agents[0].name == "alice"
        assert config.agents[1].type == "webhook"

    def test_channels(self):
        config = ServerConfig.from_dict({
            "channels": [
                {"agents": ["alice", "bob"], "depth": 6},
            ],
        })
        assert len(config.channels) == 1
        assert config.channels[0].agents == ("alice", "bob")
        assert config.channels[0].depth == 6

    def test_federation_peers(self):
        config = ServerConfig.from_dict({
            "federation": {
                "peers": [
                    {
                        "runtime_id": "peer-1",
                        "endpoint": "10.0.0.1:9876",
                        "bootstrap": "ceremonial",
                    },
                ],
            },
        })
        assert len(config.federation.peers) == 1
        assert config.federation.peers[0].runtime_id == "peer-1"
        assert config.federation.peers[0].bootstrap == "ceremonial"

    def test_log_level(self):
        config = ServerConfig.from_dict({"log_level": "DEBUG"})
        assert config.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# ServerConfig.from_yaml
# ---------------------------------------------------------------------------

class TestServerConfigFromYaml:
    def test_load_yaml(self):
        raw = {
            "runtime": {"deployment_id": "test-deploy"},
            "agents": [{"name": "agent-a"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(raw, f)
            f.flush()
            config = ServerConfig.from_yaml(f.name)
        assert config.runtime.deployment_id == b"test-deploy"
        assert len(config.agents) == 1

    def test_empty_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            config = ServerConfig.from_yaml(f.name)
        assert config.agents == ()


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_no_args(self):
        args = parse_args([])
        assert args.config == ""
        assert args.log_level == ""

    def test_config(self):
        args = parse_args(["--config", "foo.yaml"])
        assert args.config == "foo.yaml"

    def test_short_config(self):
        args = parse_args(["-c", "bar.yaml"])
        assert args.config == "bar.yaml"

    def test_log_level(self):
        args = parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# MFPServer lifecycle
# ---------------------------------------------------------------------------

class TestMFPServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_empty(self):
        """Server starts and stops with no agents or channels."""
        server = MFPServer(ServerConfig())
        await server.start()
        assert server.runtime is not None
        assert server._running is True
        await server.stop()
        assert server.runtime is None
        assert server._running is False

    @pytest.mark.asyncio
    async def test_start_with_agents(self):
        """Server binds agents from config."""
        config = ServerConfig.from_dict({
            "agents": [
                {"name": "alice"},
                {"name": "bob"},
            ],
        })
        server = MFPServer(config)
        await server.start()
        assert "alice" in server.agent_ids
        assert "bob" in server.agent_ids
        assert len(server.agent_ids) == 2
        await server.stop()

    @pytest.mark.asyncio
    async def test_start_with_channels(self):
        """Server establishes channels between named agents."""
        config = ServerConfig.from_dict({
            "agents": [
                {"name": "alice"},
                {"name": "bob"},
            ],
            "channels": [
                {"agents": ["alice", "bob"], "depth": 4},
            ],
        })
        server = MFPServer(config)
        await server.start()
        # Verify channel exists via agent query
        alice_id = server.agent_ids["alice"]
        channels = server.runtime.get_channels(alice_id)
        assert len(channels) == 1
        await server.stop()

    @pytest.mark.asyncio
    async def test_skip_channel_with_unknown_agent(self):
        """Channel referencing unknown agent is skipped, not an error."""
        config = ServerConfig.from_dict({
            "agents": [{"name": "alice"}],
            "channels": [
                {"agents": ["alice", "unknown"], "depth": 4},
            ],
        })
        server = MFPServer(config)
        await server.start()
        alice_id = server.agent_ids["alice"]
        channels = server.runtime.get_channels(alice_id)
        assert len(channels) == 0
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """Stopping before start should not raise."""
        server = MFPServer(ServerConfig())
        await server.stop()

    @pytest.mark.asyncio
    async def test_send_message_through_server(self):
        """Full message send through server-configured runtime."""
        delivered = []

        config = ServerConfig.from_dict({
            "agents": [
                {"name": "alice"},
                {"name": "bob"},
            ],
            "channels": [
                {"agents": ["alice", "bob"]},
            ],
        })
        server = MFPServer(config)
        await server.start()

        alice_id = server.agent_ids["alice"]
        bob_id = server.agent_ids["bob"]
        channels = server.runtime.get_channels(alice_id)
        ch_id = channels[0].channel_id

        receipt = server.runtime.send(alice_id, ch_id, b"hello from server")
        assert receipt.step == 0

        await server.stop()

    @pytest.mark.asyncio
    async def test_with_storage(self):
        """Server with in-memory storage persists metadata."""
        config = ServerConfig.from_dict({
            "storage": {"path": ":memory:"},
            "agents": [{"name": "alice"}],
        })
        server = MFPServer(config)
        await server.start()
        assert server.storage is not None
        meta = server.storage.load_runtime_meta()
        assert meta is not None
        await server.stop()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class TestDataClasses:
    def test_agent_config_defaults(self):
        ac = AgentConfig(name="test")
        assert ac.type == "callback"

    def test_channel_config_defaults(self):
        cc = ChannelConfig()
        assert cc.depth == 4

    def test_peer_config_defaults(self):
        pc = PeerConfig()
        assert pc.bootstrap == "deterministic"

    def test_federation_config_defaults(self):
        fc = FederationConfig()
        assert fc.peers == ()
