"""End-to-end tests for Storage Engine — full lifecycle simulations.

Uses the real Runtime and StorageEngine classes to exercise complete
persist-recover-verify workflows across the storage boundary.
"""

import sqlite3
import time

import pytest

from mfp.core.primitives import sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import (
    AgentState,
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
    RuntimeMeta,
    StorageConfig,
    StorageEngine,
)
from mfp.storage.schema import SCHEMA_VERSION, get_schema_version


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
        deployment_id=b"e2e-deploy-test!" + b"\x00" * 16,
        instance_id=b"e2e-instance-01!" + b"\x00" * 16,
    )


def make_runtime(config: RuntimeConfig | None = None) -> Runtime:
    """Create a fresh Runtime instance."""
    return Runtime(config or make_deterministic_config())


def runtime_id_from_config(cfg: RuntimeConfig) -> bytes:
    """Derive runtime_id the same way Runtime does."""
    return sha256(cfg.deployment_id + cfg.instance_id).data


def make_shared_db() -> sqlite3.Connection:
    """Create a shared in-memory database connection."""
    return sqlite3.connect("file::memory:?cache=shared", uri=True)


def make_engine_on_conn(conn_uri: str, encrypt: bool = False) -> StorageEngine:
    """Create a StorageEngine pointing at a specific URI."""
    config = StorageConfig(
        db_path=conn_uri,
        encrypt_at_rest=encrypt,
        master_key=b"e2e-master-key-for-tests" + b"\x00" * 8 if encrypt else b"",
        wal_mode=False,
    )
    return StorageEngine(config)


def persist_full_state(
    engine: StorageEngine,
    rt: Runtime,
    cfg: RuntimeConfig,
) -> None:
    """Persist all runtime state to the engine.

    save_agent() atomically increments agent_counter on each call, so we
    initialise the counter to 0 and let the N save_agent calls bring it
    to the correct value N (== rt._agent_counter).
    """
    rid = runtime_id_from_config(cfg)
    num_agents = len(rt._agents)
    meta = RuntimeMeta(
        runtime_id=rid,
        deployment_id=cfg.deployment_id,
        instance_id=cfg.instance_id,
        agent_counter=rt._agent_counter - num_agents,
        schema_version=SCHEMA_VERSION,
        created_at=int(time.time()),
    )
    engine.save_runtime_meta(meta)

    for record in rt._agents.values():
        engine.save_agent(record.agent_id.value, record.state.value, rid)

    for channel in rt._channels.values():
        engine.save_channel(channel, rt.global_state)

    if rt.global_state is not None:
        engine.save_sg_cache(rt.global_state)


# ---------------------------------------------------------------------------
# Full lifecycle: create, bind, establish, send, persist, recover, verify
# ---------------------------------------------------------------------------

class TestFullLifecycle:

    def test_full_lifecycle_persist_and_recover(self):
        """Create Runtime, bind 3 agents, establish channels, send 5 messages,
        persist everything, create new StorageEngine from same db, recover,
        verify all state matches."""
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)

        inbox_b, handler_b = collecting_agent()
        inbox_c, handler_c = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        c = rt.bind_agent(handler_c)

        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)

        # Send 5 messages across both channels
        rt.send(a, ch_ab, b"msg-1")
        rt.send(b, ch_ab, b"msg-2")
        rt.send(a, ch_ab, b"msg-3")
        rt.send(a, ch_ac, b"msg-4")
        rt.send(c, ch_ac, b"msg-5")

        # Capture expected state before persisting
        expected_sg = rt.global_state
        ch_ab_state = rt._channels[ch_ab.value]
        ch_ac_state = rt._channels[ch_ac.value]
        expected_counter = rt._agent_counter

        # Persist to engine 1
        engine1 = make_engine_on_conn("")
        persist_full_state(engine1, rt, cfg)

        # Recover from engine 1 (same in-memory db)
        result = engine1.recover()
        assert result is not None

        # Verify meta
        assert result.meta.agent_counter == expected_counter
        assert result.meta.runtime_id == runtime_id_from_config(cfg)

        # Verify agents — 3 agents, all active
        assert len(result.agents) == 3
        agent_ids_recovered = {ag.agent_id for ag in result.agents}
        assert a.value in agent_ids_recovered
        assert b.value in agent_ids_recovered
        assert c.value in agent_ids_recovered
        for ag in result.agents:
            assert ag.state == "active"

        # Verify channels
        assert len(result.channels) == 2
        ch_ids_recovered = {ch.channel_id for ch in result.channels}
        assert ch_ab.value in ch_ids_recovered
        assert ch_ac.value in ch_ids_recovered

        # Verify channel steps
        ch_ab_row = next(ch for ch in result.channels if ch.channel_id == ch_ab.value)
        ch_ac_row = next(ch for ch in result.channels if ch.channel_id == ch_ac.value)
        assert ch_ab_row.step == ch_ab_state.state.step
        assert ch_ac_row.step == ch_ac_state.state.step
        assert ch_ab_row.local_state == ch_ab_state.state.local_state.data
        assert ch_ac_row.local_state == ch_ac_state.state.local_state.data

        # Verify Sg
        assert result.sg is not None
        assert result.sg.value.data == expected_sg.value.data

        # No warnings expected
        assert len(result.warnings) == 0

        engine1.close()


# ---------------------------------------------------------------------------
# Crash simulation: setup, discard runtime, recover, verify
# ---------------------------------------------------------------------------

class TestCrashSimulation:

    def test_crash_recovery_consistency(self):
        """Setup full state, 'crash' (discard Runtime), recover from storage,
        rebuild state manually, verify consistency."""
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)

        # Send several messages
        for i in range(4):
            rt.send(a, ch_id, f"pre-crash-{i}".encode())

        # Capture state snapshots before "crash"
        pre_crash_sg = rt.global_state
        pre_crash_channel = rt._channels[ch_id.value]
        pre_crash_step = pre_crash_channel.state.step
        pre_crash_local = pre_crash_channel.state.local_state.data
        pre_crash_counter = rt._agent_counter

        # Persist to storage
        engine = make_engine_on_conn("")
        persist_full_state(engine, rt, cfg)

        # "CRASH" — discard the runtime
        del rt

        # Recover from storage
        result = engine.recover()
        assert result is not None

        # Verify meta integrity
        assert result.meta.agent_counter == pre_crash_counter
        rid = runtime_id_from_config(cfg)
        expected_rid = sha256(cfg.deployment_id + cfg.instance_id).data
        assert result.meta.runtime_id == expected_rid

        # Verify channel state preserved
        assert len(result.channels) == 1
        ch_row = result.channels[0]
        assert ch_row.step == pre_crash_step
        assert ch_row.local_state == pre_crash_local
        assert ch_row.status == "active"

        # Verify agents
        assert len(result.agents) == 2
        agent_states = {ag.agent_id: ag.state for ag in result.agents}
        assert agent_states[a.value] == "active"
        assert agent_states[b.value] == "active"

        # Verify Sg recomputed correctly
        assert result.sg is not None
        assert result.sg.value.data == pre_crash_sg.value.data

        # Verify no warnings
        assert len(result.warnings) == 0

        engine.close()


# ---------------------------------------------------------------------------
# Encryption at rest: full lifecycle, verify raw db has encrypted bytes
# ---------------------------------------------------------------------------

class TestEncryptionAtRest:

    def test_encrypted_columns_differ_from_plaintext(self):
        """Full lifecycle with encryption enabled. Verify that the raw db
        stores different bytes than the plaintext local_state."""
        cfg = make_deterministic_config()
        rt = make_runtime(cfg)

        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch_id = rt.establish_channel(a, b)
        rt.send(a, ch_id, b"encrypted-payload")

        channel = rt._channels[ch_id.value]
        plaintext_local_state = channel.state.local_state.data

        # Persist with encryption
        engine = make_engine_on_conn("", encrypt=True)
        persist_full_state(engine, rt, cfg)

        # Read raw bytes from the database, bypassing decryption
        cursor = engine._conn.execute(
            "SELECT local_state FROM channels WHERE channel_id = ?",
            (ch_id.value,),
        )
        raw_row = cursor.fetchone()
        assert raw_row is not None
        raw_local_state = raw_row[0]

        # Raw bytes should differ from plaintext (encrypted)
        assert raw_local_state != plaintext_local_state
        # Encrypted data is longer: nonce (12) + ciphertext (32) + tag (16) = 60
        assert len(raw_local_state) > len(plaintext_local_state)

        # But recovery should decrypt back to the original
        result = engine.recover()
        assert result is not None
        assert len(result.channels) == 1
        assert result.channels[0].local_state == plaintext_local_state

        engine.close()


# ---------------------------------------------------------------------------
# Schema version check: create schema, verify version=1
# ---------------------------------------------------------------------------

class TestSchemaVersion:

    def test_schema_version_is_one(self):
        """Create schema, verify version = 1."""
        engine = make_engine_on_conn("")
        cfg = make_deterministic_config()
        rid = runtime_id_from_config(cfg)

        # Save meta to populate runtime_meta table
        meta = RuntimeMeta(
            runtime_id=rid,
            deployment_id=cfg.deployment_id,
            instance_id=cfg.instance_id,
            agent_counter=0,
            schema_version=SCHEMA_VERSION,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)

        version = get_schema_version(engine._conn)
        assert version == 1

        engine.close()


# ---------------------------------------------------------------------------
# Empty recovery: fresh engine, recover returns None
# ---------------------------------------------------------------------------

class TestEmptyRecovery:

    def test_fresh_engine_recovery_returns_none(self):
        """A fresh engine with no data should return None on recover."""
        engine = make_engine_on_conn("")
        result = engine.recover()
        assert result is None
        engine.close()
