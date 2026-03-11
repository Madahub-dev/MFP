# Agent Binding Lifecycle

```yaml
id: mfp-agent-lifecycle
type: spec
status: complete
created: 2026-03-10
revised: 2026-03-10
authors:
  - Akil Abderrahim
  - Claude Opus 4.6
tags: [agent, lifecycle, binding, state-machine, channels]
```

## Table of Contents

1. Overview
2. Lifecycle States
3. State Machine
4. Binding Procedure
5. Channel Establishment
6. Active Operation
7. Quarantine
8. Unbinding
9. Forced Termination
10. Lifecycle Invariants
11. References

---

## 1. Overview

This document defines the state machine for an agent's participation in the Mirror Frame Protocol. It specifies every state an agent can occupy, what triggers each transition, and what the runtime does at each transition — including seed derivation timing, tool provisioning, channel establishment, and the distinction between graceful unbinding and forced termination.

The runtime interface contract (`runtime-interface.md`) defines *what* the agent can do in each state. This document defines *how* the agent moves between states and what the runtime guarantees at each transition.

**Scope.** This document covers the single-runtime agent lifecycle completely. Cross-runtime agent identity projection and migration are deferred to `federation.md`.

**Relationship to other documents.** The lifecycle depends on:

- `spec.md` — seed derivation (§4.2), state advancement (§4.4), global state composition (§4.3).
- `threat-model.md` — isolation requirements (§5.2) that the binding procedure must enforce, quarantine triggers (referenced from `runtime-interface.md` §8).
- `runtime-interface.md` — tool provisioning (§3.4), channel model (§6), quarantine effects (§8.3), error semantics (§7).

---

## 2. Lifecycle States

An agent occupies exactly one state at any time. Six states form the lifecycle:

| State | Description | Protocol tools | Channels |
|-------|-------------|---------------|----------|
| `unregistered` | The runtime has no knowledge of this agent. | None | None |
| `binding` | The runtime is initializing the agent. Transient state. | None | None |
| `bound` | The agent is initialized but has no active channels. | `mfp_status` only | None |
| `active` | The agent has at least one active channel. Normal operating state. | All (`mfp_send`, `mfp_channels`, `mfp_status`) | One or more |
| `quarantined` | The agent is isolated due to detected misbehavior. | None | Frozen |
| `terminated` | The agent has been removed from the runtime. Terminal state. | None | None |

### 2.1 State Definitions

**`unregistered`.** The agent does not exist in the runtime's agent table. This is the initial state before the runtime is instructed to bind the agent, and the final state after all cleanup from termination is complete. The runtime holds no state for unregistered agents.

**`binding`.** A transient state during which the runtime initializes the agent. The runtime assigns identity, establishes the execution context, enforces isolation properties, and provisions initial tools. This state is not observable by the agent — from the agent's perspective, it begins life in `bound`. The `binding` state exists to define the runtime's obligations during initialization and to ensure atomicity: if any initialization step fails, the agent never reaches `bound`.

**`bound`.** The agent is known to the runtime, has an assigned `agent_id`, and satisfies all isolation requirements (`threat-model.md` §5.2). It has no channels — it can query its own status (`mfp_status`) but cannot send or receive messages. This is a stable state: an agent may remain `bound` indefinitely, waiting for channel establishment. Bound agents contribute no ratchet state to `Sg` (they have no channels).

**`active`.** The agent has at least one active channel and can participate in protocol communication. This is the normal operating state. All three protocol tools are available. The agent transitions from `bound` to `active` when its first channel is established, and from `active` back to `bound` if all its channels are closed or torn down.

**`quarantined`.** The agent is isolated. All protocol tools are revoked. All channels are frozen (ratchet state preserved, no advancement). The agent's execution may continue but it cannot participate in any protocol communication. Quarantine is reversible — the agent can be restored to its prior state. See §7.

**`terminated`.** The agent has been removed from the runtime. All state — identity, channels, ratchet contributions — has been cleaned up. This is a terminal state. A terminated agent cannot be restored. A new agent may be bound with the same underlying model or configuration, but it receives a new `agent_id` and new ratchet seeds — it is a new agent from the protocol's perspective.

---

## 3. State Machine

### 3.1 Transition Diagram

```
                          bind
  unregistered ──────────────────────> binding
                                         │
                                         │ success
                                         v
                              ┌──────> bound <──────┐
                              │          │           │
                              │          │ first     │ last channel
                              │          │ channel   │ closed
                              │          v           │
                     restore  │       active ────────┘
                              │          │
                              │          │ quarantine
                              │          v
                              └──── quarantined
                                         │
                                         │ terminate
                                         v
                                    terminated ──> (cleanup) ──> unregistered
```

### 3.2 Transition Table

| From | To | Trigger | Initiator | Section |
|------|----|---------|-----------|---------|
| `unregistered` | `binding` | Bind instruction | Operator (Layer 2) | §4 |
| `binding` | `bound` | Initialization success | Runtime (automatic) | §4 |
| `binding` | `unregistered` | Initialization failure | Runtime (automatic) | §4.5 |
| `bound` | `active` | First channel established | Runtime (automatic) | §5 |
| `active` | `bound` | Last channel closed | Runtime (automatic) | §6.2 |
| `active` | `quarantined` | Misbehavior detected | Runtime (automatic) or Operator (manual) | §7 |
| `bound` | `quarantined` | Operator quarantine | Operator (Layer 2) | §7 |
| `quarantined` | `bound` | Restore (no active channels remain) | Operator (Layer 2) | §7.3 |
| `quarantined` | `active` | Restore (active channels remain) | Operator (Layer 2) | §7.3 |
| `bound` | `terminated` | Unbind instruction | Operator (Layer 2) | §8 |
| `active` | `terminated` | Unbind instruction | Operator (Layer 2) | §8 |
| `quarantined` | `terminated` | Terminate instruction | Operator (Layer 2) | §9 |
| `terminated` | `unregistered` | Cleanup complete | Runtime (automatic) | §9.2 |

### 3.3 Forbidden Transitions

The following transitions are explicitly forbidden:

- `unregistered → active` — an agent cannot skip binding.
- `unregistered → quarantined` — cannot quarantine what does not exist.
- `terminated → bound` — termination is irreversible. Rebinding creates a new agent.
- `quarantined → active` (without restore) — quarantine cannot time out or self-resolve.
- Any agent-initiated transition — agents cannot bind, unbind, quarantine, or restore themselves or other agents. All transitions are initiated by the runtime or the operator.

---

## 4. Binding Procedure

Binding transforms an agent from `unregistered` to `bound`. It is initiated by the operator (Layer 2) and executed by the runtime.

### 4.1 Input

The bind instruction provides:

| Field | Type | Description |
|-------|------|-------------|
| `agent_config` | implementation-specific | Configuration for the agent's execution environment: model endpoint, system prompt, sandbox parameters, resource limits. |
| `role` | string (optional) | Descriptive role label. Not used by the protocol — included for operator convenience and audit logging. |

The bind instruction does NOT include `agent_id` or channel configuration. Identity is assigned by the runtime (§4.3). Channels are established separately (§5).

### 4.2 Procedure

The runtime executes the following steps atomically. If any step fails, all preceding steps are rolled back and the agent remains `unregistered`.

```
BIND(agent_config) → agent_id | error

  1. REGISTER
     - Create an entry in the runtime's agent table.
     - State: binding.

  2. ASSIGN IDENTITY
     - Generate agent_id (§4.3).
     - Record in agent table.

  3. ESTABLISH EXECUTION CONTEXT
     - Initialize the agent's sandbox/execution environment.
     - Enforce isolation properties I1–I6 (threat-model.md §5.2):
       - I1: Output interception configured.
       - I2: Network access blocked.
       - I3: Filesystem access to runtime state blocked.
       - I4: Inter-agent memory sharing prevented.
       - I5: Process control blocked.
       - I6: Identity bound to execution context.
     - Apply recommended isolation properties I7–I9 if the deployment supports them.

  4. PROVISION INITIAL TOOLS
     - Grant mfp_status to the agent.
     - mfp_send and mfp_channels are NOT yet provisioned (no channels exist).

  5. INVOKE AGENT
     - Start the agent's execution (call LLM API, launch subprocess, etc.).
     - The agent begins life in the bound state.

  6. TRANSITION
     - Set state: bound.
     - Return agent_id to the operator.
```

### 4.3 Identity Assignment

The runtime generates `agent_id` as an opaque, unique identifier:

```
agent_id = runtime_identity ‖ monotonic_counter ‖ random_suffix
```

Where:

- `runtime_identity` — the runtime's own stable identifier (ensures uniqueness across runtimes if identifiers are ever compared externally).
- `monotonic_counter` — a strictly increasing counter within the runtime (ensures uniqueness within the runtime across time).
- `random_suffix` — 8 bytes from the OS CSPRNG (prevents prediction of future `agent_id` values).

The `agent_id` is opaque to agents — they see it as an identifier, not as a structured value. The internal structure is a runtime implementation detail.

### 4.4 Isolation Enforcement

The binding procedure is where isolation requirements (`threat-model.md` §5.2) are enforced. The runtime MUST verify that the execution context satisfies all mandatory properties before transitioning to `bound`. The verification is implementation-specific:

| Strategy | Verification |
|----------|-------------|
| **LLM API** | No local execution context exists. Isolation is inherent. Verify API endpoint is reachable. |
| **Subprocess + seccomp** | Verify seccomp-bpf filter is loaded and blocks network, filesystem, and process control syscalls. |
| **Container / VM** | Verify container/VM has no network access, read-only filesystem, and no shared volumes with other agents. |
| **Language sandbox** | Verify sandbox restrictions are active. Log a warning if I2, I3, or I5 are only partially enforced. |

If isolation cannot be verified, binding fails (§4.5).

### 4.5 Binding Failure

If any step in the binding procedure fails:

1. All preceding steps are rolled back (agent table entry removed, execution context torn down, identity released).
2. The agent remains `unregistered`.
3. The runtime returns an error to the operator with the failure reason.
4. No ratchet state is affected — the agent never contributed to `Sg`.

---

## 5. Channel Establishment

Channel establishment is defined in `runtime-interface.md` §6.2. This section specifies the lifecycle implications — what changes in the agent's state when a channel is established.

### 5.1 Preconditions

Both agents must be in state `bound` or `active`. Channels cannot be established with `unregistered`, `binding`, `quarantined`, or `terminated` agents.

### 5.2 Seed Derivation Timing

Seed derivation occurs during channel establishment — not during agent binding. This is because the seed depends on both agents' identities and the channel identifier, none of which are known at bind time.

```
Sₗ₀ = HMAC-SHA-256(key: runtime_identity, message: agent_a ‖ agent_b ‖ channel_id)
```

(`spec.md` §4.2)

The seed is derived once, at channel establishment, and never rederived. The ratchet advances from this seed with each exchange.

### 5.3 State Transition

When a channel is established for an agent currently in `bound` state:

1. The runtime provisions `mfp_send` and `mfp_channels` tools to the agent (in addition to the already-provisioned `mfp_status`).
2. The agent transitions from `bound` to `active`.

When a channel is established for an agent already in `active` state:

1. No state transition — the agent remains `active`.
2. The new channel appears in the agent's `mfp_channels()` results.

### 5.4 Global State Impact

The new channel's `Sₗ₀` is incorporated into `Sg`:

```
Sg' = compose(...existing Sₗ values..., Sₗ₀_new)
```

This recomputation affects frame derivation on all channels within the runtime. The new channel's existence — even before any messages are exchanged — changes the global state.

---

## 6. Active Operation

### 6.1 Normal Operation

In the `active` state, the agent participates in protocol communication through the interface defined in `runtime-interface.md` §3. The runtime processes messages through the lifecycle defined in `runtime-interface.md` §4. No lifecycle-specific behavior occurs during normal operation — the agent sends and receives messages, and the ratchet advances.

### 6.2 Channel Loss

When a channel is closed (`runtime-interface.md` §6.4) and the agent has other active channels:

1. The closed channel is removed from the agent's `mfp_channels()` results.
2. `Sg` is recomputed without the closed channel's `Sₗ`.
3. The agent remains `active`.

When a channel is closed and the agent has NO other active channels:

1. The agent transitions from `active` to `bound`.
2. `mfp_send` and `mfp_channels` tools are revoked (they require at least one channel to be useful). `mfp_status` remains provisioned.
3. The agent can still be invoked by the runtime but cannot participate in protocol communication until a new channel is established.

### 6.3 Multiple Channels

An agent may have multiple channels simultaneously. Each channel has independent ratchet state (`Sₗ`, `t`), independent frame depth (`k`), and independent quarantine status. The agent's lifecycle state is determined by the aggregate:

- `active` if at least one channel is `active`.
- Transition to `bound` only when ALL channels are closed.
- Agent-level quarantine (§7) affects all channels simultaneously.
- Channel-level quarantine (`runtime-interface.md` §8.3) does not affect the agent's lifecycle state — the agent remains `active` if it has other active channels.

---

## 7. Quarantine

### 7.1 Triggers

Quarantine triggers are defined in `runtime-interface.md` §8.2. This section specifies the lifecycle transition.

**Agent-level quarantine** is triggered when the misbehavior is attributed to the agent itself (rate violation, repeated payload constraint violation, operator instruction).

**Channel-level quarantine** is triggered when the misbehavior is specific to a channel (repeated validation failure). Channel-level quarantine does not change the agent's lifecycle state — only the channel's status changes. The agent transitions to `quarantined` only on agent-level quarantine.

### 7.2 Transition Procedure

When an agent is quarantined:

```
QUARANTINE(agent_id) → void

  1. Set agent state: quarantined.

  2. REVOKE TOOLS
     - Remove mfp_send, mfp_channels, mfp_status.
     - Pending tool calls return QUARANTINED error.

  3. FREEZE CHANNELS
     - For each channel the agent participates in:
       - Set channel status: quarantined.
       - Freeze Sₗ and t (no advancement).
       - Do NOT recompute Sg (runtime-interface.md §8.3).
     - Discard messages in transit to the agent.

  4. LOG
     - Record quarantine event: agent_id, timestamp, trigger reason.
     - Do NOT log ratchet state or frame data.
```

The agent's execution MAY continue — the runtime does not necessarily terminate the agent process. But the agent cannot participate in any protocol operation. Whether to terminate the underlying execution is an operator decision, not a protocol requirement.

### 7.3 Restoration

Restoration is initiated by the operator (Layer 2). The runtime does not auto-restore.

```
RESTORE(agent_id) → void

  1. Evaluate channels:
     - Channels quarantined solely due to agent quarantine: restore to active.
     - Channels quarantined independently (e.g., validation failure): remain quarantined.

  2. PROVISION TOOLS
     - If restored channels exist: grant mfp_send, mfp_channels, mfp_status.
       Set agent state: active.
     - If no restored channels exist: grant mfp_status only.
       Set agent state: bound.

  3. RESUME
     - Channels resume from their frozen Sₗ and t. No state reset.
     - Sg is unchanged (it was never modified during quarantine).

  4. LOG
     - Record restore event: agent_id, timestamp, restored channel count.
```

### 7.4 Quarantine Duration

The protocol does not define a maximum quarantine duration. Quarantine persists until the operator explicitly restores or terminates the agent. This is deliberate — automatic timeout would allow a compromised agent to cycle between quarantine and active status, and determining whether the underlying cause is resolved requires operator judgment that the protocol cannot automate.

---

## 8. Unbinding

Unbinding is the graceful removal of an agent from the runtime. It is initiated by the operator (Layer 2) for agents in `bound` or `active` state. Unbinding a `quarantined` agent uses forced termination instead (§9).

### 8.1 Preconditions

The agent must be in state `bound` or `active`. Unbinding from `active` triggers channel teardown as part of the procedure.

### 8.2 Procedure

```
UNBIND(agent_id) → void

  1. CLOSE CHANNELS
     - For each channel the agent participates in:
       - Execute channel teardown (runtime-interface.md §6.4):
         - Discard pending messages.
         - Zero Sₗ in memory and persistent storage.
         - Recompute Sg without this channel.
       - Notify the peer agent: channel status changes to closed
         in their mfp_channels() results.

  2. REVOKE TOOLS
     - Remove all protocol tools (mfp_send, mfp_channels, mfp_status).

  3. TERMINATE EXECUTION
     - Stop the agent's execution context (terminate API session,
       stop subprocess, destroy container).
     - Clean up sandbox resources.

  4. RELEASE IDENTITY
     - Remove agent_id from the agent table.
     - The agent_id is not reused.

  5. LOG
     - Record unbind event: agent_id, timestamp, channels closed count.

  6. TRANSITION
     - Set state: terminated.
     - After cleanup completes: unregistered (entry removed from agent table).
```

### 8.3 Peer Impact

When an agent is unbound, its peers are affected:

- All channels with the unbound agent are closed. Peers see channel status `closed` in `mfp_channels()`.
- If a peer's only channel was with the unbound agent, the peer transitions from `active` to `bound` (§6.2).
- `Sg` is recomputed for all remaining agents. This changes frame derivation on all remaining channels — the unbinding event propagates through the global state.

### 8.4 State Zeroing

Unbinding zeroes all protocol state associated with the agent:

- All `Sₗ` values for channels the agent participated in — in memory and persistent storage.
- The agent's `agent_id` is removed (not zeroed — it simply ceases to exist in the agent table).
- No ratchet state, encoding key, or channel metadata survives unbinding.

This ensures forward secrecy extends to agent removal. After unbinding, recovering the agent's historical ratchet states requires inverting HMAC-SHA-256 — computationally infeasible (`spec.md` §8.6).

---

## 9. Forced Termination

Forced termination removes a quarantined agent. It is used when the operator determines the agent cannot be safely restored.

### 9.1 Distinction from Unbinding

| | Unbinding (§8) | Forced Termination (§9) |
|--|----------------|------------------------|
| **Source state** | `bound` or `active` | `quarantined` |
| **Channels** | Closed gracefully (state zeroed) | Closed forcefully (state zeroed) |
| **Agent execution** | Stopped gracefully | Stopped immediately |
| **Peer notification** | Peers see channels closed | Peers see channels closed |
| **Use case** | Normal agent removal | Confirmed compromise, unrecoverable misbehavior |

### 9.2 Procedure

```
TERMINATE(agent_id) → void

  1. CLOSE CHANNELS (forced)
     - For each channel the agent participates in (all are currently quarantined):
       - Zero Sₗ in memory and persistent storage.
       - Recompute Sg without this channel.
       - Notify peer agents: channel status changes to closed.

  2. STOP EXECUTION (immediate)
     - Forcefully terminate the agent's execution context.
     - Do not wait for graceful shutdown.

  3. RELEASE IDENTITY
     - Remove agent_id from the agent table.

  4. LOG
     - Record termination event: agent_id, timestamp, reason,
       channels force-closed count.

  5. TRANSITION
     - Set state: terminated → unregistered (entry removed).
```

### 9.3 Forensic Considerations

When terminating a quarantined agent suspected of compromise:

- The audit log (if enabled per `threat-model.md` §5.3, I9) preserves the event history: when the agent was bound, what channels it had, when quarantine was triggered, what the trigger was, and when termination occurred.
- Ratchet state is still zeroed — forward secrecy is not compromised for forensics. The audit log records events, not cryptographic state.
- If the deployer requires post-termination analysis of the agent's behavior, they must implement application-level logging of decoded payloads. The protocol does not retain payload content after delivery.

---

## 10. Lifecycle Invariants

The following invariants hold across all states and transitions:

**L1. Single state.** An agent occupies exactly one state at any time. There are no intermediate or ambiguous states (the `binding` state is transient but well-defined).

**L2. Monotonic identity.** An `agent_id`, once assigned, is never reassigned to a different agent. Terminated agents' IDs are retired, not recycled.

**L3. Tool-state consistency.** The tools available to an agent are always consistent with its state:
- `unregistered`, `binding`, `quarantined`, `terminated`: no tools.
- `bound`: `mfp_status` only.
- `active`: all protocol tools.

There is no state where an agent has `mfp_send` but no channels, or channels but no `mfp_send`.

**L4. Channel-state consistency.** An agent's lifecycle state is consistent with its channel set:
- `bound`: zero channels.
- `active`: one or more channels.

The transitions `bound → active` (first channel) and `active → bound` (last channel closed) enforce this automatically.

**L5. Atomic transitions.** Every state transition is atomic. If the transition procedure fails at any step, all preceding steps are rolled back and the agent remains in its original state. There is no observable intermediate state during a transition.

**L6. Operator authority.** All lifecycle transitions are initiated by the runtime (automatically, in response to protocol events) or by the operator (via Layer 2 administration). Agents cannot initiate, influence, or observe lifecycle transitions for themselves or other agents. An agent does not know whether it is about to be quarantined or unbound.

**L7. Forward secrecy on removal.** When an agent is unbound or terminated, all ratchet state (`Sₗ`) for its channels is zeroed in memory and persistent storage. No cryptographic state survives agent removal.

**L8. Global state consistency.** `Sg` is recomputed whenever the set of `Sₗ` values changes — channel establishment (new `Sₗ₀`), channel closure (remove `Sₗ`), or ratchet advancement (updated `Sₗ`). `Sg` is never stale with respect to the current channel set. Note: quarantine does NOT trigger recomputation (`runtime-interface.md` §8.3).

---

## 11. References

| Ref | Document | Relevance |
|-----|----------|-----------|
| [1] | `abstract.md` | Architecture, agent hosting, orchestrator as unprivileged agent |
| [2] | `spec.md` | Seed derivation (§4.2), state advancement (§4.4), global composition (§4.3), security bounds (§8) |
| [3] | `threat-model.md` | Isolation requirements (§5.2), attacker classes (§3), quarantine rationale |
| [4] | `runtime-interface.md` | Tool provisioning (§3.4), channel model (§6), quarantine effects (§8), error semantics (§7) |
| [5] | `federation.md` | Cross-runtime agent identity projection, bilateral channel agent binding |

---

*MFP — authored by Akil Abderrahim and Claude Opus 4.6*
