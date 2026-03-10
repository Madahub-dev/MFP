"""Integration tests for Storage Engine — cross-module composition.

Tests the StorageEngine composing with Runtime state: persist, recover,
verify roundtrip integrity for agents, channels, global state, and encryption.
"""

import time

import pytest

from mfp.core.primitives import sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import (
    AgentState,
    Channel,
    ChannelId,
    ChannelState,
    ChannelStatus,
    DeliveredMessage,
    GlobalState,
    StateValue,
    DEFAULT_FRAME_DEPTH,
)
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime
from mfp.storage.engine import (
    AgentRow,
    ChannelRow,
    RuntimeMeta,
    StorageConfig,
    StorageEngine,
)
from mfp.storage.schema import SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers (plain functions, no fixtures)
# ---------------------------------------------------------------------------

def noop_agent(msg: DeliveredMessage) -> None:
    """Agent callable that does nothing."""
    pass


def collecting_agent() -> tuple[list[DeliveredMessage], "AgentCallable"]:
    """Return a list and an agent callable that appends delivered messages."""
    inbox: list[DeliveredMessage] = []

    def handler(msg: DeliveredMessage) -> None:
        inbox.append(msg)

    return inbox, handler


def make_deterministic_config() -> RuntimeConfig:
    """Create a RuntimeConfig with deterministic identity."""
    return RuntimeConfig(
        deployment_id=b"test-deploy" + b"\x00" * 21,
        instance_id=b"test-instance" + b"\x00" * 19,
    )


def make_runtime(config: RuntimeConfig | None = None) -> Runtime:
    """Create a fresh Runtime instance."""
    return Runtime(config or make_deterministic_config())


def make_storage(encrypt: bool = False) -> StorageEngine:
    """Create an in-memory StorageEngine."""
    config = StorageConfig(
        db_path="",
        encrypt_at_rest=encrypt,
        master_key=b"master-key-for-testing" + b"\x00" * 10 if encrypt else b"",
        wal_mode=False,
    )
    return StorageEngine(config)


def runtime_id_from_config(cfg: RuntimeConfig) -> bytes:
    """Derive runtime_id the same way Runtime does."""
    return sha256(cfg.deployment_id + cfg.instance_id).data


def persist_runtime_state(
    engine: StorageEngine,
    rt: Runtime,
    cfg: RuntimeConfig,
) -> None:
    """Persist the full runtime state to the storage engine."""
    rid = runtime_id_from_config(cfg)
    meta = RuntimeMeta(
        runtime_id=rid,
        deployment_id=cfg.deployment_id,
        instance_id=cfg.instance_id,
        agent_counter=rt._agent_counter,
        schema_version=SCHEMA_VERSION,
        created_at=int(time.time()),
    )
    engine.save_runtime_meta(meta)

    # Persist agents
    for record in rt._agents.values():
        engine.save_agent(record.agent_id.value, record.state.value, rid)

    # Persist channels
    for channel in rt._channels.values():
        engine.save_channel(channel, rt.global_state)

    # Save Sg cache
    if rt.global_state is not None:
        engine.save_sg_cache(rt.global_state)


# ---------------------------------------------------------------------------
# Runtime + StorageEngine: bind agents, persist, recover, verify restored
# ---------------------------------------------------------------------------

class TestAgentPersistenceRecovery:

    def test_bind_persist_recover_agents(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "bound", rid)
        engine.save_agent(b.value, "bound", rid)

        result = engine.recover()
        assert result is not None
        assert len(result.agents) == 2
        agent_ids = {ag.agent_id for ag in result.agents}
        assert a.value in agent_ids
        assert b.value in agent_ids
        for ag in result.agents:
            assert ag.state == "bound"

        engine.close()


# ---------------------------------------------------------------------------
# Runtime + StorageEngine: establish channel, persist, recover, verify state
# ---------------------------------------------------------------------------

class TestChannelPersistenceRecovery:

    def test_establish_persist_recover_channel(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "active", rid)
        engine.save_agent(b.value, "active", rid)

        channel = rt._channels[ch_id.value]
        engine.save_channel(channel, rt.global_state)

        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 1

        ch_row = result.channels[0]
        assert ch_row.channel_id == ch_id.value
        assert ch_row.local_state == channel.state.local_state.data
        assert ch_row.step == 0
        assert ch_row.depth == DEFAULT_FRAME_DEPTH
        assert ch_row.status == "active"

        engine.close()


# ---------------------------------------------------------------------------
# Runtime + StorageEngine: send, persist advancement, recover, verify
# ---------------------------------------------------------------------------

class TestAdvancementPersistenceRecovery:

    def test_send_advance_persist_recover(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        # Send a message to advance the ratchet
        rt.send(a, ch_id, b"hello")

        # Now persist the advanced state
        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "active", rid)
        engine.save_agent(b.value, "active", rid)

        channel = rt._channels[ch_id.value]
        engine.save_channel(channel, rt.global_state)

        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 1

        ch_row = result.channels[0]
        # After one send, channel step should be 1
        assert ch_row.step == 1
        # Local state should match the advanced state
        assert ch_row.local_state == channel.state.local_state.data

        engine.close()


# ---------------------------------------------------------------------------
# Multi-channel persistence: 3 channels, persist all, recover, verify Sg
# ---------------------------------------------------------------------------

class TestMultiChannelPersistence:

    def test_three_channels_persist_recover_sg(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)

        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)
        ch_bc = rt.establish_channel(b, c)

        original_sg = rt.global_state
        assert original_sg is not None

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        for aid_val, state_str in [
            (a.value, "active"), (b.value, "active"), (c.value, "active"),
        ]:
            engine.save_agent(aid_val, state_str, rid)

        for ch_id in [ch_ab, ch_ac, ch_bc]:
            channel = rt._channels[ch_id.value]
            engine.save_channel(channel, rt.global_state)

        engine.save_sg_cache(original_sg)

        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 3
        assert result.sg is not None

        # Sg should be recomputed from the channel states and match the original
        assert result.sg.value.data == original_sg.value.data

        engine.close()


# ---------------------------------------------------------------------------
# Channel close persistence: close, persist, recover, verify not active
# ---------------------------------------------------------------------------

class TestChannelClosePersistence:

    def test_close_channel_persist_recover(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)

        # Persist both channels first
        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        for aid_val in [a.value, b.value, c.value]:
            engine.save_agent(aid_val, "active", rid)

        for ch_id in [ch_ab, ch_ac]:
            channel = rt._channels[ch_id.value]
            engine.save_channel(channel, rt.global_state)

        # Now close ch_ab and persist the closure
        engine.close_channel(ch_ab.value, rt.global_state)

        result = engine.recover()
        assert result is not None
        # load_channels only returns active and quarantined, not closed
        active_ids = {ch.channel_id for ch in result.channels}
        assert ch_ab.value not in active_ids
        assert ch_ac.value in active_ids
        assert len(result.channels) == 1

        engine.close()


# ---------------------------------------------------------------------------
# Quarantine persistence: quarantine channel, persist, recover, verify
# ---------------------------------------------------------------------------

class TestQuarantinePersistence:

    def test_quarantine_channel_persist_recover(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "active", rid)
        engine.save_agent(b.value, "active", rid)

        channel = rt._channels[ch_id.value]
        engine.save_channel(channel, rt.global_state)

        # Quarantine the channel in storage
        engine.quarantine_channel(ch_id.value)

        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 1
        assert result.channels[0].status == "quarantined"

        engine.close()


# ---------------------------------------------------------------------------
# Encryption integration: full workflow with encrypt_at_rest=True
# ---------------------------------------------------------------------------

class TestEncryptionIntegration:

    def test_encrypt_at_rest_roundtrip(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage(encrypt=True)

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        # Send a few messages to advance state
        rt.send(a, ch_id, b"message one")
        rt.send(b, ch_id, b"message two")

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "active", rid)
        engine.save_agent(b.value, "active", rid)

        channel = rt._channels[ch_id.value]
        engine.save_channel(channel, rt.global_state)
        engine.save_sg_cache(rt.global_state)

        # Recover and verify decrypted state matches
        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 1

        ch_row = result.channels[0]
        assert ch_row.local_state == channel.state.local_state.data
        assert ch_row.step == 2
        assert result.sg is not None
        assert result.sg.value.data == rt.global_state.value.data

        engine.close()


# ---------------------------------------------------------------------------
# Recovery with Sg cache mismatch: save wrong Sg, recover, verify warning
# ---------------------------------------------------------------------------

class TestSgCacheMismatchRecovery:

    def test_sg_cache_mismatch_produces_warning(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)
        engine.save_agent(a.value, "active", rid)
        engine.save_agent(b.value, "active", rid)

        channel = rt._channels[ch_id.value]
        engine.save_channel(channel, rt.global_state)

        # Save a deliberately wrong Sg to the cache
        wrong_sg = GlobalState(value=StateValue(b"\xff" * 32))
        engine.save_sg_cache(wrong_sg)

        result = engine.recover()
        assert result is not None
        # Should have a warning about Sg mismatch
        mismatch_warnings = [
            w for w in result.warnings if "Cached Sg mismatch" in w
        ]
        assert len(mismatch_warnings) == 1

        # Recovery should use recomputed value, not the cached one
        assert result.sg is not None
        assert result.sg.value.data != wrong_sg.value.data
        assert result.sg.value.data == rt.global_state.value.data

        engine.close()


# ---------------------------------------------------------------------------
# Agent counter persistence: bind multiple agents, persist, verify monotonic
# ---------------------------------------------------------------------------

class TestAgentCounterPersistence:

    def test_agent_counter_monotonic(self):
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)
        engine = make_storage()

        # Bind 5 agents — counter should advance to 5
        agents = [rt.bind_agent(noop_agent) for _ in range(5)]
        assert rt._agent_counter == 5

        rid = runtime_id_from_config(cfg)
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=rt._agent_counter,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)

        # Verify persisted counter
        loaded_meta = engine.load_runtime_meta()
        assert loaded_meta is not None
        assert loaded_meta.agent_counter == 5

        # Increment counter via engine and verify monotonicity
        new_val = engine.increment_agent_counter(rid)
        assert new_val == 6

        new_val_2 = engine.increment_agent_counter(rid)
        assert new_val_2 == 7

        # Values are strictly increasing
        assert new_val < new_val_2

        engine.close()
