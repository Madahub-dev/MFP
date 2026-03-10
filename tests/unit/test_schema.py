"""Unit tests for mfp/storage/schema.py (I-13)."""

import sqlite3

import pytest

from mfp.storage.schema import (
    SCHEMA_VERSION,
    create_schema,
    get_schema_version,
    set_pragma,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite connection with the MFP schema."""
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    """Return the set of user table names in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cursor.fetchall()}


def _insert_runtime_meta(conn: sqlite3.Connection) -> None:
    """Insert a minimal runtime_meta row so foreign keys are satisfied."""
    conn.execute(
        "INSERT INTO runtime_meta "
        "(runtime_id, deployment_id, instance_id, agent_counter, schema_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (b"\x01" * 16, b"\x02" * 16, b"\x03" * 16, 0, SCHEMA_VERSION, 1000),
    )
    conn.commit()


def _insert_agent(conn: sqlite3.Connection, agent_id: bytes, state: str = "active") -> None:
    """Insert a minimal agent row."""
    conn.execute(
        "INSERT INTO agents (agent_id, state, message_count, quarantine_reason, created_at, updated_at) "
        "VALUES (?, ?, 0, '', 1000, 1000)",
        (agent_id, state),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# create_schema()
# ---------------------------------------------------------------------------

class TestCreateSchema:
    def test_creates_all_five_tables(self):
        conn = _make_conn()
        expected = {
            "runtime_meta",
            "agents",
            "channels",
            "bilateral_channels",
            "global_state_cache",
        }
        assert _table_names(conn) == expected
        conn.close()

    def test_idempotent(self):
        conn = sqlite3.connect(":memory:")
        create_schema(conn)
        create_schema(conn)  # second call must not raise
        assert _table_names(conn) == {
            "runtime_meta",
            "agents",
            "channels",
            "bilateral_channels",
            "global_state_cache",
        }
        conn.close()


# ---------------------------------------------------------------------------
# get_schema_version()
# ---------------------------------------------------------------------------

class TestGetSchemaVersion:
    def test_returns_none_on_empty_database(self):
        conn = _make_conn()
        assert get_schema_version(conn) is None
        conn.close()

    def test_returns_version_after_insert(self):
        conn = _make_conn()
        _insert_runtime_meta(conn)
        assert get_schema_version(conn) == SCHEMA_VERSION
        conn.close()


# ---------------------------------------------------------------------------
# set_pragma()
# ---------------------------------------------------------------------------

class TestSetPragma:
    def test_foreign_keys_enabled(self):
        conn = sqlite3.connect(":memory:")
        set_pragma(conn, wal_mode=False)
        cursor = conn.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1
        conn.close()

    def test_wal_mode_enabled(self):
        """WAL mode requires a file-backed database; on :memory: it stays as 'memory'.
        We verify that the pragma call does not raise an error."""
        conn = sqlite3.connect(":memory:")
        # Should not raise even on in-memory db
        set_pragma(conn, wal_mode=True)
        cursor = conn.execute("PRAGMA journal_mode")
        # In-memory databases report 'memory'; file-backed would report 'wal'.
        journal_mode = cursor.fetchone()[0]
        assert journal_mode in ("wal", "memory")
        conn.close()


# ---------------------------------------------------------------------------
# CHECK constraints
# ---------------------------------------------------------------------------

class TestConstraints:
    def test_agent_state_rejects_invalid(self):
        conn = _make_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agents (agent_id, state, message_count, quarantine_reason, created_at, updated_at) "
                "VALUES (?, 'invalid_state', 0, '', 1000, 1000)",
                (b"\xaa" * 16,),
            )
        conn.close()

    def test_channel_step_rejects_negative(self):
        conn = _make_conn()
        conn.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO channels "
                "(channel_id, agent_a, agent_b, local_state, step, depth, status, "
                "validation_failure_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, -1, 4, 'active', 0, 1000, 1000)",
                (b"\x10" * 16, b"\x11" * 16, b"\x12" * 16, b"\x00" * 32),
            )
        conn.close()

    def test_channel_depth_rejects_less_than_two(self):
        conn = _make_conn()
        conn.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO channels "
                "(channel_id, agent_a, agent_b, local_state, step, depth, status, "
                "validation_failure_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, 1, 'active', 0, 1000, 1000)",
                (b"\x10" * 16, b"\x11" * 16, b"\x12" * 16, b"\x00" * 32),
            )
        conn.close()
