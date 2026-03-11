# Architecture

This document provides a high-level overview of MFP's internal design. It's intended for contributors and users who want to understand how the protocol works under the hood.

## Design Philosophy

**Library-first, runtime-mediated, symmetric by default.**

MFP is not a client/server system. It's a protocol for peer-to-peer communication where every agent is treated equally. The runtime acts as a neutral mediator, enforcing security constraints without imposing workflow hierarchy.

**Key principles:**

1. **No trust in LLMs** — Cryptographic validation happens before message delivery, not after
2. **Symmetric peers** — All agents use the same API and have identical capabilities
3. **Deterministic enforcement** — Security constraints are mathematical, not policy-based
4. **Library-first** — The runtime embeds in any Python process; the standalone server is a thin wrapper

---

## Layered Architecture

MFP is organized into six layers, from lowest to highest abstraction:

```
┌──────────────────────────────────────────────────────────┐
│                     Application                          │  ← User code
│           (Agent callables, business logic)              │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                   Agent Lifecycle                        │  ← bind/unbind, tools
│     (identity.py, lifecycle.py, tools.py)                │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                       Runtime                            │  ← Pipeline, quarantine
│    (runtime.py, pipeline.py, channels.py, quarantine.py) │
└──────────────────────────────────────────────────────────┘
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
┌──────────────┐  ┌─────────────┐  ┌──────────────┐
│  Core        │  │  Storage    │  │ Observability│  ← Production
│ (crypto,     │  │ (SQLite,    │  │ (logging,    │     hardening
│  frames,     │  │  schema,    │  │  metrics,    │
│  ratchet,    │  │  crypto)    │  │  health,     │
│  types)      │  │             │  │  circuit     │
│              │  │             │  │  breakers)   │
└──────────────┘  └─────────────┘  └──────────────┘
            │             │             │
            └─────────────┴─────────────┘
                          ▼
            ┌─────────────────────────────┐
            │        Federation           │  ← Cross-runtime
            │  (bilateral, transport,     │     communication
            │   recovery, wire protocol)  │
            └─────────────────────────────┘
```

---

## Layer 1: Core

**Modules:** `mfp/core/`

The foundation layer providing cryptographic primitives and frame construction.

### `primitives.py`

Low-level cryptographic operations:
- **ChaCha20-Poly1305** — AEAD encryption/decryption
- **HMAC-SHA256** — Message authentication
- **X25519** — Diffie-Hellman key exchange
- **SHA-256** — Hashing and key derivation

All primitives use the `cryptography` library.

### `frame.py`

Frame construction and validation:
- **build_frame()** — Constructs symmetric mirror frame (`2k` blocks)
- **validate_frame()** — Verifies frame integrity and temporal validity
- **strip_frame()** — Removes validated frame, returns payload

Frames are deterministic functions of `(step, local_state, global_state)`.

### `ratchet.py`

State evolution:
- **advance_ratchet()** — One-way state chain: `S_n = f(S_{n-1}, frame_n)`
- **derive_frame_seed()** — Generates cryptographic seed from ratchet state

Uses HMAC-based ratcheting for forward secrecy.

### `encoding.py`

Payload transformation:
- **encode_payload()** — Encrypts plaintext with ChaCha20-Poly1305
- **decode_payload()** — Decrypts ciphertext, verifies authentication tag

### `types.py`

Core type definitions:
- `AgentId`, `ChannelId`, `MessageId` (32-byte identifiers)
- `AgentStatus` (BOUND, ACTIVE, QUARANTINED, UNBOUND)
- `StateValue` (ratchet state, 32 bytes)
- Error types and enums

---

## Layer 2: Storage

**Modules:** `mfp/storage/`

Persistent state management via SQLite.

### `engine.py`

Storage interface:
- **create_agent()** / **delete_agent()** — Agent lifecycle
- **create_channel()** / **get_channel()** — Channel records
- **enqueue_message()** / **dequeue_message()** — Message queuing
- **update_ratchet()** — Ratchet state persistence

All operations are transactional.

### `schema.py`

SQLite schema definitions:
- `agents` — Agent identity, status, quarantine flags
- `channels` — Channel metadata, peer mappings, ratchet state
- `messages` — Pending message queue (FIFO)
- `ratchets` — Local and global ratchet states

### `crypto.py`

At-rest encryption (optional):
- **encrypt_value()** / **decrypt_value()** — Database field encryption
- Uses ChaCha20-Poly1305 with a master key from `storage.master_key_file`

---

## Layer 3: Runtime

**Modules:** `mfp/runtime/`

Execution orchestration and security enforcement.

### `runtime.py`

Central coordinator:
- **Runtime.bind()** — Register agent, assign `AgentId`
- **Runtime.unbind()** — Deregister agent, close channels
- **Runtime.deliver()** — Route message to agent callable
- **Runtime.shutdown()** — Graceful teardown

Holds the `StorageEngine` and global ratchet state.

### `pipeline.py`

Agent callable wrapper:
- Validates agent state before message delivery
- Invokes agent callable with `(channel_id, plaintext_message)`
- Captures return value for hooks/logging
- Enforces retry logic and error handling

### `channels.py`

Channel management:
- **establish_channel()** — Create new channel, initialize ratchet
- **close_channel()** — Tear down channel, flush pending messages
- **derive_channel_id()** — Deterministic ID from `(agent_a, agent_b, nonce)`

### `quarantine.py`

Security isolation:
- **check_rate_limit()** — Enforce `max_message_rate`
- **check_payload_size()** — Enforce `max_payload_size`
- **trigger_quarantine()** — Transition agent to `QUARANTINED` state

Quarantined agents cannot send/receive messages until administratively reset.

---

## Layer 4: Agent Lifecycle

**Modules:** `mfp/agent/`

User-facing API for agent operations.

### `lifecycle.py`

Agent binding and handle management:
- **bind()** — Register agent callable with runtime, return `AgentHandle`
- **unbind()** — Deregister agent
- **AgentHandle.establish_channel()** — Create channel to peer

### `identity.py`

Agent identity derivation:
- **derive_agent_id()** — Deterministic ID from `(runtime_id, agent_callable)`
- Uses HMAC-SHA256 for stable, unique identifiers

### `tools.py`

Protocol tools (agent-facing API):
- **mfp_send()** — Send encrypted message on channel
- **mfp_channels()** — List all channels for agent
- **mfp_status()** — Query agent status (state, channel count, pending messages)

These are the primary functions LLM agents invoke.

---

## Layer 5: Federation

**Modules:** `mfp/federation/`

Cross-runtime communication and recovery.

### `bilateral.py`

Bilateral channel establishment:
- **bootstrap_deterministic()** — Derive shared key from runtime IDs
- **bootstrap_ceremonial()** — X25519 Diffie-Hellman key exchange
- **BilateralChannel** — Cross-runtime channel abstraction

### `transport.py`

TCP message transport:
- **TransportServer** — Listen for incoming envelopes
- **TransportClient** — Send envelopes to remote runtime
- Asynchronous I/O with configurable timeouts

### `wire.py`

Wire protocol:
- **build_envelope_header()** — Frame + routing metadata
- **validate_envelope()** — Verify integrity, decrypt payload
- Uses the same frame/ratchet mechanism as local channels

### `recovery.py`

State synchronization:
- **detect_divergence()** — Identify ratchet state mismatch
- **negotiate_recovery()** — Exchange state proofs, find common ancestor
- **resync()** — Fast-forward ratchet to converge

Prevents federation from stalling due to dropped messages.

---

## Message Flow

### Local Send (Alice → Bob, same runtime)

1. **Application** calls `mfp_send(alice_handle, channel_id, plaintext)`
2. **Agent tools** validate handle state, lookup channel
3. **Runtime pipeline** retrieves ratchet state from storage
4. **Core frame** builds mirror frame from `(step, local_state, global_state)`
5. **Core encoding** encrypts plaintext with channel key
6. **Core frame** wraps payload in frame: `[open | ciphertext | close]`
7. **Storage** persists message to Bob's queue, advances ratchet
8. **Runtime** delivers to Bob's callable: `bob(channel_id, plaintext)`
9. **Return value** captured for logging/hooks

### Federated Send (Alice → Charlie, remote runtime)

1. **Application** calls `mfp_send(alice_handle, bilateral_channel_id, plaintext)`
2. **Agent tools** identify channel as bilateral (cross-runtime)
3. **Federation bilateral** retrieves shared federation key
4. **Core frame** builds frame using bilateral ratchet state
5. **Core encoding** encrypts plaintext
6. **Federation wire** constructs envelope with routing header
7. **Federation transport** sends envelope over TCP to remote runtime
8. **Remote runtime** receives envelope, validates frame
9. **Remote runtime** delivers to Charlie's callable
10. **Recovery** monitors for sequence gaps, triggers resync if needed

---

## Security Model

### Threat Mitigation

**Frame validation prevents:**
- Prompt injection (invalid frames rejected before payload delivery)
- Replay attacks (temporal ratchet invalidates old frames)
- Forgery (frames cryptographically bound to ratchet state)

**Encryption provides:**
- Confidentiality (ChaCha20-Poly1305 AEAD)
- Authenticity (HMAC verification)
- Forward secrecy (ratchet never reverses)

**Quarantine protects against:**
- Malicious agents (rate/size limits)
- Corrupted state (validation failure thresholds)

### Non-Goals

MFP does **not** protect against:
- Compromised runtime (trusted computing base)
- Side-channel attacks (timing, memory access patterns)
- Physical access to storage (at-rest encryption is optional)

See [Security Model](security.md) for full threat analysis.

---

## Design Decisions

### Why SQLite?

- **Transactional:** ACID guarantees for ratchet state
- **Embedded:** No external database server
- **Simple:** Single file, easy backup/restore
- **WAL mode:** Concurrent reads during writes

### Why Symmetric Frames?

- **Structural validation:** LLMs can't bypass mathematical constraints
- **Deterministic:** No policy decisions, no edge cases
- **Efficient:** O(k) validation, constant-size state

### Why Ratchet Instead of Logs?

- **Bounded state:** Fixed 32 bytes per channel, regardless of history
- **Forward secrecy:** Old states can't be recovered
- **Federation-friendly:** Only current state needs synchronization

### Why Library-First?

- **Composable:** Embed in any application
- **Testable:** Pure functions, no global state
- **Portable:** No daemon dependencies

---

## Testing Strategy

**604 tests across 6 test modules:**

- **Unit tests** — Core primitives, frame logic, ratchet evolution
- **Integration tests** — Runtime + storage + agents
- **E2E tests** — Full message flows, quarantine triggers, federation
- **Property tests** — Frame symmetry, ratchet monotonicity

Coverage: ~95% (excluding server.py boilerplate).

---

## Performance Characteristics

**Benchmarks (single runtime, 2 agents, 10k messages):**

- Frame construction: ~50 µs/message
- Encryption/decryption: ~20 µs/message
- SQLite write: ~100 µs/message
- End-to-end latency: ~200 µs/message

**Federation overhead:**

- TCP transport: +5-50ms (network latency)
- Recovery protocol: ~100ms (worst case, 3 round-trips)

**Memory:**

- Runtime: ~5 MB baseline
- Per-agent overhead: ~1 KB (ratchet state + metadata)
- Per-message queue: ~1 KB/message

---

## Extension Points

### Custom Agent Types

Implement the agent callable signature:

```python
def agent(channel_id: bytes, message: bytes) -> dict:
    # Your logic here
    return {"status": "ok"}
```

### Lifecycle Hooks

Enable hooks in `RuntimeConfig(enable_hooks=True)`:

- `before_send` — Pre-validation hook
- `after_send` — Post-delivery hook
- `on_quarantine` — Isolation event

### Storage Backends

Subclass `StorageEngine` to use alternative backends (Postgres, DynamoDB, etc.).

### Transport Protocols

Subclass `TransportServer` for non-TCP transports (WebSocket, QUIC, etc.).

---

## See Also

- [API Reference](api-reference.md) — public interface
- [Server Guide](server-guide.md) — standalone server setup
- [Security Model](security.md) — threat analysis
- [Contributing](contributing.md) — development guidelines
- Protocol design specs in [`design/`](../design/)

---

## Layer 6: Observability & Performance (v0.2.0+)

**Modules:** `mfp/observability/`, `mfp/core/merkle.py`, `mfp/federation/rotation.py`, `mfp/runtime/deduplication.py`

Production hardening features for monitoring, resilience, and performance.

### Observability (`mfp/observability/`)

**logging.py** — Structured logging:
- LogContext with correlation IDs
- JSON/text format support
- Audit events for security operations

**health.py** — Health check endpoints:
- `/health/live` — Liveness probe (K8s)
- `/health/ready` — Readiness probe (K8s)
- `/health/startup` — Startup probe (K8s)

**metrics.py** — Prometheus metrics:
- Counters: messages, failures, quarantine events
- Gauges: active channels, agents, queue depth
- Histograms: pipeline duration, Sg computation time

**circuit_breaker.py** — Resilience pattern:
- 3-state FSM (CLOSED/OPEN/HALF_OPEN)
- Prevents cascading failures
- Used for storage and bilateral channels

**timeout.py** — Time limits:
- Thread-based timeout enforcement
- Agent, pipeline, storage timeouts
- Automatic quarantine on timeout

### Performance Optimizations

**merkle.py** — Incremental Sg (⚠️ Breaking):
- Merkle tree for O(log N) Sg updates
- 10x faster than v0.1.x for 1000+ channels
- <1ms Sg computation

**frame.py** (enhanced) — Frame caching:
- LRU cache for deterministic frames
- 16-17x speedup for cache hits
- 90% hit rate in recovery scenarios

**transport.py** (enhanced) — Connection pooling:
- TCP connection reuse
- Idle eviction (5 min), lifetime limits (1 hour)
- Background cleanup task

### Security Hardening

**rotation.py** — Key rotation:
- X25519 DH-based rekey protocol
- Triggers: message count (1M), time (24h), manual
- Forward secrecy with ephemeral keypairs

**deduplication.py** — Replay protection:
- Per-channel message ID tracking
- O(1) duplicate detection
- Sliding window + TTL eviction

---

## Production Features Summary

| Feature | Module | Benefit |
|---------|--------|---------|
| Structured Logging | observability/logging.py | Request tracing, audit trail |
| Health Checks | observability/health.py | K8s integration, uptime monitoring |
| Metrics | observability/metrics.py | Performance monitoring, alerting |
| Circuit Breakers | observability/circuit_breaker.py | Resilience, failure isolation |
| Timeouts | observability/timeout.py | Prevents blocking, auto-quarantine |
| Merkle Tree | core/merkle.py | 10x faster Sg (O(log N)) |
| Frame Caching | core/frame.py | 16x speedup for recovery |
| Connection Pool | federation/transport.py | Reduced handshake overhead |
| Key Rotation | federation/rotation.py | Forward secrecy, key fatigue mitigation |
| Deduplication | runtime/deduplication.py | Replay attack prevention |

**Test Coverage:** 813 tests (591 unit, 191 integration, 27 E2E, 4 benchmark)

See [production-guide.md](./production-guide.md) for deployment details.

