"""Unit tests for mfp/storage/engine.py (I-14)."""

import time

import pytest

from mfp.core.primitives import sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import (
    AgentId,
    Channel,
    ChannelId,
    ChannelState,
    ChannelStatus,
    DEFAULT_FRAME_DEPTH,
    GlobalState,
    StateValue,
)
from mfp.storage.engine import (
    AgentRow,
    BilateralRow,
    ChannelRow,
    RecoveryResult,
    RuntimeMeta,
    StorageConfig,
    StorageEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runtime_id(deployment: bytes = b"\x01" * 16, instance: bytes = b"\x02" * 16) -> bytes:
    return sha256(deployment + instance).data


def _make_meta(
    deployment: bytes = b"\x01" * 16,
    instance: bytes = b"\x02" * 16,
) -> RuntimeMeta:
    rid = _runtime_id(deployment, instance)
    return RuntimeMeta(
        runtime_id=rid,
        deployment_id=deployment,
        instance_id=instance,
        agent_counter=0,
        schema_version=1,
        created_at=int(time.time()),
    )


def _make_engine(encrypt: bool = False) -> StorageEngine:
    cfg = StorageConfig(
        db_path="",
        encrypt_at_rest=encrypt,
        master_key=b"\xcc" * 32 if encrypt else b"",
        wal_mode=False,
    )
    return StorageEngine(cfg)


def _agent_id(n: int = 1) -> bytes:
    return bytes([n]) * 16


def _channel_id(n: int = 1) -> bytes:
    return bytes([n]) * 16


def _state_value(n: int = 1) -> StateValue:
    return StateValue(bytes([n]) * 32)


def _make_channel(
    ch_id_n: int = 1,
    agent_a_n: int = 1,
    agent_b_n: int = 2,
    local_state_n: int = 0xAA,
    step: int = 0,
    depth: int = DEFAULT_FRAME_DEPTH,
) -> Channel:
    return Channel(
        channel_id=ChannelId(_channel_id(ch_id_n)),
        agent_a=AgentId(_agent_id(agent_a_n)),
        agent_b=AgentId(_agent_id(agent_b_n)),
        state=ChannelState(local_state=_state_value(local_state_n), step=step),
        depth=depth,
        status=ChannelStatus.ACTIVE,
    )


def _compute_sg(channels: list[Channel]) -> GlobalState:
    return compose_ordered(
        channel_states=[
            (ch.channel_id, ch.state.local_state) for ch in channels
        ],
    )


# ---------------------------------------------------------------------------
# StorageConfig
# ---------------------------------------------------------------------------

class TestStorageConfig:
    def test_defaults(self):
        cfg = StorageConfig()
        assert cfg.db_path == ""
        assert cfg.encrypt_at_rest is False
        assert cfg.master_key == b""
        assert cfg.wal_mode is True


# ---------------------------------------------------------------------------
# StorageEngine creation
# ---------------------------------------------------------------------------

class TestStorageEngineCreation:
    def test_in_memory(self):
        engine = _make_engine()
        # Engine should be usable immediately
        assert engine.load_runtime_meta() is None
        engine.close()


# ---------------------------------------------------------------------------
# Runtime Meta
# ---------------------------------------------------------------------------

class TestRuntimeMeta:
    def test_save_load_roundtrip(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        loaded = engine.load_runtime_meta()
        assert loaded is not None
        assert loaded.runtime_id == meta.runtime_id
        assert loaded.deployment_id == meta.deployment_id
        assert loaded.instance_id == meta.instance_id
        assert loaded.agent_counter == meta.agent_counter
        assert loaded.schema_version == meta.schema_version
        assert loaded.created_at == meta.created_at
        engine.close()

    def test_load_returns_none_on_empty(self):
        engine = _make_engine()
        assert engine.load_runtime_meta() is None
        engine.close()


# ---------------------------------------------------------------------------
# Agent Counter
# ---------------------------------------------------------------------------

class TestAgentCounter:
    def test_increment_agent_counter(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        new_val = engine.increment_agent_counter(meta.runtime_id)
        assert new_val == 1
        new_val = engine.increment_agent_counter(meta.runtime_id)
        assert new_val == 2
        engine.close()


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class TestAgents:
    def test_save_load_roundtrip(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        aid = _agent_id(1)
        engine.save_agent(aid, "bound", meta.runtime_id)
        agents = engine.load_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == aid
        assert agents[0].state == "bound"
        engine.close()

    def test_load_agents_filters_terminated(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        aid1 = _agent_id(1)
        aid2 = _agent_id(2)
        engine.save_agent(aid1, "active", meta.runtime_id)
        engine.save_agent(aid2, "active", meta.runtime_id)
        engine.update_agent_state(aid2, "terminated")

        agents = engine.load_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == aid1
        engine.close()

    def test_update_agent_state(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        aid = _agent_id(1)
        engine.save_agent(aid, "bound", meta.runtime_id)
        engine.update_agent_state(aid, "quarantined", reason="test reason")

        # Load all agents including quarantined
        agents = engine.load_agents()
        assert len(agents) == 1
        assert agents[0].state == "quarantined"
        assert agents[0].quarantine_reason == "test reason"
        engine.close()

    def test_delete_agent(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        aid = _agent_id(1)
        engine.save_agent(aid, "active", meta.runtime_id)
        assert len(engine.load_agents()) == 1

        engine.delete_agent(aid)
        assert len(engine.load_agents()) == 0
        engine.close()


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

class TestChannels:
    def _setup_agents(self, engine: StorageEngine, meta: RuntimeMeta) -> None:
        """Insert two agents so foreign-key constraints are satisfied."""
        engine.save_agent(_agent_id(1), "bound", meta.runtime_id)
        engine.save_agent(_agent_id(2), "bound", meta.runtime_id)

    def test_save_load_roundtrip(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        self._setup_agents(engine, meta)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)

        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].channel_id == ch.channel_id.value
        assert rows[0].local_state == ch.state.local_state.data
        assert rows[0].step == ch.state.step
        assert rows[0].depth == ch.depth
        assert rows[0].status == "active"
        engine.close()

    def test_advance_channel(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        self._setup_agents(engine, meta)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)

        new_state = _state_value(0xBB)
        new_sg = GlobalState(value=new_state)
        engine.advance_channel(ch.channel_id.value, new_state, new_sg)

        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].local_state == new_state.data
        assert rows[0].step == 1
        engine.close()

    def test_close_channel_zeros_local_state(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        self._setup_agents(engine, meta)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)

        engine.close_channel(ch.channel_id.value, None)

        # Closed channels are filtered out of load_channels
        rows = engine.load_channels()
        assert len(rows) == 0

        # Directly query to verify zeroed state and closed status
        cursor = engine._conn.execute(
            "SELECT local_state, status FROM channels WHERE channel_id = ?",
            (ch.channel_id.value,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == b"\x00" * 32
        assert row[1] == "closed"
        engine.close()

    def test_quarantine_and_restore_channel(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        self._setup_agents(engine, meta)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)

        engine.quarantine_channel(ch.channel_id.value)
        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].status == "quarantined"

        engine.restore_channel(ch.channel_id.value)
        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].status == "active"
        engine.close()

    def test_load_channels_filters_closed(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        self._setup_agents(engine, meta)

        # Need a third agent for the second channel
        engine.save_agent(_agent_id(3), "bound", meta.runtime_id)

        ch1 = _make_channel(ch_id_n=1, agent_a_n=1, agent_b_n=2)
        ch2 = _make_channel(ch_id_n=2, agent_a_n=1, agent_b_n=3)
        sg = _compute_sg([ch1, ch2])
        engine.save_channel(ch1, None)
        engine.save_channel(ch2, sg)

        engine.close_channel(ch1.channel_id.value, None)

        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].channel_id == ch2.channel_id.value
        engine.close()


# ---------------------------------------------------------------------------
# Global State Cache
# ---------------------------------------------------------------------------

class TestSgCache:
    def test_save_and_load(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        sg = GlobalState(value=_state_value(0xDD))
        engine.save_sg_cache(sg)
        loaded = engine.load_sg_cache()
        assert loaded is not None
        assert loaded.value.data == sg.value.data
        engine.close()

    def test_load_returns_none_when_empty(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        assert engine.load_sg_cache() is None
        engine.close()


# ---------------------------------------------------------------------------
# Bilateral Channels
# ---------------------------------------------------------------------------

class TestBilateralChannels:
    def test_save_load_roundtrip(self):
        engine = _make_engine()
        meta = _make_meta()
        engine.save_runtime_meta(meta)

        now = int(time.time())
        row = BilateralRow(
            bilateral_id=b"\x50" * 16,
            runtime_id_local=meta.runtime_id,
            runtime_id_peer=b"\x60" * 32,
            ratchet_state=b"\x70" * 32,
            shared_prng_seed=b"\x80" * 32,
            step=0,
            status="active",
            created_at=now,
            updated_at=now,
        )
        engine.save_bilateral(row)
        loaded = engine.load_bilateral_channels()
        assert len(loaded) == 1
        assert loaded[0].bilateral_id == row.bilateral_id
        assert loaded[0].ratchet_state == row.ratchet_state
        assert loaded[0].shared_prng_seed == row.shared_prng_seed
        assert loaded[0].step == 0
        assert loaded[0].status == "active"
        engine.close()


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestRecover:
    def _setup_full(self, engine: StorageEngine) -> tuple[RuntimeMeta, Channel]:
        """Set up meta, agents, one channel, and Sg cache."""
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        engine.save_agent(_agent_id(1), "active", meta.runtime_id)
        engine.save_agent(_agent_id(2), "active", meta.runtime_id)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)
        return meta, ch

    def test_returns_none_on_empty(self):
        engine = _make_engine()
        result = engine.recover()
        assert result is None
        engine.close()

    def test_loads_agents_and_channels(self):
        engine = _make_engine()
        meta, ch = self._setup_full(engine)

        result = engine.recover()
        assert result is not None
        assert result.meta.runtime_id == meta.runtime_id
        assert len(result.agents) == 2
        assert len(result.channels) == 1
        assert result.channels[0].channel_id == ch.channel_id.value
        assert result.sg is not None
        engine.close()

    def test_recomputes_sg(self):
        engine = _make_engine()
        meta, ch = self._setup_full(engine)

        result = engine.recover()
        expected_sg = _compute_sg([ch])
        assert result.sg is not None
        assert result.sg.value.data == expected_sg.value.data
        engine.close()

    def test_detects_sg_cache_mismatch(self):
        engine = _make_engine()
        meta, ch = self._setup_full(engine)

        # Tamper with the Sg cache
        wrong_sg = GlobalState(value=_state_value(0xFF))
        engine.save_sg_cache(wrong_sg)

        result = engine.recover()
        assert any("mismatch" in w.lower() for w in result.warnings)
        engine.close()


# ---------------------------------------------------------------------------
# Engine with encryption
# ---------------------------------------------------------------------------

class TestEncryptedEngine:
    def test_full_roundtrip_with_encryption(self):
        engine = _make_engine(encrypt=True)
        meta = _make_meta()
        engine.save_runtime_meta(meta)
        engine.save_agent(_agent_id(1), "bound", meta.runtime_id)
        engine.save_agent(_agent_id(2), "bound", meta.runtime_id)

        ch = _make_channel()
        sg = _compute_sg([ch])
        engine.save_channel(ch, sg)

        rows = engine.load_channels()
        assert len(rows) == 1
        assert rows[0].local_state == ch.state.local_state.data
        assert rows[0].step == ch.state.step
        assert rows[0].status == "active"

        # Verify data at rest is encrypted (raw bytes differ from plaintext)
        cursor = engine._conn.execute(
            "SELECT local_state FROM channels WHERE channel_id = ?",
            (ch.channel_id.value,),
        )
        raw = cursor.fetchone()[0]
        assert raw != ch.state.local_state.data  # encrypted differs from plaintext
        engine.close()
