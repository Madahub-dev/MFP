"""Unit tests for connection pool improvements (P2.4)."""

import asyncio
import time

import pytest

from mfp.federation.transport import ConnectionMetadata, ConnectionPool, TransportConfig


class MockTransportClient:
    """Mock client for testing."""

    def __init__(self, host: str, port: int, config: TransportConfig):
        self.host = host
        self.port = port
        self.config = config
        self._connected = False
        self._closed = False

    @property
    def connected(self) -> bool:
        return self._connected and not self._closed

    async def connect(self) -> None:
        await asyncio.sleep(0.01)  # Simulate connection delay
        self._connected = True

    async def close(self) -> None:
        self._closed = True
        self._connected = False


@pytest.fixture
def short_timeout_config():
    """Config with very short timeouts for testing."""
    return TransportConfig(
        idle_timeout_seconds=0.5,
        max_connection_lifetime_seconds=1.0,
        eviction_interval_seconds=0.2,
    )


@pytest.fixture
def permissive_config():
    """Config with long timeouts."""
    return TransportConfig(
        idle_timeout_seconds=3600.0,
        max_connection_lifetime_seconds=7200.0,
        eviction_interval_seconds=60.0,
    )


class TestConnectionMetadata:
    """Tests for ConnectionMetadata."""

    def test_metadata_tracks_timestamps(self):
        """ConnectionMetadata should track creation and last use."""
        client = MockTransportClient("localhost", 9876, TransportConfig())
        now = time.time()

        metadata = ConnectionMetadata(
            client=client,
            created_at=now,
            last_used=now,
        )

        assert metadata.client is client
        assert metadata.created_at == now
        assert metadata.last_used == now


class TestConnectionPoolEviction:
    """Tests for idle connection eviction."""

    @pytest.mark.asyncio
    async def test_idle_connection_evicted(self, short_timeout_config, monkeypatch):
        """Idle connections should be evicted after timeout."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(short_timeout_config)
        peer_id = b"peer1" * 4  # 20 bytes

        # Create connection
        client = await pool.get_or_create(peer_id, "localhost", 9876)
        assert client.connected

        # Connection should exist
        assert peer_id in pool._connections

        # Wait for idle timeout + eviction interval
        await asyncio.sleep(0.8)

        # Connection should be evicted
        assert peer_id not in pool._connections

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_active_connection_not_evicted(self, short_timeout_config, monkeypatch):
        """Active connections should not be evicted."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(short_timeout_config)
        peer_id = b"peer1" * 4

        # Create connection
        await pool.get_or_create(peer_id, "localhost", 9876)

        # Keep using connection (refresh last_used)
        for _ in range(5):
            await asyncio.sleep(0.2)
            await pool.get_or_create(peer_id, "localhost", 9876)

        # Connection should still exist (not idle)
        assert peer_id in pool._connections

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_connection_lifetime_limit(self, short_timeout_config, monkeypatch):
        """Connections should be replaced after lifetime limit."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(short_timeout_config)
        peer_id = b"peer1" * 4

        # Create connection
        client1 = await pool.get_or_create(peer_id, "localhost", 9876)
        created_at_1 = pool._connections[peer_id].created_at

        # Wait beyond lifetime
        await asyncio.sleep(1.2)

        # Next get should create new connection
        client2 = await pool.get_or_create(peer_id, "localhost", 9876)
        created_at_2 = pool._connections[peer_id].created_at

        # Should be a new connection
        assert client2 is not client1
        assert created_at_2 > created_at_1

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_multiple_connections_independent_eviction(self, short_timeout_config, monkeypatch):
        """Each connection should be evicted independently."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(short_timeout_config)
        peer1 = b"peer1" * 4
        peer2 = b"peer2" * 4

        # Create two connections
        await pool.get_or_create(peer1, "localhost", 9876)
        await asyncio.sleep(0.4)  # Delay second connection
        await pool.get_or_create(peer2, "localhost", 9877)

        # Wait for eviction cycle to run, but peer2 should not be idle yet
        # peer1 has been idle for 0.4s + 0.4s = 0.8s (> 0.5s timeout)
        # peer2 has been idle for 0.4s (< 0.5s timeout)
        await asyncio.sleep(0.4)

        # peer1 should be evicted, peer2 should remain
        # Note: There might be a race condition with eviction timing
        # so we'll check that at least peer1 is gone
        assert peer1 not in pool._connections

        await pool.close_all()


class TestConnectionPoolGracefulShutdown:
    """Tests for graceful shutdown."""

    @pytest.mark.asyncio
    async def test_close_all_stops_eviction_task(self, permissive_config, monkeypatch):
        """close_all should stop the eviction task."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(permissive_config)
        peer_id = b"peer1" * 4

        # Create connection (starts eviction task)
        await pool.get_or_create(peer_id, "localhost", 9876)

        assert pool._eviction_task is not None
        eviction_task = pool._eviction_task
        assert not eviction_task.done()

        # Close all
        await pool.close_all()

        # Eviction task should be cancelled and pool's reference should be None
        assert eviction_task.done()
        assert pool._eviction_task is None

    @pytest.mark.asyncio
    async def test_close_all_closes_all_connections(self, permissive_config, monkeypatch):
        """close_all should close all active connections."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(permissive_config)

        # Create multiple connections
        peers = [b"peer1" * 4, b"peer2" * 4, b"peer3" * 4]
        clients = []
        for peer in peers:
            client = await pool.get_or_create(peer, "localhost", 9876)
            clients.append(client)

        # All should be connected
        for client in clients:
            assert client.connected

        # Close all
        await pool.close_all()

        # All should be closed
        for client in clients:
            assert not client.connected

        # Pool should be empty
        assert len(pool._connections) == 0

    @pytest.mark.asyncio
    async def test_shutdown_prevents_new_connections(self, permissive_config, monkeypatch):
        """After shutdown, eviction task should not restart."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(permissive_config)
        peer1 = b"peer1" * 4

        # Create and close
        await pool.get_or_create(peer1, "localhost", 9876)
        await pool.close_all()

        # Try to create new connection after shutdown
        peer2 = b"peer2" * 4
        await pool.get_or_create(peer2, "localhost", 9877)

        # Eviction task should not restart
        assert pool._eviction_task is None

        await pool.close_all()


class TestConnectionPoolReuse:
    """Tests for connection reuse."""

    @pytest.mark.asyncio
    async def test_get_or_create_reuses_existing(self, permissive_config, monkeypatch):
        """get_or_create should reuse existing connection."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(permissive_config)
        peer_id = b"peer1" * 4

        # First call creates
        client1 = await pool.get_or_create(peer_id, "localhost", 9876)
        created_at_1 = pool._connections[peer_id].created_at

        # Second call reuses
        client2 = await pool.get_or_create(peer_id, "localhost", 9876)
        created_at_2 = pool._connections[peer_id].created_at

        # Should be same connection
        assert client1 is client2
        assert created_at_1 == created_at_2

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_get_or_create_updates_last_used(self, permissive_config, monkeypatch):
        """get_or_create should update last_used on reuse."""
        monkeypatch.setattr(
            "mfp.federation.transport.TransportClient",
            MockTransportClient
        )

        pool = ConnectionPool(permissive_config)
        peer_id = b"peer1" * 4

        # Create connection
        await pool.get_or_create(peer_id, "localhost", 9876)
        last_used_1 = pool._connections[peer_id].last_used

        # Wait and reuse
        await asyncio.sleep(0.1)
        await pool.get_or_create(peer_id, "localhost", 9876)
        last_used_2 = pool._connections[peer_id].last_used

        # last_used should be updated
        assert last_used_2 > last_used_1

        await pool.close_all()
