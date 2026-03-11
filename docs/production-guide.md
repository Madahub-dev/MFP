# Production Operations Guide

This guide covers production deployment, monitoring, and operational best practices for MFP.

## Overview

MFP includes comprehensive production hardening features across three categories:
- **P1: Operational Robustness** — Logging, health checks, metrics, resource limits
- **P2: Performance Optimization** — Merkle tree, circuit breakers, timeouts, connection pooling
- **P3: Advanced Hardening** — Frame caching, key rotation, deduplication

---

## Quick Start

### Minimal Production Configuration

```python
from mfp.runtime.runtime import Runtime, RuntimeConfig
from mfp.observability.logging import LogConfig

config = RuntimeConfig(
    # Resource limits
    max_channels_per_agent=100,
    max_agents=10_000,

    # Timeouts
    agent_timeout_seconds=30.0,
    pipeline_timeout_seconds=5.0,

    # Logging
    log_config=LogConfig(
        level="INFO",
        format="json",
        include_correlation_ids=True,
    )
)

runtime = Runtime(config)
```

### Health Checks

```python
from mfp.observability.health import HealthCheckServer

# Start health check server on port 9877
health_server = HealthCheckServer(runtime, port=9877)
await health_server.start()

# Kubernetes liveness probe
# GET http://localhost:9877/health/live

# Kubernetes readiness probe
# GET http://localhost:9877/health/ready

# Startup probe
# GET http://localhost:9877/health/startup
```

### Metrics

```python
from mfp.observability.metrics import MetricsServer

# Start metrics server
metrics_server = MetricsServer(runtime, port=9877)
await metrics_server.start()

# Prometheus scrape endpoint
# GET http://localhost:9877/metrics
```

---

## P1: Operational Robustness

### 1.1 Structured Logging

**Features:**
- JSON logging for structured data
- Correlation IDs for request tracing
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Audit events for security-sensitive operations

**Configuration:**

```python
from mfp.observability.logging import LogConfig, LogFormat

config = RuntimeConfig(
    log_config=LogConfig(
        level="INFO",
        format=LogFormat.JSON,  # or LogFormat.TEXT
        include_correlation_ids=True,
        audit_events=True,
        output_file="/var/log/mfp/runtime.log",
    )
)
```

**Log Events:**
- Channel establishment/closure
- Message send/receive
- Quarantine triggers
- Storage operations
- Federation events
- Agent state transitions

**Example Log Entry:**

```json
{
  "timestamp": "2026-03-11T12:34:56.789Z",
  "level": "INFO",
  "correlation_id": "a1b2c3d4",
  "runtime_id": "e5f6g7h8",
  "agent_id": "i9j0k1l2",
  "channel_id": "m3n4o5p6",
  "operation": "send",
  "message": "Message sent successfully",
  "duration_ms": 2.5
}
```

### 1.2 Health Check Endpoints

**Endpoints:**

1. **Liveness** (`/health/live`)
   - Checks if process is alive
   - Returns 200 if event loop responsive
   - Use for Kubernetes `livenessProbe`

2. **Readiness** (`/health/ready`)
   - Checks if runtime can accept traffic
   - Validates: storage writable, Sg computable, no critical errors
   - Returns 200 if ready, 503 if not
   - Use for Kubernetes `readinessProbe`

3. **Startup** (`/health/startup`)
   - Checks if initialization complete
   - Returns 200 after startup, 503 before
   - Use for Kubernetes `startupProbe`

**Kubernetes Configuration:**

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: mfp-runtime
    livenessProbe:
      httpGet:
        path: /health/live
        port: 9877
      initialDelaySeconds: 5
      periodSeconds: 10

    readinessProbe:
      httpGet:
        path: /health/ready
        port: 9877
      initialDelaySeconds: 10
      periodSeconds: 5

    startupProbe:
      httpGet:
        path: /health/startup
        port: 9877
      failureThreshold: 30
      periodSeconds: 10
```

### 1.3 Metrics Instrumentation

**Metric Types:**

**Counters:**
- `mfp_messages_sent_total` — Total messages sent
- `mfp_messages_received_total` — Total messages received
- `mfp_validation_failures_total` — Validation failures by type
- `mfp_quarantine_events_total` — Quarantine events by reason

**Gauges:**
- `mfp_active_channels` — Current active channels
- `mfp_active_agents` — Current active agents
- `mfp_quarantined_agents` — Current quarantined agents
- `mfp_pending_messages` — Queue depth

**Histograms:**
- `mfp_pipeline_duration_seconds` — Pipeline stage duration
- `mfp_sg_computation_duration_seconds` — Sg computation time
- `mfp_storage_operation_duration_seconds` — Storage operation time
- `mfp_message_size_bytes` — Message size distribution

**Prometheus Configuration:**

```yaml
scrape_configs:
  - job_name: 'mfp'
    static_configs:
      - targets: ['localhost:9877']
    scrape_interval: 15s
    metrics_path: /metrics
```

### 1.4 Resource Limits

**Configuration:**

```python
config = RuntimeConfig(
    # Agent limits
    max_agents=10_000,
    max_channels_per_agent=100,

    # Federation limits
    max_bilateral_channels=100,
    max_connections=1_000,
    max_connection_rate=100,  # per second

    # Storage limits
    max_storage_size_mb=1024,  # 1 GB
)
```

**Enforcement:**
- Exceeding limits returns `AgentError(RESOURCE_LIMIT_EXCEEDED)`
- New connections rejected when limit reached
- Prevents OOM and file descriptor exhaustion

---

## P2: Performance Optimization

### 2.1 Merkle Tree (Incremental Sg)

**Overview:**
- O(log N) Sg updates instead of O(N)
- 10x faster for 1000+ channels
- <1ms Sg computation

**Configuration:**

```python
# Automatic - enabled by default
# Runtime automatically uses Merkle tree for Sg computation
runtime = Runtime()

# Merkle tree initialized on first channel
channel_id = runtime.establish_channel(agent_a, agent_b, depth=4)

# Sg updates incrementally on each message
runtime.send(agent_a, channel_id, payload)
```

**Performance:**
- 100 channels: 0.1ms
- 1,000 channels: 0.5ms
- 10,000 channels: 1.0ms

**Breaking Change:**
- Different Sg algorithm than v0.1.x
- All channels must be re-established after upgrade

### 2.2 Storage Circuit Breakers

**Overview:**
- Prevents storage failures from crashing runtime
- Three states: CLOSED → OPEN → HALF_OPEN
- Automatic recovery testing

**Configuration:**

```python
from mfp.storage.engine import StorageEngine, StorageConfig
from mfp.observability.circuit_breaker import CircuitBreakerConfig

breaker_config = CircuitBreakerConfig(
    failure_threshold=5,      # Open after 5 failures
    timeout_seconds=30.0,     # Try recovery after 30s
    half_open_max_attempts=3, # Test attempts in HALF_OPEN
    success_threshold=2,      # Close after 2 successes
)

storage = StorageEngine(
    StorageConfig(db_path="mfp.db"),
    breaker_config=breaker_config
)
```

**States:**
- **CLOSED:** Normal operation
- **OPEN:** Too many failures, operations fail fast
- **HALF_OPEN:** Testing recovery, limited attempts

**Handling:**

```python
from mfp.observability.circuit_breaker import CircuitBreakerOpen

try:
    storage.save_channel(channel, sg)
except CircuitBreakerOpen:
    # Circuit is open - storage unavailable
    # Fall back to in-memory cache or alert
    logger.error("Storage circuit breaker open")
```

### 2.3 Pipeline Timeouts

**Configuration:**

```python
config = RuntimeConfig(
    agent_timeout_seconds=30.0,      # Agent callable timeout
    pipeline_timeout_seconds=5.0,    # Total pipeline timeout
    storage_timeout_seconds=10.0,    # Storage operation timeout
)
```

**Behavior:**
- Agent timeout → Automatic quarantine
- Pipeline timeout → Message dropped, error logged
- Storage timeout → Circuit breaker

**Timeout Handling:**

```python
from mfp.core.types import AgentErrorCode

# Agent automatically quarantined on timeout
status = runtime.get_status(agent_id)
if status.error_code == AgentErrorCode.TIMEOUT:
    print("Agent timed out and was quarantined")

    # Restore after fix
    runtime.restore_agent(agent_id)
```

### 2.4 Connection Pooling

**Configuration:**

```python
from mfp.federation.transport import TransportConfig

config = TransportConfig(
    idle_timeout_seconds=300.0,           # 5 minutes
    max_connection_lifetime_seconds=3600.0, # 1 hour
    eviction_interval_seconds=60.0,       # Check every minute
    max_connections=1000,
)
```

**Features:**
- Connection reuse (avoids handshake overhead)
- Idle eviction (saves resources)
- Lifetime limits (prevents stale connections)
- Background eviction task
- Graceful shutdown

---

## P3: Advanced Hardening

### 3.1 Bilateral Circuit Breakers

**Configuration:**

```python
from mfp.federation.bilateral import BilateralChannel
from mfp.observability.circuit_breaker import CircuitBreakerConfig

bilateral_channel = BilateralChannel(...)

breaker_config = CircuitBreakerConfig(
    failure_threshold=5,
    timeout_seconds=60.0,
)

breaker = bilateral_channel.get_circuit_breaker(breaker_config)

# Wrap bilateral operations
try:
    result = breaker.execute(lambda: send_to_peer(...))
except CircuitBreakerOpen:
    logger.error(f"Bilateral channel {bilateral_id} unavailable")
```

**Benefits:**
- Prevents cascading failures from bad peers
- Independent failure tracking per bilateral channel
- Automatic backoff and recovery

### 3.2 Frame Caching

**Overview:**
- LRU cache for deterministic cross-runtime frames
- 16-17x speedup for cache hits
- 90% hit rate in recovery scenarios

**Configuration:**

```python
from mfp.core.frame import configure_frame_cache, clear_frame_cache

# Configure cache size (default: 1000 frames)
configure_frame_cache(maxsize=5000)

# Clear cache if needed
clear_frame_cache()

# Cache statistics
from mfp.core.frame import get_frame_cache_stats

hits, misses, hit_rate = get_frame_cache_stats()
print(f"Cache hit rate: {hit_rate:.2%}")
```

**Use Cases:**
- Recovery scenarios (replaying old frames)
- Bilateral channel validation
- Historical frame verification

**Limitations:**
- Only caches `sample_frame_cross_runtime()` (deterministic)
- Does NOT cache `sample_frame()` (uses OS jitter)
- Best for recovery, not normal operation

### 3.3 Key Rotation

**Configuration:**

```python
from mfp.federation.rotation import RotationConfig

rotation_config = RotationConfig(
    enable_auto_rotation=True,
    rotation_message_threshold=1_000_000,  # 1M messages
    rotation_time_threshold_seconds=86400.0, # 24 hours
    manual_rotation_enabled=True,
)

bilateral_channel.configure_rotation(rotation_config)

# Check if rotation needed
if bilateral_channel.should_rotate():
    # Initiate rotation protocol
    # ... (handled by federation layer)
    pass
```

**Rotation Protocol:**
1. Alice → Bob: `REKEY_REQUEST` (ephemeral public key)
2. Bob → Alice: `REKEY_ACCEPT` (ephemeral public key)
3. Both compute DH shared secret
4. Derive new bilateral state
5. Switch to new keys at agreed step

**Benefits:**
- Forward secrecy (ephemeral X25519 keypairs)
- Mitigates key fatigue
- Seamless rotation (no message loss)

### 3.4 Message Deduplication

**Configuration:**

```python
from mfp.runtime.deduplication import DeduplicationTracker, DeduplicationConfig

dedup_config = DeduplicationConfig(
    window_size=1000,        # Track last 1000 messages per channel
    ttl_seconds=300.0,       # 5 minute TTL
)

tracker = DeduplicationTracker(dedup_config)

# In pipeline accept stage
from mfp.core.types import MessageId

if tracker.is_duplicate(channel_id, message_id):
    raise AgentError(AgentErrorCode.DUPLICATE, "Duplicate message")
```

**Features:**
- O(1) duplicate detection (deque + set)
- Window-based eviction (FIFO)
- TTL-based eviction
- Independent tracking per channel
- Replay attack prevention

---

## Monitoring & Alerting

### Key Metrics to Monitor

**Performance:**
- `mfp_sg_computation_duration_seconds` — Should be <1ms
- `mfp_pipeline_duration_seconds` — Track pipeline bottlenecks
- `mfp_messages_sent_total` rate — Message throughput

**Health:**
- `mfp_active_channels` — Active channel count
- `mfp_quarantined_agents` — Quarantine rate (should be low)
- `mfp_validation_failures_total` — Validation failures (investigate spikes)

**Circuit Breakers:**
- Circuit breaker state changes (CLOSED → OPEN alerts)
- `mfp_circuit_breaker_state{name="storage"}` — Storage health
- `mfp_circuit_breaker_state{name="bilateral_*"}` — Bilateral health

### Recommended Alerts

```yaml
# Prometheus alerting rules
groups:
  - name: mfp
    rules:
      # High quarantine rate
      - alert: HighQuarantineRate
        expr: rate(mfp_quarantine_events_total[5m]) > 0.1
        annotations:
          summary: "High agent quarantine rate"

      # Storage circuit breaker open
      - alert: StorageCircuitOpen
        expr: mfp_circuit_breaker_state{name="storage"} == 2
        annotations:
          summary: "Storage circuit breaker is OPEN"

      # Slow Sg computation
      - alert: SlowSgComputation
        expr: histogram_quantile(0.95, mfp_sg_computation_duration_seconds) > 0.01
        annotations:
          summary: "Sg computation >10ms (p95)"

      # High validation failure rate
      - alert: HighValidationFailures
        expr: rate(mfp_validation_failures_total[5m]) > 0.05
        annotations:
          summary: "High validation failure rate"
```

---

## Performance Tuning

### Scaling Guidelines

**100 channels:**
- Default configuration works well
- Minimal tuning needed

**1,000 channels:**
- Merkle tree provides 10x speedup
- Monitor Sg computation time (<1ms)
- Consider increasing frame cache size

**10,000+ channels:**
- Tune resource limits (`max_agents`, `max_channels_per_agent`)
- Monitor memory usage (Merkle tree overhead)
- Consider sharding across multiple runtimes

### Resource Requirements

**Memory:**
- Base runtime: ~50 MB
- Per channel: ~1 KB
- Merkle tree overhead: ~500 bytes per channel
- Frame cache: ~100 bytes per cached frame

**CPU:**
- Sg computation: <1ms for 1000 channels
- ChaCha20 encryption: ~1-2ms per message
- Total pipeline: ~5-10ms per message

**Storage:**
- SQLite database: ~5 KB per channel
- Message queue: ~1 KB per message
- Sg cache: ~32 bytes per entry

---

## Deployment Best Practices

### 1. Health Checks

Always configure Kubernetes probes:
```yaml
livenessProbe: /health/live (detect crashes)
readinessProbe: /health/ready (load balancing)
startupProbe: /health/startup (slow init)
```

### 2. Resource Limits

Set appropriate pod limits:
```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "500m"
  limits:
    memory: "1Gi"
    cpu: "2000m"
```

### 3. Persistent Storage

Mount persistent volume for SQLite:
```yaml
volumeMounts:
  - name: mfp-data
    mountPath: /data
```

### 4. Monitoring

Deploy Prometheus and Grafana:
- Scrape `/metrics` endpoint
- Set up alerting rules
- Create dashboards for key metrics

### 5. Logging

Configure structured logging:
- JSON format for easy parsing
- Correlation IDs for tracing
- Ship logs to centralized system (ELK, Loki)

### 6. Circuit Breakers

Tune thresholds based on environment:
- Dev: Lower thresholds for fast feedback
- Prod: Higher thresholds to avoid false positives

---

## Troubleshooting

### High Quarantine Rate

**Symptoms:** Many agents being quarantined

**Causes:**
- Agents timing out (check `agent_timeout_seconds`)
- Validation failures (check agent logic)
- Network issues (bilateral channels)

**Solutions:**
- Increase timeout if legitimate slow agents
- Fix agent callable bugs
- Check bilateral connectivity

### Slow Sg Computation

**Symptoms:** `mfp_sg_computation_duration_seconds` >1ms

**Causes:**
- Very large channel count (>10,000)
- Merkle tree not being used (v0.1.x)

**Solutions:**
- Verify Merkle tree enabled (v0.2.0+)
- Profile Sg computation
- Consider sharding

### Storage Circuit Breaker Open

**Symptoms:** Circuit breaker in OPEN state

**Causes:**
- Disk full
- SQLite locked
- Corrupted database
- High I/O latency

**Solutions:**
- Check disk space
- Verify no other processes accessing DB
- Run SQLite integrity check
- Monitor I/O wait time

### High Memory Usage

**Symptoms:** Pod OOM killed

**Causes:**
- Too many channels (Merkle tree + channel state)
- Frame cache too large
- Deduplication window too large

**Solutions:**
- Reduce `max_channels_per_agent`
- Lower frame cache size
- Lower deduplication window size
- Increase pod memory limit

---

## Migration Guide

### Upgrading from v0.1.x to v0.2.0+

**Breaking Changes:**
- Merkle tree changes Sg computation
- All channels must be re-established

**Migration Steps:**

1. **Backup data**
   ```bash
   cp mfp.db mfp.db.backup
   ```

2. **Deploy new version**
   ```bash
   pip install mfp>=0.2.0
   ```

3. **Re-establish channels**
   - Existing channels will have incompatible Sg
   - Close old channels
   - Establish new channels with same agents

4. **Verify metrics**
   - Check Sg computation time (<1ms)
   - Verify Merkle tree in use

---

## Security Considerations

### Production Checklist

- [ ] Enable structured logging (audit trail)
- [ ] Configure resource limits (DoS prevention)
- [ ] Set up health checks (detect failures)
- [ ] Monitor metrics (anomaly detection)
- [ ] Tune timeouts (prevent blocking)
- [ ] Enable circuit breakers (resilience)
- [ ] Configure key rotation (forward secrecy)
- [ ] Enable deduplication (replay protection)
- [ ] Restrict health/metrics ports (localhost only in production)
- [ ] Use TLS for bilateral connections
- [ ] Review logs regularly (security events)

---

## Support

For production support:
- GitHub Issues: [github.com/your-org/mfp/issues](https://github.com)
- Documentation: [docs/](.)
- Security: See [security.md](./security.md)
