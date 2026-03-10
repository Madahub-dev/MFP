"""MFP Storage Schema — SQLite table definitions, versioning, migration.

All tables, indexes, and constraints for persisting MFP runtime state.

Maps to: impl/I-13_schema.md
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS runtime_meta (
    runtime_id     BLOB    PRIMARY KEY,
    deployment_id  BLOB    NOT NULL,
    instance_id    BLOB    NOT NULL,
    agent_counter  INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id          BLOB    PRIMARY KEY,
    state             TEXT    NOT NULL CHECK(state IN ('bound','active','quarantined','terminated')),
    message_count     INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT    DEFAULT '',
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id               BLOB    PRIMARY KEY,
    agent_a                  BLOB    NOT NULL REFERENCES agents(agent_id),
    agent_b                  BLOB    NOT NULL REFERENCES agents(agent_id),
    local_state              BLOB    NOT NULL,
    step                     INTEGER NOT NULL DEFAULT 0 CHECK(step >= 0),
    depth                    INTEGER NOT NULL DEFAULT 4 CHECK(depth >= 2),
    status                   TEXT    NOT NULL CHECK(status IN ('active','quarantined','closed')),
    validation_failure_count INTEGER NOT NULL DEFAULT 0,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bilateral_channels (
    bilateral_id     BLOB    PRIMARY KEY,
    runtime_id_local BLOB    NOT NULL,
    runtime_id_peer  BLOB    NOT NULL,
    ratchet_state    BLOB    NOT NULL,
    shared_prng_seed BLOB    NOT NULL,
    step             INTEGER NOT NULL DEFAULT 0 CHECK(step >= 0),
    status           TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active','recovery','suspended')),
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS global_state_cache (
    runtime_id BLOB    PRIMARY KEY REFERENCES runtime_meta(runtime_id),
    sg_value   BLOB    NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

_INDEXES_SQL = """\
CREATE INDEX IF NOT EXISTS idx_channels_agent_a ON channels(agent_a);
CREATE INDEX IF NOT EXISTS idx_channels_agent_b ON channels(agent_b);
CREATE INDEX IF NOT EXISTS idx_channels_status  ON channels(status);
CREATE INDEX IF NOT EXISTS idx_agents_state     ON agents(state);
CREATE INDEX IF NOT EXISTS idx_bilateral_peer   ON bilateral_channels(runtime_id_peer);
"""


# ---------------------------------------------------------------------------
# Schema Management
# ---------------------------------------------------------------------------

def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and constraints.

    Uses IF NOT EXISTS — safe to call on an existing database.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_TABLES_SQL)
    conn.executescript(_INDEXES_SQL)


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Read current schema version from runtime_meta.

    Returns None if runtime_meta is empty (fresh database).
    """
    cursor = conn.execute(
        "SELECT schema_version FROM runtime_meta LIMIT 1"
    )
    row = cursor.fetchone()
    return row[0] if row else None


def set_pragma(conn: sqlite3.Connection, wal_mode: bool = True) -> None:
    """Set connection pragmas for performance and safety."""
    conn.execute("PRAGMA foreign_keys = ON")
    if wal_mode:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

# Future migrations registered here: version -> callable
_MIGRATIONS: dict[int, callable] = {}


def migrate(conn: sqlite3.Connection, current: int, target: int) -> None:
    """Apply migrations from current to target version.

    Each migration runs in its own transaction.
    """
    for version in range(current + 1, target + 1):
        migration_fn = _MIGRATIONS.get(version)
        if migration_fn is None:
            raise ValueError(f"No migration for version {version}")
        migration_fn(conn)
        conn.execute(
            "UPDATE runtime_meta SET schema_version = ?",
            (version,),
        )
        conn.commit()
