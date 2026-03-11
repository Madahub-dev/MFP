# Multi-Runtime Federation Protocol

```yaml
id: mfp-federation
type: spec
status: complete
created: 2026-03-10
revised: 2026-03-10
authors:
  - Akil Abderrahim
  - Claude Opus 4.6
tags: [federation, multi-runtime, bilateral, wire-format, recovery]
```

## Table of Contents

1. Overview
2. Federation Model
3. Bilateral Channel Bootstrap
4. Cross-Organizational Trust Ceremony
5. Wire Format
6. Cross-Runtime Message Lifecycle
7. Bilateral State Recovery
8. Federation Topology
9. Runtime Identity
10. Security Considerations
11. References

---

## 1. Overview

This document defines the wire protocol and operational procedures for cross-runtime communication in the Mirror Frame Protocol. Where `spec.md` §7 defines the tensor structure and bilateral state operations at the mathematical level, this document defines how two runtimes actually establish contact, exchange messages, recover from failures, and manage the bilateral relationship.

**Scope.** This document completes the multi-runtime extension. It covers:

- How two runtimes bootstrap a bilateral channel (deterministic seeding and cross-organizational ceremony).
- The wire format for framed messages transmitted between runtimes.
- The cross-runtime message lifecycle (TRANSIT stage inserted between ENCODE and VALIDATE from `runtime-interface.md` §4).
- State recovery after network partition, crash, or divergence.
- Federation topology constraints (or lack thereof).

**Dependencies.** This document depends on:

- `spec.md` §7 — tensor structure, bilateral state, advancement protocol.
- `runtime-interface.md` §4 — message lifecycle (this document extends it with TRANSIT).
- `threat-model.md` §3.4–3.5 — compromised runtime and network adversary classes.
- `threat-model.md` §4.12 — state divergence attack analysis.

---

## 2. Federation Model

### 2.1 Principles

Federation in MFP follows three principles:

**Runtime sovereignty.** Each runtime is the sole authority over its own agents, channels, and internal state. No remote runtime can bind, unbind, quarantine, or inspect agents on another runtime. Cross-runtime communication is between runtimes, not between agents on different runtimes — the runtimes act as proxies for their agents.

**Bilateral isolation.** Cross-runtime frame derivation uses the bilateral state `S_AB`, not either runtime's internal `Sg` (`spec.md` §7.6). No internal state is exposed through federation. A runtime's participation in federation does not weaken the security of its internal channels.

**Symmetric authority.** Neither runtime in a bilateral relationship has privilege over the other. Both contribute equally to `S_AB`. Both must agree to advance state. Neither can force the other to accept a message or advance.

### 2.2 Cross-Runtime Channel Architecture

A cross-runtime channel connects an agent on Runtime A to an agent on Runtime B. From the agents' perspective, the channel looks identical to an intra-runtime channel — the agents see `mfp_send`, `mfp_channels`, and decoded plaintext. The cross-runtime machinery is invisible.

```
Agent X         Runtime A              Network              Runtime B         Agent Y
   │                │                     │                     │                │
   ├─ mfp_send ───>│                     │                     │                │
   │                ├─ FRAME (Sₗ, S_AB)  │                     │                │
   │                ├─ ENCODE            │                     │                │
   │                ├─ ASSEMBLE          │                     │                │
   │                ├─── TRANSIT ────────>│────────────────────>│                │
   │                │                     │                     ├─ VALIDATE      │
   │                │                     │                     ├─ DECODE        │
   │                │                     │                     ├─ DELIVER ─────>│
   │                │                     │                     │                │
```

The channel has two layers of ratchet state:

- **`Sₗ`** — the channel's local ratchet state, maintained independently by each runtime. Both runtimes derive the same `Sₗ₀` from the seed (`spec.md` §4.2) and advance in lockstep through bilateral acknowledgment.
- **`S_AB`** — the bilateral ratchet state, jointly maintained by both runtimes. Used in place of `Sg` for frame derivation (`spec.md` §7.6).

### 2.3 Bilateral Channel vs. Agent Channel

A bilateral channel (`S_AB`) is not the same as an agent channel (`Sₗ`):

| | Bilateral Channel | Agent Channel |
|--|-------------------|---------------|
| **Scope** | Runtime pair | Agent pair |
| **State** | `S_AB = (ratchet_state, shared_prng_seed)` | `Sₗ` (32 bytes) |
| **Participants** | Two runtimes | Two agents |
| **Multiplexing** | Carries all agent channels between the runtime pair | Carries messages for one agent pair |
| **Establishment** | Bootstrap (§3) or ceremony (§4) | Channel establishment (`agent-lifecycle.md` §5) |

A single bilateral channel between Runtime A and Runtime B carries messages for all agent pairs that span those two runtimes. The bilateral state `S_AB` advances with every cross-runtime message regardless of which agent pair sent it. Individual agent channels have their own `Sₗ` that also advances independently.

---

## 3. Bilateral Channel Bootstrap

### 3.1 Same Trust Domain

When two runtimes are within the same trust domain (same organization, same deployment, same administrative authority), the bilateral channel is bootstrapped deterministically with no ceremony:

```
S_AB₀.ratchet_state    = HMAC-SHA-256(key: "mfp-bilateral", message: runtime_a ‖ runtime_b)
S_AB₀.shared_prng_seed = HMAC-SHA-256(key: "mfp-bilateral-prng", message: runtime_a ‖ runtime_b)
```

(`spec.md` §7.4)

Where `runtime_a` and `runtime_b` are the canonical runtime identifiers, lexicographically ordered.

**Preconditions:**

- Both runtimes know each other's `runtime_identity`.
- Both runtimes are administered by the same authority (or authorities that share a pre-existing trust relationship).
- The runtime identifiers are authentic — neither is impersonated.

**Procedure:**

1. Runtime A computes `S_AB₀` from its own identity and Runtime B's identity.
2. Runtime B computes `S_AB₀` from the same inputs (lexicographic ordering ensures identical results).
3. Both runtimes incorporate `S_AB₀.ratchet_state` into their respective `Sg`.
4. The bilateral channel is active. Either runtime can send the first message.

No network exchange is required for bootstrap. Both runtimes derive the same initial state independently. The first message on the bilateral channel is the first network interaction.

### 3.2 Bootstrap Security

Deterministic bootstrap provides no initial secrecy — the seed inputs (`runtime_a`, `runtime_b`) may be public. This is by design (`abstract.md` Design Decision 5): security is not front-loaded into seed entropy but accumulates through the one-way chaining of subsequent exchanges. After a single exchange, the bilateral state has been folded through HMAC-SHA-256 with a frame containing OS CSPRNG jitter (or shared PRNG output), making recovery of `S_AB₀` from `S_AB₁` computationally infeasible.

**Threat exposure during bootstrap.** An attacker who knows both runtime identifiers can compute `S_AB₀` and derive the first frame. This means the first message on a deterministically bootstrapped bilateral channel has no confidentiality or integrity against an attacker who knows the runtime identifiers. After the first successful exchange, forward secrecy applies — the attacker cannot derive `S_AB₁` without having observed the frame that was folded in.

**Mitigation.** For deployments where the bootstrap window is unacceptable, the cross-organizational trust ceremony (§4) provides initial secrecy.

---

## 4. Cross-Organizational Trust Ceremony

### 4.1 When Required

The deterministic bootstrap (§3) is insufficient when:

- The two runtimes are administered by different organizations with no pre-existing trust.
- The runtime identifiers are transmitted through channels where impersonation is possible.
- The deployment requires confidentiality from the first message (no bootstrap exposure window).

In these cases, the bilateral channel requires a trust ceremony that establishes shared secret material before the first message.

### 4.2 Ceremony Protocol

The ceremony establishes an initial bilateral state with secrecy, using an out-of-band key exchange.

**Phase 1 — Identity Exchange.**

Both runtimes exchange their `runtime_identity` values through an authenticated out-of-band channel. The channel must provide:

- **Authenticity** — each side is confident the identity came from the claimed runtime, not an impersonator.
- **Integrity** — the identity was not modified in transit.

The channel need not provide confidentiality — runtime identifiers are not secret. Examples: signed email, in-person exchange, authenticated API call through an existing TLS relationship.

**Phase 2 — Secret Establishment.**

Both runtimes perform a Diffie-Hellman key exchange to establish shared secret material:

```
Runtime A:
  a ← random scalar
  A_pub = g^a mod p

Runtime B:
  b ← random scalar
  B_pub = g^b mod p

Exchange: A_pub and B_pub through the authenticated channel.

Shared secret:
  Runtime A: shared = B_pub^a mod p
  Runtime B: shared = A_pub^b mod p
  (Both derive the same shared value.)
```

The DH parameters (`g`, `p`) are protocol constants. The specification uses the 2048-bit MODP group from RFC 3526, Group 14. Implementations MAY use X25519 (RFC 7748) as an alternative — the ceremony protocol is agnostic to the specific key agreement scheme provided it produces a shared secret of at least 256 bits.

**Phase 3 — Bilateral State Derivation.**

The shared secret is mixed with the deterministic seed to produce the initial bilateral state:

```
S_AB₀.ratchet_state    = HMAC-SHA-256(key: shared, message: "mfp-bilateral" ‖ runtime_a ‖ runtime_b)
S_AB₀.shared_prng_seed = HMAC-SHA-256(key: shared, message: "mfp-bilateral-prng" ‖ runtime_a ‖ runtime_b)
```

This is the same derivation as §3.1 but with the shared secret as the HMAC key instead of the fixed string. The shared secret provides initial confidentiality — an attacker who knows the runtime identifiers but not the shared secret cannot compute `S_AB₀`.

**Phase 4 — Confirmation.**

Both runtimes exchange a confirmation message: the first framed message on the bilateral channel. If both sides can validate the other's frame, the ceremony succeeded — both derived the same `S_AB₀`.

If validation fails, the ceremony has failed. Both runtimes discard `S_AB₀` and the ceremony must be restarted. The failure indicates either a key exchange error or an active attacker. The runtime SHOULD log the failure and alert the operator.

### 4.3 Ceremony Security Properties

| Property | Guarantee |
|----------|-----------|
| **Initial secrecy** | `S_AB₀` is secret — derived from DH shared secret unknown to observers. |
| **Forward secrecy** | Same as deterministic bootstrap — one-way ratchet advancement after ceremony. |
| **Mutual authentication** | Both runtimes verify the other's identity through the authenticated channel (Phase 1). |
| **Resistance to MITM** | Depends on the authenticated channel in Phase 1. If the identity exchange is intercepted and modified, the DH exchange is compromised. The authenticated channel is the trust anchor. |

---

## 5. Wire Format

### 5.1 Purpose

The wire format defines how a framed, encoded message is serialized for transmission between runtimes. It is the byte-level representation of a complete protocol message `M` as it crosses boundary B2 (`threat-model.md` §2.3).

### 5.2 Message Envelope

A cross-runtime message is wrapped in an envelope that provides the metadata the receiving runtime needs to validate and route the message:

```
┌──────────────────────────────────────────────────┐
│  Envelope Header (fixed size: 64 bytes)          │
├──────────────────────────────────────────────────┤
│  Protocol Message M (variable size)              │
│  = frame_open ‖ E(P) ‖ frame_close              │
└──────────────────────────────────────────────────┘
```

### 5.3 Envelope Header

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | `magic` | Protocol identifier: `0x4D465031` (ASCII "MFP1"). |
| 4 | 2 | `version` | Protocol version: `0x0001` for this specification. |
| 6 | 2 | `flags` | Bit flags (see §5.4). |
| 8 | 4 | `frame_depth` | Frame depth `k` (uint32, big-endian). |
| 12 | 4 | `payload_len` | Length of `E(P)` in bytes (uint32, big-endian). |
| 16 | 16 | `channel_id` | The cross-runtime channel identifier. |
| 32 | 8 | `step` | Transaction step `t` (uint64, big-endian). |
| 40 | 16 | `sender_runtime` | First 16 bytes of the sending runtime's identity (for routing, not authentication). |
| 56 | 8 | `reserved` | Reserved for future use. Must be zero. |

Total header size: 64 bytes (fixed).

Total message size: `64 + 2k·b + |E(P)|` bytes, where `b = 16` (block size).

### 5.4 Flags

| Bit | Name | Description |
|-----|------|-------------|
| 0 | `ACK` | This message is a response (implicit acknowledgment). The sender has advanced `S_AB` based on the prior message. |
| 1 | `RECOVERY` | This message is part of the state recovery protocol (§7). |
| 2–15 | Reserved | Must be zero. |

### 5.5 Validation of Envelope

The receiving runtime validates the envelope before processing the protocol message:

1. Verify `magic` equals `0x4D465031`. Reject otherwise.
2. Verify `version` is supported. Reject otherwise.
3. Verify `reserved` is zero. Reject otherwise.
4. Verify `frame_depth` matches the expected depth for this bilateral channel. Reject otherwise.
5. Verify `channel_id` is a known cross-runtime channel. Reject otherwise.
6. Verify `step` is the expected next step for this channel. Reject otherwise (but see §7 for recovery procedures).
7. Extract `M` using `frame_depth` and `payload_len` to determine boundaries.

Envelope validation is cheap — all checks are against known values. An attacker flooding a runtime with garbage envelopes is rejected at step 1 (magic check) with minimal processing.

### 5.6 No Encryption of Wire Format

The wire format does not add a transport encryption layer. The protocol message `M` already contains an encrypted payload (`E(P)` via AES-256-GCM) and frame blocks that are meaningless without ratchet state. The envelope header fields (`channel_id`, `step`, `sender_runtime`) are metadata that a network observer could infer from traffic patterns anyway.

Deployments that require confidentiality of envelope metadata (hiding which channel a message belongs to, which step it's at) SHOULD use transport-level encryption (TLS, WireGuard, etc.) beneath the wire format. This is a deployment decision, not a protocol requirement — consistent with the traffic analysis position in `threat-model.md` §6.1.

---

## 6. Cross-Runtime Message Lifecycle

### 6.1 Extended Lifecycle

The single-runtime lifecycle (`runtime-interface.md` §4) has six stages: ACCEPT → FRAME → ENCODE → VALIDATE → DECODE → DELIVER. For cross-runtime messages, the lifecycle splits across two runtimes with a TRANSIT stage between them:

```
Sending Runtime                                 Receiving Runtime
  1. ACCEPT     (same as single-runtime)
  2. FRAME      (uses S_AB, shared PRNG)
  3. ENCODE     (same as single-runtime)
  T. TRANSIT    (serialize, transmit)
                                                  4. VALIDATE  (uses S_AB, shared PRNG)
                                                  5. DECODE    (same as single-runtime)
                                                  6. DELIVER   (same as single-runtime)
```

### 6.2 Stage T — TRANSIT

**Trigger.** Successful ENCODE on the sending runtime.

**Sending runtime actions:**

1. Construct the envelope header (§5.3):
   - Set `magic`, `version`, `flags`.
   - Set `frame_depth` to the channel's `k`.
   - Set `payload_len` to `|E(P)|`.
   - Set `channel_id` to the cross-runtime channel identifier.
   - Set `step` to the current `t`.
   - Set `sender_runtime` to the first 16 bytes of its own identity.
   - Set `ACK` flag if this message is a response to a prior received message.
2. Concatenate: `envelope_header ‖ M`.
3. Transmit to the receiving runtime.

**Receiving runtime actions:**

1. Receive the byte stream.
2. Validate the envelope (§5.5).
3. Extract `M` from the envelope.
4. Proceed to VALIDATE (stage 4) using the bilateral state `S_AB`.

### 6.3 Frame Generation — Cross-Runtime

On the sending side, frame generation uses a per-step PRNG derived from the shared seed and the transaction step, instead of OS CSPRNG for jitter (`spec.md` §5.4):

```
jitter_seed = HMAC-SHA-256(key: S_AB.shared_prng_seed, message: encode_u64_be(t))
jitter_prng = ChaCha20(seed: jitter_seed)

For i = 1 to k:
    candidate = prng.next_bytes(16)                  // from ds, deterministic
    jitter = jitter_prng.next_bytes(16)              // from per-step PRNG
    Bᵢ = candidate XOR jitter
```

The `jitter_prng` is ephemeral — derived fresh for each frame derivation from `(shared_prng_seed, t)` and discarded after sampling. Both runtimes derive the same frame because both know `S_AB.shared_prng_seed` and `t`. This construction is idempotent: deriving the frame multiple times at the same step produces the same result, eliminating PRNG synchronization as a failure mode. Failed sends, retransmissions, and recovery attempts at the same step all produce identical frames without consuming shared mutable state.

### 6.4 State Advancement — Cross-Runtime

Cross-runtime state advancement follows the implicit acknowledgment model (`spec.md` §7.5):

```
1. Runtime A sends message M at step t, framed with S_AB.
   Runtime A does NOT advance S_AB yet.
   Runtime A records: pending_advance = (S_AB, frame, t).

2. Runtime B receives M, validates frame against S_AB.
   On success:
     Runtime B advances: S_AB' = advance(S_AB, frame)
     Runtime B advances: Sₗ' = f(Sₗ, frame)
     Runtime B recomputes Sg_B.

3. Runtime B sends response M' at step t', framed with S_AB'.
   Runtime B does NOT advance S_AB' yet (awaits A's implicit ack).

4. Runtime A receives M', validates frame against pending S_AB'.
   On success:
     Runtime A advances: S_AB' (confirms pending_advance)
     Runtime A advances: Sₗ' = f(Sₗ, frame_from_M')
     Runtime A recomputes Sg_A.
```

**Key property.** Neither runtime advances `S_AB` until it has evidence that the other side also advanced. Runtime B advances on receiving a valid frame from A. Runtime A advances on receiving a valid response from B. This is the protocol's built-in protection against unilateral divergence.

### 6.5 Unacknowledged Messages

If Runtime A sends a message and receives no response:

1. `S_AB` has not advanced on Runtime A (it was pending).
2. Runtime A does not know whether Runtime B received the message.
3. Runtime A MAY retransmit the same message (same frame, same payload) after a timeout. The retransmission uses the same `S_AB` and `t` — it is idempotent from the protocol's perspective.
4. If Runtime B already received and processed the original, the retransmission's frame will not match Runtime B's advanced state. Runtime B recognizes this as a duplicate (step `t` already processed) and responds with its already-computed response.

The timeout and retry policy is implementation-defined. The protocol defines the idempotency semantics — retransmission of the same `(S_AB, t, frame)` is safe.

---

## 7. Bilateral State Recovery

### 7.1 Divergence Scenarios

Bilateral state divergence occurs when the two runtimes disagree on the current `S_AB`. This is the core problem identified in `threat-model.md` §4.12.

**Scenario 1 — Dropped message.** Runtime A sends, Runtime B never receives. Neither side advances. `S_AB` remains consistent. Recovery: Runtime A retransmits (§6.5).

**Scenario 2 — Dropped response.** Runtime A sends, Runtime B receives and advances to `S_AB'`. Runtime B's response is lost. Runtime A remains at `S_AB`. The states have diverged.

**Scenario 3 — Runtime crash.** Runtime B receives A's message, advances to `S_AB'`, then crashes before persisting state. On restart, Runtime B is at `S_AB`. Meanwhile, Runtime A received B's response (before crash) and advanced. Runtime A is at `S_AB'`. The states have diverged — but in the opposite direction from Scenario 2.

**Scenario 4 — Simultaneous send.** Both runtimes send a message to each other at the same step, each expecting the other to receive first. Both frames are derived from the same `S_AB` but neither side expects the other's message.

### 7.2 Recovery Protocol

Recovery uses a three-phase protocol: DETECT → NEGOTIATE → RESYNC.

**Phase 1 — DETECT.**

Divergence is detected when a received frame fails validation but the envelope passes all checks (§5.5) — the message appears well-formed but the frame doesn't match. This distinguishes divergence from garbage or forgery:

- Garbage: fails envelope validation (wrong magic, unknown channel). Discarded.
- Forgery: passes envelope, fails frame. Could be divergence or attack. Proceed to negotiation.
- Divergence: passes envelope, fails frame, `RECOVERY` flag indicates peer is attempting recovery.

On frame validation failure for a cross-runtime message, the runtime enters recovery mode for that bilateral channel:

1. Suspend normal message processing on the bilateral channel.
2. Set the `RECOVERY` flag on all outbound messages.
3. Begin Phase 2.

**Phase 2 — NEGOTIATE.**

Both runtimes exchange their current state position to determine who is ahead:

```
Recovery message:
  channel_id:    the bilateral channel
  step:          the runtime's current step t for this channel
  state_hash:    SHA-256(S_AB.ratchet_state ‖ S_AB.shared_prng_seed)
  flags:         RECOVERY
```

The `state_hash` allows comparison without revealing the actual state. If both runtimes send recovery messages, they compare:

| Condition | Diagnosis | Resolution |
|-----------|-----------|------------|
| Same step, same hash | States are consistent. Spurious detection. | Exit recovery. Resume normal operation. |
| Same step, different hash | Corruption or attack. | Escalate to operator. Cannot auto-recover. |
| Step differs by 1 | Scenario 2 or 3. One side advanced, the other didn't. | The behind runtime advances (Phase 3). |
| Step differs by > 1 | Extended partition or repeated failure. | Escalate to operator if gap exceeds configured threshold. Otherwise, iterate Phase 3. |

**Phase 3 — RESYNC.**

The behind runtime must advance to match the ahead runtime. The ahead runtime retransmits the message(s) that the behind runtime missed:

1. The ahead runtime retransmits the message at the disputed step, with `RECOVERY` flag set.
2. The behind runtime processes the retransmitted message: validates frame, decodes, advances state.
3. After advancement, both runtimes exchange a recovery confirmation (a normal framed message at the new step).
4. If confirmation validates on both sides, recovery is complete. Resume normal operation.
5. If confirmation fails, repeat from Phase 2. If repeated failures exceed a configured threshold, escalate to operator.

### 7.3 Recovery Limits

The recovery protocol has configurable limits to prevent unbounded retry loops:

| Parameter | Description | Recommended Default |
|-----------|-------------|---------------------|
| `max_step_gap` | Maximum step divergence before operator escalation | 5 |
| `max_recovery_attempts` | Maximum Phase 2–3 iterations before escalation | 3 |
| `recovery_timeout` | Maximum time in recovery mode before escalation | 30 seconds |

When any limit is exceeded, the runtime:

1. Suspends the bilateral channel.
2. Alerts the operator.
3. Waits for operator instruction: manual resync, channel reset, or channel teardown.

### 7.4 Channel Reset

As a last resort, the operator may instruct both runtimes to reset the bilateral channel. Reset re-derives `S_AB` from scratch:

```
S_AB₀ = derive from ceremony or deterministic bootstrap (§3 or §4)
```

This is a destructive operation:

- All ratchet state accumulated through prior exchanges is lost.
- Forward secrecy for the channel's history is preserved (the old state was one-way — resetting doesn't reveal it).
- But the reset state has the same bootstrap exposure as a new channel (§3.2).
- All agent channels that span this bilateral channel are also reset — their `Sₗ` must be re-seeded.

Channel reset MUST be a manual operator decision. The protocol does not auto-reset.

### 7.5 Persistence Requirements

To support recovery, runtimes MUST persist bilateral state to durable storage:

- `S_AB` (both `ratchet_state` and `shared_prng_seed`).
- The current step `t` for each cross-runtime channel.
- Pending unacknowledged messages (for retransmission after crash recovery).

Persistence MUST be atomic — the runtime either persists the complete advanced state or does not persist at all. Partial persistence (advancing `ratchet_state` but not `shared_prng_seed`) would produce internal inconsistency that cannot be recovered.

Persisted state MUST be encrypted at rest. The encryption key should not be derived from the bilateral state itself (circular dependency). Standard infrastructure key management applies.

---

## 8. Federation Topology

### 8.1 Protocol Agnosticism

MFP does not constrain federation topology. The bilateral channel model supports any arrangement:

**Mesh.** Every runtime has a bilateral channel with every other runtime. `n` runtimes produce `n(n-1)/2` bilateral channels. Maximum connectivity, maximum state overhead.

```
    A ──── B
    │ \  / │
    │  \/  │
    │  /\  │
    │ /  \ │
    C ──── D
```

**Hub-and-spoke.** One central runtime has bilateral channels with all others. Peripheral runtimes communicate through the hub. Minimal state overhead, single point of failure.

```
    A       B
     \     /
      \   /
       Hub
      /   \
     /     \
    C       D
```

**Hierarchical.** Runtimes are organized in a tree. Each runtime has a bilateral channel with its parent and children. Messages between non-adjacent runtimes transit through intermediate runtimes.

```
        Root
       /    \
      A      B
     / \      \
    C   D      E
```

**Hybrid.** Any combination. The protocol does not care — each bilateral channel is independent. The topology is a deployment decision.

### 8.2 Multi-Hop Routing

When two runtimes do not share a direct bilateral channel, messages must be routed through intermediate runtimes. MFP does not define a routing protocol — routing is a deployment concern. However, the protocol defines what happens at each hop:

1. Runtime A frames and encodes the message for its bilateral channel with Runtime X (the next hop).
2. Runtime X receives, validates frame, decodes payload.
3. Runtime X re-frames and re-encodes the payload for its bilateral channel with the destination (or next hop).
4. Each hop produces a new frame and new encoding — the message is not passed through transparently.

**Security implication.** Each intermediate runtime sees the decoded payload. Multi-hop routing through untrusted intermediate runtimes exposes payload content. For end-to-end confidentiality across hops, the source runtime would need to apply an additional encryption layer keyed to the destination runtime. This end-to-end layer is outside MFP's current scope — the protocol provides hop-by-hop security, not end-to-end.

### 8.3 Topology and Global State

Federation topology affects `Sg` through the bilateral state contributions:

```
Sg_A = compose(all internal Sₗ, S_AB, S_AC, ...)
```

Each bilateral channel adds one state contribution to `Sg`. A runtime with many bilateral channels has a richer `Sg` (more entropy sources), but also a higher recomputation cost. The tradeoff is linear — each bilateral channel adds one 32-byte value to the `compose` input.

---

## 9. Runtime Identity

### 9.1 Requirements

Runtime identity serves two purposes in federation:

1. **Seed derivation.** The bilateral seed (`spec.md` §7.4) and agent channel seeds (`spec.md` §4.2) use `runtime_identity` as input.
2. **Envelope routing.** The wire format envelope (§5.3) includes `sender_runtime` for routing.

Runtime identity must be:

- **Stable.** The identity does not change during the runtime's lifetime. Changing identity would invalidate all bilateral state derived from it.
- **Unique.** No two runtimes share the same identity. Collisions would produce identical bilateral seeds, breaking channel separation.
- **Deterministic.** Given the same runtime, the same identity is produced. This is required for bilateral state to be derivable independently by both sides (§3.1).

### 9.2 Identity Format

```
runtime_identity = SHA-256(deployment_id ‖ instance_id)
```

Where:

- `deployment_id` — identifies the deployment or organization (e.g., organizational domain, deployment name). Stable across runtime restarts.
- `instance_id` — identifies the specific runtime instance within the deployment. Stable across restarts, unique within the deployment.

The concatenation is hashed to produce a fixed-size, uniformly distributed identifier suitable for use as an HMAC key (`spec.md` §4.2) and for inclusion in wire format headers (§5.3).

### 9.3 Identity Lifecycle

Runtime identity is derived once at first startup and persisted. It does not change. If a runtime must be reidentified (e.g., migration to a new deployment), all bilateral channels must be re-bootstrapped — the old identity's bilateral state is invalid under the new identity.

---

## 10. Security Considerations

### 10.1 Network Adversary (Class 4)

The wire format and cross-runtime lifecycle are designed against the network adversary described in `threat-model.md` §3.5:

- **Message forgery.** An attacker who does not know `S_AB` cannot produce a valid frame. The envelope provides no authentication — all security is in the frame (`spec.md` §8.2).
- **Message tampering.** Modifying the payload breaks AES-256-GCM integrity. Modifying the frame breaks mirror symmetry or state matching. Modifying the envelope header causes the receiving runtime to reject at envelope validation (wrong step, wrong channel) or at frame validation (wrong depth).
- **Replay.** Replaying a message at a later step fails frame validation — the ratchet has advanced. Replaying at the same step is detected as a duplicate (`step` already processed).
- **Reordering.** The step counter in the envelope provides total ordering. Out-of-order messages fail step validation (§5.5, step 6).

### 10.2 Compromised Runtime (Class 3)

A compromised runtime in a federated deployment is analyzed in `threat-model.md` §3.4. Federation-specific considerations:

- **Bilateral state exposure.** The compromised runtime knows all `S_AB` values it participates in. It can forge messages on any bilateral channel it is a party to. It cannot forge messages on bilateral channels between other runtimes.
- **Multi-hop exposure.** If the compromised runtime is an intermediate hop (§8.2), it sees decoded payloads in transit. End-to-end encryption across hops would mitigate this but is outside current scope.
- **Bilateral state poisoning.** The compromised runtime could advance `S_AB` without sending a real message (e.g., by sending a validly framed message with a payload designed to cause harm). The peer runtime would advance state normally — the bilateral state remains synchronized, but the compromised runtime controls the payload. This is equivalent to the "social engineering via legitimate channels" risk (`threat-model.md` §6.3) at the runtime level.

### 10.3 Recovery Protocol Attacks

The recovery protocol (§7) introduces an attack surface:

- **False recovery.** An attacker sends a message with the `RECOVERY` flag to trick a runtime into entering recovery mode. Mitigation: the runtime enters recovery only on actual frame validation failure, not on receipt of the `RECOVERY` flag alone. The flag indicates the peer's intent, not a trigger for local action.
- **Recovery flooding.** An attacker repeatedly triggers recovery by dropping messages. Mitigation: recovery limits (§7.3) bound the retry loop and escalate to operator.
- **State hash leak.** The recovery protocol exchanges `state_hash = SHA-256(S_AB)`. This reveals whether two runtimes share the same state but does not reveal the state itself (SHA-256 preimage resistance). An attacker observing the hash gains no advantage in forging frames.

---

## 11. References

| Ref | Document | Relevance |
|-----|----------|-----------|
| [1] | `abstract.md` | Multi-runtime architecture, bilateral channels, ratchet state tensor |
| [2] | `spec.md` §7 | Tensor structure, bilateral state operations, advancement protocol |
| [3] | `spec.md` §5.4 | Cross-runtime frame derivation with shared PRNG |
| [4] | `runtime-interface.md` §4 | Message lifecycle (extended with TRANSIT in §6) |
| [5] | `threat-model.md` §3.4–3.5 | Compromised runtime and network adversary analysis |
| [6] | `threat-model.md` §4.12 | State divergence attack analysis |
| [7] | `agent-lifecycle.md` §5 | Channel establishment (agent channels spanning runtimes) |
| [8] | RFC 3526 | MODP group for DH key exchange in trust ceremony |
| [9] | RFC 7748 | X25519 as alternative key agreement scheme |

---

*MFP — authored by Akil Abderrahim and Claude Opus 4.6*
