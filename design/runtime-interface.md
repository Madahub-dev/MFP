# Runtime Interface Contract

```yaml
id: mfp-runtime-interface
type: spec
status: draft
created: 2026-03-10
revised: 2026-03-10
authors:
  - Akil Abderrahim
  - Claude Opus 4.6
tags: [runtime, interface, api, lifecycle, channels, quarantine]
```

## Table of Contents

1. Overview
2. Interface Layers
3. Agent-Facing Interface
4. Message Lifecycle
5. Agent Identity Model
6. Channel Model
7. Error Semantics
8. Quarantine Model
9. Runtime-Internal Operations
10. References

---

## 1. Overview

This document defines the API surface between the MFP runtime and everything it touches — agents, channels, and (for cross-runtime federation) other runtimes. It is the contract that an implementation must satisfy.

The spec (`spec.md`) defines the mathematical constructions. The threat model (`threat-model.md`) defines the security boundaries and isolation requirements. This document defines the operational interface: what agents can call, what the runtime does at each stage of the message lifecycle, how channels are managed, what happens when things fail, and how the runtime isolates misbehavior.

**Scope.** This document covers the single-runtime interface completely. Cross-runtime wire protocol and bilateral channel management are deferred to `federation.md`. Agent binding and unbinding lifecycle (state machine, transitions, seed derivation timing) are deferred to `agent-lifecycle.md`.

**Design principle.** The agent-facing interface is minimal. Agents send plaintext and receive plaintext. Everything between — framing, encoding, validation, decoding, ratchet advancement — is invisible. The interface exposes the smallest surface necessary for agents to participate in communication, and nothing more. This is not minimalism for aesthetics — it is minimalism for security. Every capability exposed to an agent is a capability a compromised agent can abuse (`threat-model.md` §3.2).

---

## 2. Interface Layers

The runtime interface has three layers, ordered by decreasing privilege:

```
┌──────────────────────────────────────────────┐
│  Layer 3: Runtime-Internal Operations        │
│  Frame derivation, encoding/decoding,        │
│  ratchet advancement, state management       │
│  (no external interface — runtime only)      │
├──────────────────────────────────────────────┤
│  Layer 2: Runtime Administration             │
│  Agent binding/unbinding, channel mgmt,      │
│  quarantine, configuration                   │
│  (operator interface — not agent-accessible) │
├──────────────────────────────────────────────┤
│  Layer 1: Agent-Facing Interface             │
│  send, receive, channel_info                 │
│  (the only interface agents see)             │
└──────────────────────────────────────────────┘
```

- **Layer 1** is defined in §3. It is the only interface agents interact with. It is intentionally small.
- **Layer 2** is exercised by the operator or orchestration layer that configures the runtime. It is not accessible to agents. It is defined implicitly in §5 (identity), §6 (channels), and §8 (quarantine). The full administration interface depends on the agent lifecycle (`agent-lifecycle.md`).
- **Layer 3** is the runtime's internal machinery. It is not an interface at all — no external caller invokes it. It is documented in §9 for implementers.

---

## 3. Agent-Facing Interface

### 3.1 Design Constraints

The agent-facing interface is governed by three constraints derived from the threat model (`threat-model.md` §2.2, §5.2):

1. **Agents never see frames, encoded payloads, ratchet state, encoding keys, or channel metadata beyond their own channel identifiers.** (B1 invariant — frame invisibility)
2. **Every agent output passes through the runtime before taking effect.** (Isolation property I1 — total mediation)
3. **Agent identity is unforgeable — the runtime determines the caller, not the caller's claim.** (Isolation property I6 — deterministic identity)

These constraints produce a minimal interface: agents submit plaintext, receive plaintext, and query their own channel list. Nothing else.

### 3.2 Tools

The runtime provisions exactly three protocol-level tools to bound agents. These are the agent's entire view of MFP.

**`mfp_send`**

```
mfp_send(channel: channel_id, payload: bytes) → receipt | error
```

Submit a raw plaintext payload for delivery on the specified channel.

| Parameter | Type | Description |
|-----------|------|-------------|
| `channel` | `channel_id` | Runtime-assigned channel identifier. Must be a channel the calling agent is bound to. |
| `payload` | `bytes` | Raw plaintext payload. Arbitrary length. The runtime will encode it. |

| Return | Description |
|--------|-------------|
| `receipt` | Confirmation that the message was accepted by the runtime for processing. Contains: `message_id` (runtime-assigned, opaque), `channel` (echo), `step` (the transaction step `t` at which this message was processed). |
| `error` | On failure. See §7 for error semantics. |

**What happens after `mfp_send` returns a receipt:**

The receipt confirms the runtime accepted the message. It does not confirm delivery to the destination agent. The runtime processes the message through the full lifecycle (§4) asynchronously. If processing fails (frame derivation error, encoding error), the runtime handles the failure internally — the sending agent is not notified of infrastructure failures beyond the initial acceptance.

**What `mfp_send` does NOT expose:**

- The frame that was derived.
- The encoded form of the payload.
- The ratchet state before or after advancement.
- The encoding algorithm or key used.
- Whether the message was delivered, or when.

**`mfp_channels`**

```
mfp_channels() → channel_list
```

Query the channels available to the calling agent.

| Return | Description |
|--------|-------------|
| `channel_list` | List of `channel_info` records for channels the calling agent is bound to. |

Each `channel_info` contains:

| Field | Type | Description |
|-------|------|-------------|
| `channel_id` | `channel_id` | The runtime-assigned channel identifier. |
| `peer` | `agent_id` | The identity of the other agent on this channel. |
| `status` | enum | One of: `active`, `quarantined`, `closed`. |

**What `mfp_channels` does NOT expose:**

- Ratchet state (`Sₗ`, `Sg`).
- Transaction step `t` (the agent does not need to track this — the runtime manages it).
- Frame depth `k`.
- Encoding algorithm or parameters.
- Channels belonging to other agents.

**`mfp_status`**

```
mfp_status() → agent_status
```

Query the calling agent's own protocol status.

| Return | Description |
|--------|-------------|
| `agent_status` | Record containing the agent's own identity and binding state. |

Each `agent_status` contains:

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | `agent_id` | The calling agent's runtime-assigned identity. |
| `state` | enum | The agent's lifecycle state (defined in `agent-lifecycle.md`). |
| `channel_count` | integer | Number of active channels. |

### 3.3 Message Delivery

Incoming messages are delivered to the agent by the runtime. This is not a tool call — it is the runtime pushing decoded content into the agent's execution context.

The delivered message contains:

| Field | Type | Description |
|-------|------|-------------|
| `payload` | `bytes` | The decoded plaintext payload. |
| `sender` | `agent_id` | The identity of the sending agent. Verified by the runtime — unforgeable. |
| `channel` | `channel_id` | The channel this message arrived on. |
| `message_id` | `message_id` | Runtime-assigned identifier for this message. |

**What the delivered message does NOT contain:**

- The frame.
- The encoded payload.
- The ratchet state or transaction step.
- Any metadata about the sending agent's runtime (for cross-runtime messages).

The delivery mechanism is implementation-dependent. In the LLM API model (the expected deployment), the runtime includes the message in the agent's next invocation context. In a long-running agent model, the runtime pushes the message through a callback or message queue internal to the host process. The protocol does not prescribe the delivery mechanism — only the content.

### 3.4 Tool Provisioning

Protocol tools (`mfp_send`, `mfp_channels`, `mfp_status`) are provisioned by the runtime at bind time. Unbound agents have no access to any protocol tool.

The runtime MAY provision additional application-level tools beyond the protocol tools. Application-level tools are outside MFP's scope — the protocol neither defines nor constrains them. However, all tool calls — protocol and application — pass through the runtime (isolation property I1), and the runtime MAY inspect, modify, or suppress any tool call.

Tool provisioning is revocable. The runtime removes protocol tools when an agent is unbound or quarantined (§8). Revocation is immediate — pending tool calls from a quarantined agent are rejected.

---

## 4. Message Lifecycle

### 4.1 Overview

Every message transits six stages. The runtime executes all six — agents participate only at stages 1 and 6.

```
Agent A            Runtime                              Agent B
  │                  │                                     │
  ├─ mfp_send() ───>│ 1. ACCEPT                          │
  │                  │ 2. FRAME    (derive + sample)       │
  │                  │ 3. ENCODE   (payload → E(P))       │
  │                  │ 4. VALIDATE (frame check)           │
  │                  │ 5. DECODE   (E(P) → P)             │
  │                  │ 6. DELIVER  (plaintext → Agent B) ──>│
  │                  │                                     │
```

For cross-runtime messages, the runtime inserts a TRANSIT stage between ENCODE and VALIDATE, where the assembled message crosses the bilateral channel. The receiving runtime performs VALIDATE, DECODE, and DELIVER.

### 4.2 Stage 1 — ACCEPT

**Trigger.** Agent calls `mfp_send(channel, payload)`.

**Runtime actions:**

1. Verify the calling agent is bound and not quarantined.
2. Verify `channel` is a valid channel the calling agent is bound to.
3. Verify the channel is active (not quarantined or closed).
4. Assign a `message_id`.
5. Return `receipt` to the calling agent.

**On failure:** Return `error` to the calling agent (§7). No state is modified.

### 4.3 Stage 2 — FRAME

**Trigger.** Successful ACCEPT.

**Runtime actions:**

1. Look up the channel's current local state `Sₗ` and transaction step `t`.
2. Look up the runtime's current global state `Sg` (or bilateral state `S_AB` for cross-runtime channels).
3. Derive the distribution: `D = F(t, Sₗ, Sg)` (`spec.md` §5.1–5.2).
4. Sample the frame: `frame ← D` (`spec.md` §5.3).
5. Construct `frame_open = (B₁, ..., Bₖ)` and `frame_close = mirror(frame_open)`.

**On failure:** Infrastructure error. The message is discarded. The runtime logs the failure (if audit logging is enabled). No state is modified. The sending agent is not notified — the receipt was already returned at ACCEPT. See §7.3 for infrastructure error semantics.

### 4.4 Stage 3 — ENCODE

**Trigger.** Successful FRAME.

**Runtime actions:**

1. Derive the encoding context: `ctx = (algorithm_id, key, channel_id, t)` (`spec.md` §6.3).
2. Derive the encoding key: `ctx.key = HMAC-SHA-256(key: Sₗ, message: "mfp-encoding-key" ‖ algorithm_id)` (`spec.md` §6.6).
3. Encode the payload: `E(P) = encode(P, ctx)` (`spec.md` §6.5).
4. Assemble the complete message: `M = frame_open ‖ E(P) ‖ frame_close`.

**Atomicity.** FRAME and ENCODE are atomic with respect to the message. If encoding fails, the frame is discarded. There is no state for "valid frame, unencoded payload" (`abstract.md` Design Decision 4).

**On failure:** Same as FRAME failure — infrastructure error, message discarded, no state modified.

### 4.5 Stage 4 — VALIDATE

**Trigger.** Assembled message `M` is ready for delivery (intra-runtime) or has been received from a bilateral channel (cross-runtime).

**Runtime actions:**

1. Extract `frame_open` (first `k` blocks) and `frame_close` (last `k` blocks) from `M`.
2. Verify structural mirror symmetry: `frame_close ≡ mirror(frame_open)` (`spec.md` §3.4).
3. Verify frame correctness: `frame_open` matches the expected sample from `D(t, Sₗ, Sg)`.
4. On valid frame, advance the ratchet: `Sₗ' = f(Sₗ, frame)` (`spec.md` §4.1).
5. Recompute global state: `Sg' = compose(...)` (`spec.md` §4.3).

**Atomicity.** Steps 4 and 5 (state advancement) are atomic (`spec.md` §4.4). If either fails, no state is modified.

**On failure:** The message is discarded. The payload never enters the destination agent's context. The runtime logs the failure. Repeated validation failures on a channel may trigger quarantine (§8).

**Intra-runtime note.** For messages within a single runtime, VALIDATE is a self-check — the same runtime generated and validates the frame. The check is still performed to maintain protocol invariants and detect internal corruption.

### 4.6 Stage 5 — DECODE

**Trigger.** Successful VALIDATE.

**Runtime actions:**

1. Reconstruct the encoding context `ctx` for the channel and step.
2. Decode the payload: `P = decode(E(P), ctx)` (`spec.md` §6.2).
3. If decode returns `⊥` (integrity failure), discard the message.

**On failure:** Message discarded. Integrity failure on a successfully framed message indicates tampering between ENCODE and DECODE — possible only for cross-runtime messages where a network adversary modified the payload in transit (`threat-model.md` §4.6). For intra-runtime messages, decode failure after successful validation is an internal error.

### 4.7 Stage 6 — DELIVER

**Trigger.** Successful DECODE.

**Runtime actions:**

1. Verify the destination agent is bound and not quarantined.
2. Construct the delivery record: `(payload: P, sender: agent_id, channel: channel_id, message_id: message_id)`.
3. Deliver the record to the destination agent through the implementation's delivery mechanism.

**On failure:** If the destination agent is quarantined or unbound, the message is discarded. The runtime logs the delivery failure.

### 4.8 Lifecycle Invariants

The following invariants hold across all stages:

1. **No partial messages.** A message either completes all six stages or is discarded entirely. There is no state for a partially processed message.
2. **No state modification on failure.** If any stage fails, ratchet state (`Sₗ`, `Sg`) is unchanged. State advances only on successful completion of VALIDATE (stage 4).
3. **Agent opacity.** Agents see only stages 1 (ACCEPT, via `mfp_send` return) and 6 (DELIVER, via message delivery). Stages 2–5 are invisible.
4. **Ordering.** Stages execute in strict sequence. No stage begins before the previous stage completes. There is no pipelining within a single message.

---

## 5. Agent Identity Model

### 5.1 Identity Assignment

Agent identity is assigned by the runtime at bind time. It is not derived from the agent's name, model, or any property the agent controls.

```
agent_id = runtime-assigned opaque identifier
```

The `agent_id` is:

- **Opaque.** It carries no semantic meaning. It is not a name, a role, or a description. It is a unique identifier within the runtime's scope.
- **Unforgeable.** An agent cannot claim a different `agent_id`. The runtime determines identity from the execution context — it knows which agent is calling because it controls the invocation (`threat-model.md` §5.2, I6).
- **Stable.** An agent's `agent_id` does not change during its binding lifetime. Unbinding and rebinding may produce a different `agent_id`.
- **Scoped.** An `agent_id` is unique within a single runtime. Cross-runtime agent references require qualification (deferred to `federation.md`).

### 5.2 Identity Visibility

Agents see their own `agent_id` (via `mfp_status`) and the `agent_id` of peers on their channels (via `mfp_channels` and delivered message metadata).

Agents do NOT see:

- Agent IDs of agents they have no channel with.
- The total number of agents in the runtime.
- Any mapping between `agent_id` and agent implementation details (model, provider, configuration).

### 5.3 Identity and Seed Derivation

The `agent_id` is an input to the seed derivation function (`spec.md` §4.2):

```
seed(agent_pair, channel_id) = HMAC-SHA-256(key: runtime_identity, message: agent_a ‖ agent_b ‖ channel_id)
```

Where `agent_a` and `agent_b` are the `agent_id` values of the two agents, lexicographically ordered. This binds the channel's ratchet state to the specific agent pair — a channel between agents A and B has a different ratchet trajectory than a channel between agents A and C, even if all other parameters are identical.

---

## 6. Channel Model

### 6.1 Channel Definition

A **channel** is a bidirectional communication path between exactly two agents, identified by a runtime-assigned `channel_id` and secured by its own ratchet state `Sₗ`.

```
channel = (channel_id, agent_a, agent_b, Sₗ, t, k, status)
```

| Field | Type | Description |
|-------|------|-------------|
| `channel_id` | opaque identifier | Runtime-assigned. Unique within the runtime. |
| `agent_a` | `agent_id` | First agent. Lexicographically ordered with `agent_b`. |
| `agent_b` | `agent_id` | Second agent. |
| `Sₗ` | 32-byte string | Current local ratchet state for this channel. |
| `t` | integer | Current transaction step. |
| `k` | integer, `k ≥ 2` | Frame depth for this channel. |
| `status` | enum | One of: `active`, `quarantined`, `closed`. |

### 6.2 Channel Establishment

Channels are established by the runtime, not by agents. An agent cannot request the creation of a channel — this is a Layer 2 (administration) operation.

**Establishment procedure:**

1. The runtime receives an instruction to establish a channel between two bound agents (from the operator, orchestration configuration, or policy engine).
2. The runtime verifies both agents are bound and not quarantined.
3. The runtime assigns a `channel_id`.
4. The runtime derives the initial ratchet state: `Sₗ₀ = seed(agent_pair, channel_id)` (`spec.md` §4.2).
5. The runtime sets `t = 0` and selects frame depth `k` (default: 4, minimum: 2 per `spec.md` §8.8).
6. The runtime sets channel status to `active`.
7. The runtime recomputes `Sg` to incorporate the new `Sₗ₀` (`spec.md` §4.3).
8. The channel is now available. Both agents see it in their `mfp_channels()` results.

**Agents cannot establish channels** because channel establishment is a trust decision. Which agents should communicate, with what frame depth, under what policies — these are deployment-level decisions that the runtime enforces, not choices agents make. A compromised agent that could create channels could reach any other agent in the runtime, violating channel scoping (`threat-model.md` §4.1).

### 6.3 Channel Addressing

Agents address messages by `channel_id`, not by destination `agent_id`. This is deliberate:

- The agent does not choose its peer — the channel already defines the peer.
- A `channel_id` is scoped to a specific agent pair. The agent cannot use a `channel_id` it is not bound to.
- Multiple channels may exist between the same agent pair (for different communication purposes). Each has its own `channel_id`, its own `Sₗ`, and its own `t`.

### 6.4 Channel Teardown

Channels are closed by the runtime, not by agents. Closure is a Layer 2 operation.

**Teardown procedure:**

1. The runtime sets channel status to `closed`.
2. Pending messages on the channel are discarded.
3. The runtime zeros the channel's ratchet state `Sₗ` in memory.
4. The runtime recomputes `Sg` without the closed channel's state.
5. The channel is removed from both agents' `mfp_channels()` results.

**State zeroing.** On closure, `Sₗ` is overwritten with zeros in memory. If the runtime persists state to disk, the persisted state for the closed channel is also zeroed. This ensures forward secrecy extends to channel teardown — a closed channel's ratchet state cannot be recovered from memory or storage.

### 6.5 Channel Lifecycle Summary

```
                establish
   (not exists) ─────────> active
                              │
                 quarantine   │   close
                ┌─────────────┤──────────> closed ──> (removed)
                │             │
                v             │
           quarantined ───────┘
                    restore
```

Transitions:
- `active → quarantined` — triggered by quarantine model (§8).
- `quarantined → active` — triggered by restore (§8.4). State is preserved — the channel resumes from its current `Sₗ` and `t`.
- `active → closed` — triggered by teardown (§6.4).
- `quarantined → closed` — triggered by teardown while quarantined.

---

## 7. Error Semantics

### 7.1 Error Classification

Errors in the runtime interface fall into three categories:

| Category | Source | Visible to Agent | Example |
|----------|--------|-------------------|---------|
| **Agent error** | Agent violates interface contract | Yes — returned from tool call | Sending on a channel the agent is not bound to |
| **Validation error** | Inbound message fails protocol checks | No — runtime handles internally | Frame mismatch, decode integrity failure |
| **Infrastructure error** | Runtime internal failure | No — runtime handles internally | CSPRNG failure, state corruption |

### 7.2 Agent Errors

Agent errors are returned to the calling agent as the `error` return from `mfp_send`. The error contains:

| Field | Type | Description |
|-------|------|-------------|
| `code` | enum | Error category (see table below). |
| `message` | string | Human-readable description. MUST NOT contain ratchet state, frame data, or any protocol-internal information. |

| Code | Condition |
|------|-----------|
| `UNBOUND` | The calling agent is not bound. |
| `QUARANTINED` | The calling agent is quarantined. |
| `INVALID_CHANNEL` | The specified `channel_id` does not exist or the agent is not bound to it. |
| `CHANNEL_CLOSED` | The channel exists but has been closed. |
| `CHANNEL_QUARANTINED` | The channel is quarantined. |
| `PAYLOAD_TOO_LARGE` | The payload exceeds the runtime's maximum payload size. |

**Error opacity.** Error codes are deliberately coarse. There is no `FRAME_DERIVATION_FAILED` or `ENCODE_FAILED` code visible to agents — these are infrastructure errors (§7.3). The agent learns only whether its request was accepted or rejected, and the reason for rejection is always something the agent can act on (use a different channel, wait, reduce payload size). No error leaks protocol-internal state (`threat-model.md` §4.10).

### 7.3 Validation Errors

Validation errors occur when an inbound message fails VALIDATE (§4.5) or DECODE (§4.6). The sending agent is not notified. The receiving agent never sees the message. The runtime:

1. Discards the message.
2. Logs the failure (channel, step, failure type) if audit logging is enabled. The log MUST NOT contain the invalid frame or payload — only the event metadata.
3. Increments the channel's failure counter (used by the quarantine model, §8).

### 7.4 Infrastructure Errors

Infrastructure errors are internal runtime failures: CSPRNG unavailability, memory allocation failure, state corruption, encoding library errors. The runtime:

1. Discards the affected message.
2. Logs the failure with full diagnostic detail (since infrastructure logs are not agent-accessible).
3. Does NOT advance ratchet state — the lifecycle invariant (§4.8.2) guarantees no state modification on failure.
4. If the infrastructure error is persistent (e.g., CSPRNG unavailable), the runtime SHOULD halt all message processing rather than operating in a degraded state. A runtime that cannot generate frame jitter is a runtime that cannot guarantee frame unpredictability.

---

## 8. Quarantine Model

### 8.1 Purpose

Quarantine isolates misbehaving agents or channels without destroying their state. It is a containment action — stopping further damage while preserving the ability to restore the agent or channel if the misbehavior is resolved.

Quarantine is distinct from closure (§6.4). A closed channel's state is zeroed. A quarantined channel's state is preserved — the channel can be restored to active status.

### 8.2 Quarantine Triggers

The runtime quarantines an agent or channel when it detects behavior that violates protocol expectations. Triggers fall into two categories:

**Automatic triggers** — the runtime detects these without operator intervention:

| Trigger | Scope | Condition |
|---------|-------|-----------|
| **Repeated validation failure** | Channel | A channel accumulates `n` consecutive validation failures. The threshold `n` is a runtime configuration parameter (recommended default: 3). |
| **Rate violation** | Agent | An agent exceeds the configured message rate limit. |
| **Payload constraint violation** | Agent | An agent repeatedly submits payloads exceeding size limits. |

**Manual triggers** — the operator instructs the runtime:

| Trigger | Scope | Condition |
|---------|-------|-----------|
| **Operator quarantine** | Agent or channel | The operator explicitly quarantines an agent or channel via Layer 2 administration. |

### 8.3 Quarantine Effects

**Agent quarantine:**

1. All protocol tools (`mfp_send`, `mfp_channels`, `mfp_status`) are revoked. Pending tool calls return `QUARANTINED`.
2. All channels the agent participates in are also quarantined.
3. Messages in transit to the quarantined agent are discarded.
4. The agent's execution may continue (the runtime does not necessarily terminate the agent process), but it cannot participate in any protocol communication.

**Channel quarantine:**

1. Messages on the channel are rejected at ACCEPT (§4.2) with `CHANNEL_QUARANTINED`.
2. The channel's ratchet state `Sₗ` and step `t` are frozen — no advancement occurs.
3. `Sg` is NOT recomputed on quarantine — the quarantined channel's last `Sₗ` remains in the composition. This is deliberate: removing the channel's state from `Sg` would change the global state and affect all other channels' frame derivation, creating a protocol-visible signal that a quarantine occurred.
4. Both agents on the channel see status `quarantined` in `mfp_channels()`.

### 8.4 Quarantine Restoration

Restoration returns a quarantined agent or channel to active status. It is a Layer 2 (operator) operation — agents cannot self-restore.

**Agent restoration:**

1. The operator instructs the runtime to restore the agent.
2. Protocol tools are re-provisioned.
3. All channels the agent participates in are evaluated — channels quarantined solely due to the agent's quarantine are restored. Channels quarantined independently (e.g., repeated validation failure) remain quarantined.

**Channel restoration:**

1. The operator instructs the runtime to restore the channel.
2. The channel resumes from its current `Sₗ` and `t`. No state is reset — the ratchet continues from where it was frozen.
3. Both agents see status `active` in `mfp_channels()`.

**No automatic restoration.** The runtime does not automatically restore quarantined agents or channels. Quarantine is a containment action that requires operator judgment to resolve. Automatic restoration would allow a compromised agent to cycle between quarantine and active status indefinitely.

### 8.5 Quarantine vs. Closure Decision

| | Quarantine | Closure |
|--|-----------|---------|
| **State preserved** | Yes — `Sₗ`, `t` frozen | No — `Sₗ` zeroed |
| **Restorable** | Yes — operator can restore | No — channel is destroyed |
| **Use case** | Suspected misbehavior, investigation, temporary isolation | Permanent removal, agent unbinding, confirmed compromise |
| **`Sg` impact** | None — quarantined state stays in composition | Recomputed without closed channel |

---

## 9. Runtime-Internal Operations

This section documents the runtime's internal operations for implementers. These are NOT callable by agents or external systems. They are the mechanisms the runtime executes during the message lifecycle (§4).

### 9.1 Frame Derivation

Implements `spec.md` §5.1–5.3. Called during FRAME stage (§4.3).

```
derive_frame(channel) → frame

  1. ds = HMAC-SHA-256(key: channel.Sₗ, message: encode_u64_be(channel.t) ‖ Sg)
  2. prng = ChaCha20(seed: ds)
  3. For i = 1 to channel.k:
       candidate = prng.next_bytes(16)
       jitter = os_csprng(16)          // or shared_prng for cross-runtime
       Bᵢ = candidate XOR jitter
  4. frame = (B₁, ..., Bₖ)
  5. Return frame
```

### 9.2 State Advancement

Implements `spec.md` §4.4. Called during VALIDATE stage (§4.5) after successful validation.

```
advance_state(channel, frame) → void

  1. channel.Sₗ = HMAC-SHA-256(key: channel.Sₗ, message: B₁ ‖ ... ‖ Bₖ)
  2. channel.t = channel.t + 1
  3. Sg = compose(all channel Sₗ values in canonical order)
```

Steps 1–3 are atomic. If any step fails, all are rolled back.

### 9.3 Encoding and Decoding

Implements `spec.md` §6.5. Called during ENCODE (§4.4) and DECODE (§4.6) stages.

```
encode_payload(channel, payload) → encoded

  1. key = HMAC-SHA-256(key: channel.Sₗ, message: "mfp-encoding-key" ‖ algorithm_id)
  2. nonce = HMAC-SHA-256(key: key, message: channel.channel_id ‖ encode_u64_be(channel.t))[:12]
  3. aad = channel.channel_id ‖ encode_u64_be(channel.t)
  4. encoded = AES-256-GCM-Encrypt(key: key, nonce: nonce, plaintext: payload, aad: aad)
  5. Return encoded
```

```
decode_payload(channel, encoded) → payload | ⊥

  1. key = HMAC-SHA-256(key: channel.Sₗ, message: "mfp-encoding-key" ‖ algorithm_id)
  2. nonce = HMAC-SHA-256(key: key, message: channel.channel_id ‖ encode_u64_be(channel.t))[:12]
  3. aad = channel.channel_id ‖ encode_u64_be(channel.t)
  4. payload | ⊥ = AES-256-GCM-Decrypt(key: key, nonce: nonce, ciphertext: encoded, aad: aad)
  5. Return payload | ⊥
```

### 9.4 Message Assembly

Implements `spec.md` §3.5. Called at the end of ENCODE stage (§4.4).

```
assemble(frame, encoded_payload) → M

  1. frame_open = (B₁, ..., Bₖ)
  2. frame_close = (reverse(Bₖ), ..., reverse(B₁))
  3. M = frame_open ‖ encoded_payload ‖ frame_close
  4. Return M
```

### 9.5 Frame Validation

Implements `spec.md` §3.4 and §5. Called during VALIDATE stage (§4.5).

```
validate_frame(M, channel) → valid | invalid

  1. Extract frame_open = first k blocks of M
  2. Extract frame_close = last k blocks of M
  3. If frame_close ≢ mirror(frame_open): return invalid
  4. Derive expected frame using derive_frame(channel)
  5. If frame_open ≢ expected_frame: return invalid      // constant-time comparison
  6. Return valid
```

Step 5 MUST use constant-time comparison to prevent timing side channels (`threat-model.md` §4.10).

**Intra-runtime optimization.** For intra-runtime messages, the runtime holds the generated frame in memory from FRAME through VALIDATE. Step 4 (re-derivation) can be skipped — the runtime compares against the frame it generated. This is an optimization, not a semantic change. Cross-runtime messages always require full re-derivation.

---

## 10. References

| Ref | Document | Relevance |
|-----|----------|-----------|
| [1] | `abstract.md` | Architecture, design decisions, runtime responsibilities |
| [2] | `spec.md` | Mathematical constructions implemented by §9, security bounds governing §7–8 |
| [3] | `threat-model.md` | Trust boundaries (§2), isolation requirements (§5), attack vectors informing error opacity (§7) |
| [4] | `agent-lifecycle.md` | Agent binding/unbinding state machine, seed derivation timing, tool provisioning lifecycle |
| [5] | `federation.md` | Cross-runtime bilateral channel management, wire protocol for TRANSIT stage |

---

*Mada OS — authored by Akil Abderrahim and Claude Opus 4.6*
