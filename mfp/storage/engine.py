"""MFP Storage Engine — persistence layer between Runtime and SQLite.

Translates Runtime state mutations into atomic database transactions.
Handles schema creation, recovery, and optional encryption at rest.

Maps to: impl/I-14_persistence.md
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from mfp.core.primitives import sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import (
    AgentId,
    AgentState,
    Channel,
    ChannelId,
    ChannelState,
    ChannelStatus,
    GlobalState,
    StateValue,
    DEFAULT_FRAME_DEPTH,
)

from mfp.storage.crypto import decrypt_cell, derive_storage_key, encrypt_cell
from mfp.storage.schema import (
    SCHEMA_VERSION,
    create_schema,
    get_schema_version,
    migrate,
    set_pragma,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StorageConfig:
    """Storage layer configuration."""
    db_path: str = ""              # Empty = in-memory (":memory:")
    encrypt_at_rest: bool = False
    master_key: bytes = b""        # Required if encrypt_at_rest is True
    wal_mode: bool = True


# ---------------------------------------------------------------------------
# Row Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeMeta:
    """Persisted runtime metadata."""
    runtime_id: bytes
    deployment_id: bytes
    instance_id: bytes
    agent_counter: int
    schema_version: int
    created_at: int


@dataclass(frozen=True)
class AgentRow:
    """Persisted agent record."""
    agent_id: bytes
    state: str
    message_count: int
    quarantine_reason: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ChannelRow:
    """Persisted channel record."""
    channel_id: bytes
    agent_a: bytes
    agent_b: bytes
    local_state: bytes
    step: int
    depth: int
    status: str
    validation_failure_count: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class BilateralRow:
    """Persisted bilateral channel record."""
    bilateral_id: bytes
    runtime_id_local: bytes
    runtime_id_peer: bytes
    ratchet_state: bytes
    shared_prng_seed: bytes
    step: int
    status: str
    created_at: int
    updated_at: int


@dataclass
class RecoveryResult:
    """State recovered from persistent storage."""
    meta: RuntimeMeta
    agents: list[AgentRow]
    channels: list[ChannelRow]
    bilateral_channels: list[BilateralRow]
    sg: GlobalState | None
    warnings: list[str]


# ---------------------------------------------------------------------------
# Storage Engine
# ---------------------------------------------------------------------------

class StorageEngine:
    """Persistence layer between Runtime and SQLite.

    Maps to: I-14 §7.
    """

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        db = config.db_path or ":memory:"
        self._conn = sqlite3.connect(db)
        set_pragma(self._conn, wal_mode=config.wal_mode)
        create_schema(self._conn)

        self._storage_key: StateValue | None = None
        self._runtime_id: bytes = b""

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _init_encryption(self, runtime_id: bytes) -> None:
        """Initialize encryption key from master key + runtime ID."""
        self._runtime_id = runtime_id
        if self._config.encrypt_at_rest and self._config.master_key:
            self._storage_key = derive_storage_key(
                self._config.master_key, runtime_id
            )

    def _encrypt(
        self, table: str, column: str, row_id: bytes, plaintext: bytes,
    ) -> bytes:
        """Encrypt a value if encryption is enabled, else passthrough."""
        if self._storage_key is None:
            return plaintext
        return encrypt_cell(
            self._storage_key,
            table.encode(),
            column.encode(),
            row_id,
            plaintext,
        )

    def _decrypt(
        self, table: str, column: str, row_id: bytes, stored: bytes,
    ) -> bytes:
        """Decrypt a value if encryption is enabled, else passthrough."""
        if self._storage_key is None:
            return stored
        result = decrypt_cell(
            self._storage_key,
            table.encode(),
            column.encode(),
            row_id,
            stored,
        )
        if result is None:
            raise ValueError(
                f"Decryption failed for {table}.{column} row {row_id.hex()[:8]}"
            )
        return result

    # ------------------------------------------------------------------
    # Runtime Meta
    # ------------------------------------------------------------------

    def save_runtime_meta(self, meta: RuntimeMeta) -> None:
        """Insert or update runtime metadata."""
        self._init_encryption(meta.runtime_id)
        self._conn.execute(
            """INSERT OR REPLACE INTO runtime_meta
               (runtime_id, deployment_id, instance_id, agent_counter,
                schema_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                meta.runtime_id,
                meta.deployment_id,
                meta.instance_id,
                meta.agent_counter,
                meta.schema_version,
                meta.created_at,
            ),
        )
        self._conn.commit()

    def load_runtime_meta(self) -> RuntimeMeta | None:
        """Load runtime metadata. Returns None if no metadata exists."""
        cursor = self._conn.execute(
            "SELECT runtime_id, deployment_id, instance_id, agent_counter, "
            "schema_version, created_at FROM runtime_meta LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            return None
        meta = RuntimeMeta(*row)
        self._init_encryption(meta.runtime_id)
        return meta

    def increment_agent_counter(self, runtime_id: bytes) -> int:
        """Atomically increment and return the new counter value."""
        self._conn.execute(
            "UPDATE runtime_meta SET agent_counter = agent_counter + 1 "
            "WHERE runtime_id = ?",
            (runtime_id,),
        )
        cursor = self._conn.execute(
            "SELECT agent_counter FROM runtime_meta WHERE runtime_id = ?",
            (runtime_id,),
        )
        row = cursor.fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    def save_agent(
        self,
        agent_id: bytes,
        state: str,
        runtime_id: bytes,
    ) -> None:
        """Save a new agent. Atomic with counter increment."""
        now = int(time.time())
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE runtime_meta SET agent_counter = agent_counter + 1 "
                "WHERE runtime_id = ?",
                (runtime_id,),
            )
            self._conn.execute(
                """INSERT INTO agents
                   (agent_id, state, message_count, quarantine_reason,
                    created_at, updated_at)
                   VALUES (?, ?, 0, '', ?, ?)""",
                (agent_id, state, now, now),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def update_agent_state(
        self,
        agent_id: bytes,
        state: str,
        reason: str = "",
    ) -> None:
        """Update an agent's state."""
        now = int(time.time())
        self._conn.execute(
            "UPDATE agents SET state = ?, quarantine_reason = ?, updated_at = ? "
            "WHERE agent_id = ?",
            (state, reason, now, agent_id),
        )
        self._conn.commit()

    def delete_agent(self, agent_id: bytes) -> None:
        """Remove an agent from the database."""
        self._conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        self._conn.commit()

    def load_agents(self) -> list[AgentRow]:
        """Load all non-terminated agents."""
        cursor = self._conn.execute(
            "SELECT agent_id, state, message_count, quarantine_reason, "
            "created_at, updated_at FROM agents "
            "WHERE state IN ('bound', 'active', 'quarantined')"
        )
        return [AgentRow(*row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    def save_channel(self, channel: Channel, sg: GlobalState | None) -> None:
        """Persist a newly established channel. Atomic with Sg cache update."""
        now = int(time.time())
        local_state_bytes = self._encrypt(
            "channels", "local_state",
            channel.channel_id.value,
            channel.state.local_state.data,
        )

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                """INSERT INTO channels
                   (channel_id, agent_a, agent_b, local_state, step, depth,
                    status, validation_failure_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    channel.channel_id.value,
                    channel.agent_a.value,
                    channel.agent_b.value,
                    local_state_bytes,
                    channel.state.step,
                    channel.depth,
                    channel.status.value,
                    now,
                    now,
                ),
            )
            # Update agent states to active if bound
            self._conn.execute(
                "UPDATE agents SET state = 'active', updated_at = ? "
                "WHERE agent_id IN (?, ?) AND state = 'bound'",
                (now, channel.agent_a.value, channel.agent_b.value),
            )
            if sg is not None:
                self._save_sg_cache_inner(sg)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def advance_channel(
        self,
        channel_id: bytes,
        new_local_state: StateValue,
        sg: GlobalState,
    ) -> None:
        """Persist channel state advancement. Atomic Sₗ + step + Sg."""
        now = int(time.time())
        local_state_bytes = self._encrypt(
            "channels", "local_state", channel_id, new_local_state.data,
        )

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE channels SET local_state = ?, step = step + 1, "
                "validation_failure_count = 0, updated_at = ? "
                "WHERE channel_id = ?",
                (local_state_bytes, now, channel_id),
            )
            self._save_sg_cache_inner(sg)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close_channel(
        self,
        channel_id: bytes,
        sg: GlobalState | None,
    ) -> None:
        """Persist channel closure. Zeros Sₗ, sets status to closed."""
        now = int(time.time())
        zeroed = b"\x00" * 32
        zeroed_encrypted = self._encrypt(
            "channels", "local_state", channel_id, zeroed,
        )

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE channels SET local_state = ?, status = 'closed', "
                "updated_at = ? WHERE channel_id = ?",
                (zeroed_encrypted, now, channel_id),
            )
            if sg is not None:
                self._save_sg_cache_inner(sg)
            else:
                self._conn.execute(
                    "DELETE FROM global_state_cache WHERE runtime_id = ?",
                    (self._runtime_id,),
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def quarantine_channel(self, channel_id: bytes) -> None:
        """Set channel status to quarantined."""
        now = int(time.time())
        self._conn.execute(
            "UPDATE channels SET status = 'quarantined', updated_at = ? "
            "WHERE channel_id = ?",
            (now, channel_id),
        )
        self._conn.commit()

    def restore_channel(self, channel_id: bytes) -> None:
        """Set channel status to active, reset failure count."""
        now = int(time.time())
        self._conn.execute(
            "UPDATE channels SET status = 'active', "
            "validation_failure_count = 0, updated_at = ? "
            "WHERE channel_id = ?",
            (now, channel_id),
        )
        self._conn.commit()

    def load_channels(self) -> list[ChannelRow]:
        """Load all active and quarantined channels."""
        cursor = self._conn.execute(
            "SELECT channel_id, agent_a, agent_b, local_state, step, depth, "
            "status, validation_failure_count, created_at, updated_at "
            "FROM channels WHERE status IN ('active', 'quarantined')"
        )
        rows = []
        for row in cursor.fetchall():
            ch_id = row[0]
            local_state = self._decrypt(
                "channels", "local_state", ch_id, row[3],
            )
            rows.append(ChannelRow(
                channel_id=ch_id,
                agent_a=row[1],
                agent_b=row[2],
                local_state=local_state,
                step=row[4],
                depth=row[5],
                status=row[6],
                validation_failure_count=row[7],
                created_at=row[8],
                updated_at=row[9],
            ))
        return rows

    # ------------------------------------------------------------------
    # Bilateral Channels (Phase 5 — schema present, methods ready)
    # ------------------------------------------------------------------

    def save_bilateral(self, row: BilateralRow) -> None:
        """Persist a bilateral channel."""
        ratchet = self._encrypt(
            "bilateral_channels", "ratchet_state",
            row.bilateral_id, row.ratchet_state,
        )
        prng = self._encrypt(
            "bilateral_channels", "shared_prng_seed",
            row.bilateral_id, row.shared_prng_seed,
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO bilateral_channels
               (bilateral_id, runtime_id_local, runtime_id_peer,
                ratchet_state, shared_prng_seed, step, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.bilateral_id,
                row.runtime_id_local,
                row.runtime_id_peer,
                ratchet,
                prng,
                row.step,
                row.status,
                row.created_at,
                row.updated_at,
            ),
        )
        self._conn.commit()

    def load_bilateral_channels(self) -> list[BilateralRow]:
        """Load all active and recovery bilateral channels."""
        cursor = self._conn.execute(
            "SELECT bilateral_id, runtime_id_local, runtime_id_peer, "
            "ratchet_state, shared_prng_seed, step, status, "
            "created_at, updated_at "
            "FROM bilateral_channels WHERE status IN ('active', 'recovery')"
        )
        rows = []
        for row in cursor.fetchall():
            bid = row[0]
            ratchet = self._decrypt(
                "bilateral_channels", "ratchet_state", bid, row[3],
            )
            prng = self._decrypt(
                "bilateral_channels", "shared_prng_seed", bid, row[4],
            )
            rows.append(BilateralRow(
                bilateral_id=bid,
                runtime_id_local=row[1],
                runtime_id_peer=row[2],
                ratchet_state=ratchet,
                shared_prng_seed=prng,
                step=row[5],
                status=row[6],
                created_at=row[7],
                updated_at=row[8],
            ))
        return rows

    # ------------------------------------------------------------------
    # Global State Cache
    # ------------------------------------------------------------------

    def _save_sg_cache_inner(self, sg: GlobalState) -> None:
        """Save Sg cache (call within an existing transaction)."""
        now = int(time.time())
        self._conn.execute(
            "INSERT OR REPLACE INTO global_state_cache "
            "(runtime_id, sg_value, updated_at) VALUES (?, ?, ?)",
            (self._runtime_id, sg.value.data, now),
        )

    def save_sg_cache(self, sg: GlobalState) -> None:
        """Save Sg cache as a standalone operation."""
        self._save_sg_cache_inner(sg)
        self._conn.commit()

    def load_sg_cache(self) -> GlobalState | None:
        """Load cached Sg. Returns None if not cached."""
        cursor = self._conn.execute(
            "SELECT sg_value FROM global_state_cache WHERE runtime_id = ?",
            (self._runtime_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return GlobalState(value=StateValue(row[0]))

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover(self) -> RecoveryResult | None:
        """Load and verify persistent state for crash recovery.

        Returns None if the database is empty (first startup).

        Maps to: I-14 §6.
        """
        meta = self.load_runtime_meta()
        if meta is None:
            return None

        # Verify runtime identity
        expected_id = sha256(meta.deployment_id + meta.instance_id)
        warnings: list[str] = []
        if meta.runtime_id != expected_id.data:
            warnings.append(
                "Runtime ID mismatch: stored does not match derived"
            )

        # Check schema version
        version = get_schema_version(self._conn)
        if version is not None and version < SCHEMA_VERSION:
            migrate(self._conn, version, SCHEMA_VERSION)

        # Load agents
        agents = self.load_agents()

        # Load channels and verify
        channels = self.load_channels()
        agent_ids = {a.agent_id for a in agents}
        for ch in channels:
            if ch.agent_a not in agent_ids:
                warnings.append(
                    f"Channel {ch.channel_id.hex()[:8]} references unknown agent_a"
                )
            if ch.agent_b not in agent_ids:
                warnings.append(
                    f"Channel {ch.channel_id.hex()[:8]} references unknown agent_b"
                )
            if len(ch.local_state) != 32:
                warnings.append(
                    f"Channel {ch.channel_id.hex()[:8]} has invalid local_state size"
                )

        # Load bilateral channels
        bilateral = self.load_bilateral_channels()

        # Recompute Sg
        sg: GlobalState | None = None
        if channels:
            try:
                sg = compose_ordered(
                    channel_states=[
                        (ChannelId(ch.channel_id), StateValue(ch.local_state))
                        for ch in channels
                    ],
                )
            except Exception as e:
                warnings.append(f"Failed to recompute Sg: {e}")

        # Verify against cache
        cached_sg = self.load_sg_cache()
        if cached_sg and sg and cached_sg.value.data != sg.value.data:
            warnings.append("Cached Sg mismatch — using recomputed value")

        return RecoveryResult(
            meta=meta,
            agents=agents,
            channels=channels,
            bilateral_channels=bilateral,
            sg=sg,
            warnings=warnings,
        )
