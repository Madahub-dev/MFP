"""HTTP server for health check and metrics endpoints.

Runs on a separate port from the main transport server for monitoring.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mfp.observability.logging import LogContext, get_logger

if TYPE_CHECKING:
    from mfp.observability.health import HealthChecker

logger = get_logger(__name__)


@dataclass(frozen=True)
class HealthServerConfig:
    """Configuration for health check HTTP server."""

    enabled: bool = True
    host: str = "127.0.0.1"  # Localhost only by default for security
    port: int = 9877
    allow_detailed_status: bool = True


class HealthHTTPServer:
    """Simple HTTP server for health checks.

    Provides endpoints:
    - GET /health/live - Liveness probe
    - GET /health/ready - Readiness probe
    - GET /health/startup - Startup probe
    - GET /health/status - Detailed status (optional)
    """

    def __init__(self, config: HealthServerConfig, health_checker: HealthChecker):
        self.config = config
        self.health_checker = health_checker
        self._server = None
        self._running = False

    async def start(self):
        """Start the HTTP server."""
        if not self.config.enabled:
            logger.info("Health server disabled, skipping start")
            return

        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.host,
            self.config.port,
        )

        self._running = True

        context = LogContext(
            correlation_id="health_server_start",
            runtime_id="",
            operation="health_server_start",
        )
        logger.info(
            f"Health server listening on {self.config.host}:{self.config.port}",
            context=context,
        )

    async def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._running = False

            context = LogContext(
                correlation_id="health_server_stop",
                runtime_id="",
                operation="health_server_stop",
            )
            logger.info("Health server stopped", context=context)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming HTTP request."""
        try:
            # Read HTTP request line
            request_line = await reader.readline()
            if not request_line:
                return

            # Parse request
            parts = request_line.decode().strip().split()
            if len(parts) < 2:
                await self._send_response(writer, 400, "Bad Request")
                return

            method, path = parts[0], parts[1]

            # Read headers (we don't need them, but must consume)
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break

            # Route request
            if method != "GET":
                await self._send_response(writer, 405, "Method Not Allowed")
                return

            if path == "/health/live":
                await self._handle_liveness(writer)
            elif path == "/health/ready":
                await self._handle_readiness(writer)
            elif path == "/health/startup":
                await self._handle_startup(writer)
            elif path == "/health/status" and self.config.allow_detailed_status:
                await self._handle_status(writer)
            else:
                await self._send_response(writer, 404, "Not Found")

        except Exception as e:
            logger.error(f"Health server error: {e}")
            await self._send_response(writer, 500, "Internal Server Error")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_liveness(self, writer: asyncio.StreamWriter):
        """Handle liveness probe."""
        result = self.health_checker.liveness()
        status_code = 200 if result.status.value == "healthy" else 503
        await self._send_json_response(writer, status_code, result.to_dict())

    async def _handle_readiness(self, writer: asyncio.StreamWriter):
        """Handle readiness probe."""
        result = self.health_checker.readiness()
        status_code = 200 if result.status.value == "healthy" else 503
        await self._send_json_response(writer, status_code, result.to_dict())

    async def _handle_startup(self, writer: asyncio.StreamWriter):
        """Handle startup probe."""
        result = self.health_checker.startup()
        status_code = 200 if result.status.value == "healthy" else 503
        await self._send_json_response(writer, status_code, result.to_dict())

    async def _handle_status(self, writer: asyncio.StreamWriter):
        """Handle detailed status request."""
        result = self.health_checker.detailed_status()
        await self._send_json_response(writer, 200, result.to_dict())

    async def _send_json_response(
        self, writer: asyncio.StreamWriter, status_code: int, data: dict
    ):
        """Send JSON HTTP response."""
        body = json.dumps(data, indent=2).encode()
        await self._send_response(writer, status_code, "OK", body, "application/json")

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        status_text: str,
        body: bytes = b"",
        content_type: str = "text/plain",
    ):
        """Send HTTP response."""
        response = (
            f"HTTP/1.1 {status_code} {status_text}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()

        writer.write(response)
        if body:
            writer.write(body)

        await writer.drain()
