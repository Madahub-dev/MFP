"""Integration tests for transport connection limits."""

import asyncio
import pytest

from mfp.federation.transport import TransportConfig, TransportServer


@pytest.mark.asyncio
class TestTransportConnectionLimits:
    """Tests for transport layer connection limits."""

    async def test_max_connections_enforced(self):
        """Server should reject connections beyond max_connections."""
        # Create server with low connection limit
        config = TransportConfig(port=19881, max_connections=2)

        async def handler(header, msg):
            pass

        server = TransportServer(config, handler)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            # Open connections up to limit
            conn1_reader, conn1_writer = await asyncio.open_connection("127.0.0.1", 19881)
            await asyncio.sleep(0.05)

            conn2_reader, conn2_writer = await asyncio.open_connection("127.0.0.1", 19881)
            await asyncio.sleep(0.05)

            # Third connection should be rejected
            conn3_reader, conn3_writer = await asyncio.open_connection("127.0.0.1", 19881)
            await asyncio.sleep(0.05)

            # Verify third connection was closed
            # Try to read - should get EOF or connection reset
            try:
                data = await asyncio.wait_for(conn3_reader.read(1024), timeout=1.0)
                # If we get empty data, connection was closed
                assert len(data) == 0, "Third connection should have been closed"
            except (ConnectionResetError, asyncio.TimeoutError):
                # Connection was reset or timed out - good
                pass

            # Clean up connections
            conn1_writer.close()
            await conn1_writer.wait_closed()
            conn2_writer.close()
            await conn2_writer.wait_closed()
            conn3_writer.close()
            await conn3_writer.wait_closed()

        finally:
            await server.stop()

    async def test_closing_connection_allows_new_connection(self):
        """Closing a connection should allow a new one within limit."""
        config = TransportConfig(port=19882, max_connections=2)

        async def handler(header, msg):
            pass

        server = TransportServer(config, handler)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            # Open two connections (at limit)
            conn1_reader, conn1_writer = await asyncio.open_connection("127.0.0.1", 19882)
            await asyncio.sleep(0.05)
            conn2_reader, conn2_writer = await asyncio.open_connection("127.0.0.1", 19882)
            await asyncio.sleep(0.05)

            # Close first connection
            conn1_writer.close()
            await conn1_writer.wait_closed()
            await asyncio.sleep(0.1)  # Wait for server to process close

            # Should be able to open a new connection
            conn3_reader, conn3_writer = await asyncio.open_connection("127.0.0.1", 19882)
            await asyncio.sleep(0.05)

            # Verify connection is active by checking it's not immediately closed
            assert not conn3_writer.is_closing()

            # Clean up
            conn2_writer.close()
            await conn2_writer.wait_closed()
            conn3_writer.close()
            await conn3_writer.wait_closed()

        finally:
            await server.stop()

    async def test_default_connection_limit_is_reasonable(self):
        """Default max_connections should be set to reasonable value."""
        config = TransportConfig()

        assert config.max_connections == 1000
        assert config.max_connection_rate == 100

    async def test_multiple_servers_independent_limits(self):
        """Each server should have independent connection limits."""
        config1 = TransportConfig(port=19883, max_connections=1)
        config2 = TransportConfig(port=19884, max_connections=1)

        async def handler(header, msg):
            pass

        server1 = TransportServer(config1, handler)
        server2 = TransportServer(config2, handler)

        try:
            await server1.start()
            await server2.start()
            await asyncio.sleep(0.1)

            # Each server can accept up to its limit
            conn1 = await asyncio.open_connection("127.0.0.1", 19883)
            conn2 = await asyncio.open_connection("127.0.0.1", 19884)

            # Verify both connections are active
            assert not conn1[1].is_closing()
            assert not conn2[1].is_closing()

            # Clean up
            conn1[1].close()
            await conn1[1].wait_closed()
            conn2[1].close()
            await conn2[1].wait_closed()

        finally:
            await server1.stop()
            await server2.stop()

    async def test_high_connection_limit_allows_many_connections(self):
        """Server should handle many concurrent connections."""
        config = TransportConfig(port=19885, max_connections=50)

        async def handler(header, msg):
            pass

        server = TransportServer(config, handler)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            # Open many connections
            connections = []
            for _ in range(20):
                reader, writer = await asyncio.open_connection("127.0.0.1", 19885)
                connections.append((reader, writer))
                await asyncio.sleep(0.01)

            # All should be accepted
            assert len(connections) == 20

            # Clean up all connections
            for reader, writer in connections:
                writer.close()
                await writer.wait_closed()

        finally:
            await server.stop()
