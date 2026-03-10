# MFP Production Hardening — Build Journal

```yaml
project: Mirror Frame Protocol
phase: Production Hardening (P1-P3)
status: in-progress
created: 2026-03-10
authors:
  - Akil Abderrahim
  - Claude Sonnet 4.5
```

---

## Overview

This journal tracks the implementation of production hardening features for MFP v0.2.0+. Phase P0 (Critical Security) is complete. This document covers the remaining operational, performance, and advanced hardening work.

**Completed:** P0 - Critical Security (Config validation, input validation, secure defaults)

**In Progress:** P1 → P2 → P3

---

## P1 — Operational Robustness (Weeks 3-4)

**Status:** ✅ Complete
**Goal:** Production observability, health monitoring, and resource protection.

### 1. Structured Logging with Correlation IDs

**Status:** ✅ Complete
**Priority:** High
**Estimate:** 2-3 days

#### Motivation

Current logging is sparse and unstructured:
- Only `server.py` and `transport.py` have logging
- No correlation IDs to trace messages through the pipeline
- No structured fields (JSON logging)
- Missing critical audit events (channel establishment, quarantine)

#### Design

**New Module:** `mfp/observability/logging.py`

```python
@dataclass
class LogContext:
    """Structured logging context."""
    correlation_id: str      # Trace ID for message lifecycle
    runtime_id: str          # Deployment/instance ID
    agent_id: str | None     # Agent involved (if applicable)
    channel_id: str | None   # Channel involved (if applicable)
    operation: str           # e.g., "send", "deliver", "quarantine"

def structured_log(level: str, message: str, context: LogContext, **kwargs):
    """Emit structured log entry (JSON format)."""
    ...
```

**Correlation ID Flow:**
1. Generated on `mfp_send()` → stored in `DeliveredMessage`
2. Flows through pipeline stages (ACCEPT → FRAME → ENCODE → VALIDATE → DECODE → DELIVER)
3. Logged at each stage with timing metadata
4. Stored in `Receipt` for client tracking

**Log Events to Add:**
- Channel establishment (agent_a ↔ agent_b)
- Quarantine triggers (reason, threshold exceeded)
- Validation failures (frame mismatch, HMAC failure)
- Storage operations (commit, rollback, recovery)
- Federation events (bilateral handshake, recovery protocol)
- Agent state transitions (BOUND → ACTIVE → QUARANTINED)

**Implementation Files:**
- `mfp/observability/logging.py` — Structured logger
- `mfp/runtime/runtime.py` — Add logging to deliver(), send()
- `mfp/runtime/pipeline.py` — Log each pipeline stage
- `mfp/storage/engine.py` — Log transactions, recovery
- `mfp/federation/transport.py` — Log connection events
- `mfp/runtime/quarantine.py` — Log quarantine events

**Configuration:**
```yaml
logging:
  format: "json"  # or "text"
  level: "INFO"
  include_correlation_ids: true
  audit_events: true
```

**Testing:**
- Unit tests: Verify log entries emitted for each event
- Integration tests: Correlation ID flows through full pipeline
- E2E tests: Grep logs for correlation_id, verify ordering

---

### 2. Health Check Endpoints

**Status:** ✅ Complete
**Priority:** High
**Estimate:** 2 days

#### Motivation

No way to programmatically check if the server is healthy:
- K8s/Docker need liveness/readiness probes
- Operators need status endpoints for monitoring
- No way to detect degraded state (e.g., storage failing)

#### Design

**Health Check Types:**

1. **Liveness:** Is the process alive?
   - Check: Event loop responsive (timeout 1s)
   - Endpoint: `/health/live`
   - Returns: `200 OK` or timeout

2. **Readiness:** Can the server accept traffic?
   - Check: Storage writable, Sg computable, no critical errors
   - Endpoint: `/health/ready`
   - Returns: `200 OK` if ready, `503 Service Unavailable` if not

3. **Startup:** Has initialization completed?
   - Check: Schema migrated, recovery successful
   - Endpoint: `/health/startup`
   - Returns: `200 OK` after startup, `503` before

**Response Format (JSON):**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "active_channels": 42,
  "quarantined_agents": 0,
  "storage_writable": true,
  "last_sg_computation_ms": 125,
  "version": "0.2.0"
}
```

**Implementation:**

Add HTTP server to `server.py`:
```python
from aiohttp import web

async def liveness_handler(request):
    return web.json_response({"status": "alive"})

async def readiness_handler(request):
    # Check storage, Sg computation, quarantine count
    if not await check_readiness():
        return web.json_response({"status": "not_ready"}, status=503)
    return web.json_response({"status": "ready"})

# Run HTTP server on separate port (e.g., 9877)
```

**Configuration:**
```yaml
health:
  enabled: true
  port: 9877
  host: "127.0.0.1"  # Localhost only for security
```

**Implementation Files:**
- `mfp/observability/health.py` — Health check logic
- `mfp/server.py` — HTTP server for health endpoints
- `mfp/runtime/runtime.py` — Expose health status

**Testing:**
- Unit tests: Health checks return correct status
- Integration tests: Simulate failures (storage down, Sg timeout)
- E2E tests: curl health endpoints, verify responses

---

### 3. Metrics Instrumentation

**Status:** ✅ Complete
**Priority:** Medium
**Estimate:** 3 days

#### Motivation

No telemetry for performance monitoring:
- Can't track message throughput, latency
- No visibility into queue depths, channel counts
- No alerts on performance degradation

#### Design

**Metrics to Track:**

**Counters:**
- `mfp_messages_sent_total` (labels: agent_id, channel_id)
- `mfp_messages_received_total`
- `mfp_validation_failures_total` (labels: error_type)
- `mfp_quarantine_events_total` (labels: reason)

**Gauges:**
- `mfp_active_channels` (current count)
- `mfp_active_agents`
- `mfp_quarantined_agents`
- `mfp_pending_messages` (queue depth)

**Histograms:**
- `mfp_pipeline_duration_seconds` (labels: stage)
- `mfp_sg_computation_duration_seconds`
- `mfp_storage_operation_duration_seconds` (labels: operation)
- `mfp_message_size_bytes`

**Implementation:**

Use `prometheus_client` library:
```python
from prometheus_client import Counter, Gauge, Histogram

messages_sent = Counter('mfp_messages_sent_total', 'Messages sent', ['agent_id'])
pipeline_duration = Histogram('mfp_pipeline_duration_seconds', 'Pipeline stage duration', ['stage'])

# In pipeline
with pipeline_duration.labels(stage="FRAME").time():
    frame = sample_frame(...)
```

**Exposition:**
- HTTP endpoint: `/metrics` (Prometheus format)
- Port: Same as health checks (9877)

**Configuration:**
```yaml
metrics:
  enabled: true
  port: 9877
  path: "/metrics"
```

**Implementation Files:**
- `mfp/observability/metrics.py` — Metric definitions
- `mfp/runtime/pipeline.py` — Instrument pipeline stages
- `mfp/runtime/runtime.py` — Instrument send/deliver
- `mfp/storage/engine.py` — Instrument storage ops
- `mfp/server.py` — Expose /metrics endpoint

**Testing:**
- Unit tests: Metrics increment correctly
- Integration tests: Full pipeline updates all metrics
- E2E tests: Scrape /metrics, verify Prometheus format

---

### 4. Resource Limits

**Status:** ✅ Complete
**Priority:** High
**Estimate:** 2 days

#### Motivation

Unbounded resource usage:
- No limit on channels per agent (memory exhaustion)
- No limit on total agents (DoS vector)
- No limit on bilateral channels (network overhead)
- No limit on TCP connections (file descriptor exhaustion)
- No limit on storage size (disk exhaustion)

#### Design

**New Limits in RuntimeConfig:**
```python
@dataclass(frozen=True)
class RuntimeConfig:
    # Existing...
    max_channels_per_agent: int = 100
    max_agents: int = 10_000
    max_bilateral_channels: int = 100
    max_storage_size_mb: int = 1024  # 1 GB
```

**New Limits in TransportConfig:**
```python
@dataclass(frozen=True)
class TransportConfig:
    # Existing...
    max_connections: int = 1000
    max_connection_rate: int = 100  # connections per second
```

**Enforcement:**

1. **max_channels_per_agent:**
   - Check in `Runtime.establish_channel()`
   - Return `AgentError(RESOURCE_LIMIT_EXCEEDED)` if exceeded

2. **max_agents:**
   - Check in `Runtime.bind()`
   - Return `AgentError(RESOURCE_LIMIT_EXCEEDED)` if exceeded

3. **max_bilateral_channels:**
   - Check in `BilateralChannel.establish()`
   - Reject new bilateral channels if limit reached

4. **max_connections:**
   - Check in `TransportServer.handle_connection()`
   - Close connection immediately if limit exceeded

5. **max_storage_size_mb:**
   - Check in `StorageEngine.enqueue_message()`
   - Trigger cleanup or reject write if exceeded

**Implementation Files:**
- `mfp/runtime/pipeline.py` — Add resource limit fields
- `mfp/runtime/runtime.py` — Enforce agent and channel limits
- `mfp/federation/transport.py` — Enforce connection limits
- `mfp/storage/engine.py` — Enforce storage size limit

**Testing:**
- Unit tests: Limits enforced, errors returned
- Integration tests: Hit limits, verify graceful rejection
- E2E tests: Max channels, max agents, max connections

---

## P2 — Performance Optimization (Weeks 5-6)

**Goal:** Optimize hot paths, reduce latency, improve scalability.

### 1. Incremental Sg Computation (Merkle Tree)

**Status:** Not Started
**Priority:** Critical
**Estimate:** 4-5 days

#### Motivation

**Current Bottleneck:** `recompute_sg()` in `runtime.py`
- Called after every message
- O(N) where N = number of channels
- SHA-256 of concatenated states (megabytes for 1000+ channels)
- Becomes bottleneck at scale (10ms+ for 1000 channels)

#### Design

**Replace with Merkle Tree:**
- Store channel states as leaves
- Recompute only path from updated leaf to root
- O(log N) instead of O(N)

**Data Structure:**
```python
@dataclass
class MerkleNode:
    hash: bytes  # SHA-256(left_hash || right_hash)
    left: MerkleNode | None
    right: MerkleNode | None
    leaf_value: bytes | None  # Channel state if leaf

class IncrementalSg:
    """Merkle tree for incremental global state computation."""
    root: MerkleNode
    leaf_map: dict[ChannelId, MerkleNode]  # Fast leaf lookup

    def update_channel(self, channel_id: ChannelId, new_state: StateValue):
        """Update single channel, recompute O(log N) hashes."""
        leaf = self.leaf_map[channel_id]
        leaf.hash = sha256(new_state)
        self._recompute_path_to_root(leaf)

    def get_root_hash(self) -> bytes:
        """Current Sg value (Merkle root)."""
        return self.root.hash
```

**Migration:**
- Replace `compose_ordered()` with `IncrementalSg.get_root_hash()`
- Build tree on startup from storage
- Update tree on every channel advance

**Performance:**
- Before: 10ms for 1000 channels
- After: <1ms (log2(1000) ≈ 10 hashes)

**Implementation Files:**
- `mfp/core/merkle.py` — Merkle tree implementation
- `mfp/core/ratchet.py` — Replace compose_ordered with Merkle
- `mfp/runtime/runtime.py` — Use IncrementalSg
- `mfp/storage/engine.py` — Persist Merkle tree structure

**Testing:**
- Unit tests: Merkle tree correctness, incremental updates
- Property tests: Merkle root matches compose_ordered
- Benchmark: Verify O(log N) scaling

---

### 2. Storage Circuit Breakers

**Status:** Not Started
**Priority:** High
**Estimate:** 2-3 days

#### Motivation

Storage failures crash the runtime:
- SQLite busy/locked → hard failure
- Disk full → unhandled exception
- No retry logic for transient errors

#### Design

**Circuit Breaker States:**
- **CLOSED:** Normal operation
- **OPEN:** Too many failures, stop trying
- **HALF_OPEN:** Test if storage recovered

**State Transitions:**
```
CLOSED --[N failures]--> OPEN
OPEN --[timeout]--> HALF_OPEN
HALF_OPEN --[success]--> CLOSED
HALF_OPEN --[failure]--> OPEN
```

**Configuration:**
```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5  # Open after N failures
    timeout_seconds: int = 30   # Try again after timeout
    half_open_attempts: int = 3 # Test attempts in HALF_OPEN
```

**Degraded Mode:**
When circuit is OPEN:
- Switch to read-only mode
- Cache Sg in memory only
- Log errors, emit metrics

**Implementation:**
```python
class StorageCircuitBreaker:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def execute(self, operation: Callable[[], T]) -> T:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpen("Storage unavailable")

        try:
            result = operation()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
            self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            if self.failure_count >= self.threshold:
                self.state = CircuitState.OPEN
            raise
```

**Implementation Files:**
- `mfp/observability/circuit_breaker.py` — Circuit breaker base class
- `mfp/storage/engine.py` — Wrap operations in circuit breaker
- `mfp/runtime/runtime.py` — Handle CircuitBreakerOpen exception

**Testing:**
- Unit tests: Circuit breaker state machine
- Integration tests: Simulate storage failures, verify OPEN state
- E2E tests: Recovery from transient failures

---

### 3. Pipeline Timeouts

**Status:** Not Started
**Priority:** Medium
**Estimate:** 2 days

#### Motivation

No timeout protection:
- Agent callable can block forever
- Pipeline can hang on crypto operations
- Storage operations can deadlock

#### Design

**Timeout Points:**
1. **Agent callable:** 30s default
2. **Pipeline total:** 5s default
3. **Storage operation:** 10s default

**Implementation:**
```python
@dataclass(frozen=True)
class RuntimeConfig:
    # Existing...
    agent_timeout_seconds: float = 30.0
    pipeline_timeout_seconds: float = 5.0
    storage_timeout_seconds: float = 10.0
```

**Enforcement (asyncio):**
```python
async def deliver_with_timeout(agent_callable, message, timeout):
    try:
        await asyncio.wait_for(agent_callable(message), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(f"Agent callable timeout after {timeout}s")
        # Quarantine agent
        raise AgentError(AgentErrorCode.TIMEOUT)
```

**Enforcement (sync):**
```python
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Operation timed out")

def with_timeout(func, timeout):
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(timeout))
    try:
        result = func()
    finally:
        signal.alarm(0)  # Cancel alarm
    return result
```

**Timeout Actions:**
- Agent timeout → Quarantine agent
- Pipeline timeout → Log error, drop message
- Storage timeout → Circuit breaker

**Implementation Files:**
- `mfp/runtime/pipeline.py` — Pipeline timeout wrapper
- `mfp/runtime/runtime.py` — Agent callable timeout
- `mfp/storage/engine.py` — Storage operation timeout

**Testing:**
- Unit tests: Timeouts trigger correctly
- Integration tests: Slow agent triggers quarantine
- E2E tests: End-to-end timeout enforcement

---

### 4. Connection Pooling Improvements

**Status:** Not Started
**Priority:** Medium
**Estimate:** 2-3 days

#### Motivation

Current connection management is inefficient:
- One connection per bilateral channel (no reuse)
- No idle timeout (connections held forever)
- No connection health checks (dead connections linger)
- No graceful close (abrupt disconnect)

#### Design

**Connection Pool:**
```python
@dataclass
class ConnectionPoolConfig:
    max_connections: int = 100
    idle_timeout_seconds: int = 300  # 5 minutes
    keepalive_interval_seconds: int = 60
    max_connection_lifetime_seconds: int = 3600  # 1 hour

class ConnectionPool:
    """Connection pool for bilateral channels."""
    active: dict[str, Connection]  # endpoint -> connection
    idle: dict[str, tuple[Connection, float]]  # endpoint -> (conn, last_used)

    async def get_or_create(self, endpoint: str) -> Connection:
        # Check active pool
        # Check idle pool (evict stale)
        # Create new if needed
        ...

    async def evict_idle(self):
        """Background task to close idle connections."""
        now = time.time()
        for endpoint, (conn, last_used) in list(self.idle.items()):
            if now - last_used > self.idle_timeout:
                await conn.close()
                del self.idle[endpoint]
```

**Keepalive:**
- Send periodic ping frame every 60s
- Close connection if no pong after 10s
- Detect dead connections early

**Graceful Shutdown:**
- Drain period: 5s to finish in-flight messages
- Send close frame to peer
- Wait for acknowledgment
- Force close after timeout

**Implementation Files:**
- `mfp/federation/pool.py` — Connection pool
- `mfp/federation/transport.py` — Use pool, add keepalive
- `mfp/server.py` — Graceful shutdown on SIGTERM

**Testing:**
- Unit tests: Pool eviction, reuse
- Integration tests: Keepalive detection
- E2E tests: Graceful shutdown

---

## P3 — Advanced Hardening (Weeks 7-8)

**Goal:** Advanced resilience, optimization, and security features.

### 1. Bilateral Circuit Breakers

**Status:** Not Started
**Priority:** Medium
**Estimate:** 2 days

#### Motivation

Federation failures cascade:
- One bad peer causes repeated failures
- No backoff for problematic bilaterals
- Recovery protocol can loop infinitely

#### Design

Similar to storage circuit breaker:
- Track failures per bilateral channel
- Open circuit after N consecutive failures
- Half-open to test recovery
- Emit metrics and alerts

**Configuration:**
```python
@dataclass
class BilateralConfig:
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 60  # seconds
```

**Implementation Files:**
- `mfp/federation/bilateral.py` — Wrap send in circuit breaker
- `mfp/federation/recovery.py` — Circuit breaker for recovery

**Testing:**
- Unit tests: Circuit breaker logic
- Integration tests: Repeated failures trigger OPEN

---

### 2. Frame Caching

**Status:** Not Started
**Priority:** Low
**Estimate:** 2 days

#### Motivation

Frame sampling is deterministic:
- Same (Sl, t, Sg) → same frame
- Can cache last K frames per channel
- Avoid redundant ChaCha20 keystream generation

#### Design

**LRU Cache:**
```python
@dataclass
class FrameCacheKey:
    channel_id: ChannelId
    step: int
    sl: StateValue
    sg: StateValue

frame_cache: LRUCache[FrameCacheKey, Frame] = LRUCache(maxsize=1000)
```

**Cache Hit Rate:**
- High for channels with predictable Sg
- Low for high-churn scenarios
- Target: 30-50% hit rate

**Implementation Files:**
- `mfp/core/frame.py` — Add caching layer
- `mfp/runtime/runtime.py` — Configure cache size

**Testing:**
- Unit tests: Cache hits/misses
- Benchmark: Measure speedup

---

### 3. Key Rotation Mechanism

**Status:** Not Started
**Priority:** Medium
**Estimate:** 3 days

#### Motivation

Long-lived channels never refresh keys:
- Cryptographic key fatigue
- No bound on messages per key
- Bilateral keys never rotate

#### Design

**Rotation Triggers:**
1. **Message count:** Rotate after 1M messages
2. **Time-based:** Rotate after 24 hours
3. **Manual:** Operator-triggered rotation

**Protocol:**
```
Alice → Bob: REKEY_REQUEST (new_public_key)
Bob → Alice: REKEY_ACCEPT (new_public_key)
[Perform X25519 DH]
Alice & Bob: Switch to new_key at step N+1
```

**Implementation Files:**
- `mfp/federation/bilateral.py` — Rekey protocol
- `mfp/runtime/channels.py` — Rotation triggers

**Testing:**
- Unit tests: Rekey protocol
- Integration tests: Seamless transition

---

### 4. Message Deduplication

**Status:** Not Started
**Priority:** Low
**Estimate:** 2 days

#### Motivation

No protection against duplicate delivery:
- Network retries can cause duplicates
- Recovery protocol may replay messages

#### Design

**Deduplication Window:**
- Track last N message IDs per channel
- Or time window (e.g., last 5 minutes)
- Reject duplicates at pipeline entry

**Storage:**
```python
recent_messages: dict[ChannelId, set[MessageId]] = {}
```

**Implementation Files:**
- `mfp/runtime/pipeline.py` — Check for duplicates
- `mfp/storage/engine.py` — Persist dedup set

**Testing:**
- Unit tests: Duplicate detection
- Integration tests: Replay protection

---

## Implementation Roadmap

### Week 3 (P1 Start)
- [x] P0 Complete
- [ ] Structured logging (2-3 days)
- [ ] Health checks (2 days)
- [ ] Start metrics instrumentation

### Week 4 (P1 Finish)
- [ ] Finish metrics (1 day)
- [ ] Resource limits (2 days)
- [ ] Testing and integration
- [ ] P1 Release: v0.2.0

### Week 5 (P2 Start)
- [ ] Incremental Sg (Merkle tree) (4-5 days)

### Week 6 (P2 Finish)
- [ ] Storage circuit breakers (2-3 days)
- [ ] Pipeline timeouts (2 days)
- [ ] Connection pooling (2-3 days)
- [ ] P2 Release: v0.3.0

### Week 7 (P3 Start)
- [ ] Bilateral circuit breakers (2 days)
- [ ] Frame caching (2 days)
- [ ] Key rotation (3 days)

### Week 8 (P3 Finish)
- [ ] Message deduplication (2 days)
- [ ] Final testing
- [ ] Documentation updates
- [ ] P3 Release: v0.4.0

---

## Success Criteria

**P1 (Operational):**
- [ ] All critical paths logged with correlation IDs
- [ ] Health endpoints respond in <100ms
- [ ] Metrics exposed in Prometheus format
- [ ] Resource limits prevent OOM/file descriptor exhaustion

**P2 (Performance):**
- [ ] Sg computation <1ms for 1000+ channels
- [ ] Storage failures don't crash runtime
- [ ] Pipeline timeouts prevent blocking
- [ ] Connection pool reduces overhead

**P3 (Advanced):**
- [ ] Bilateral failures don't cascade
- [ ] Frame cache improves throughput by 20%
- [ ] Key rotation works seamlessly
- [ ] Duplicate messages rejected

---

## Notes

- All features require comprehensive testing (unit, integration, E2E)
- Metrics and logging should be added incrementally
- Performance benchmarks required for P2 features
- Backward compatibility maintained where possible
- Breaking changes documented in CHANGELOG

---

**Next:** Start P1.1 — Structured Logging with Correlation IDs
