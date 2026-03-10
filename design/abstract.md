# Mirror Frame Protocol (MFP)
**A Structural Integrity Mechanism for LLM Agent-to-Agent Communication**

---

## Abstract

The Mirror Frame Protocol (MFP) defines a lightweight, language-native validation envelope for securing communication between LLM agents. By embedding symmetric structural constraints into the message itself — rather than relying on external cryptographic layers — MFP enables receiving agents to validate message integrity before semantic interpretation of the payload. This two-phase gate (structural validation -> payload execution) provides a defense against prompt injection attacks that operate within the LLM's native medium: natural language.

MFP separates security enforcement from workflow orchestration. A deterministic runtime layer binds every agent — including orchestrators — and mediates all inter-agent communication. Frames are derived from a mathematical function rather than stored in a registry, and payloads are encoded through external tooling rather than LLM-performed transformation.

## Core Structure

A message `M` consists of `2k + p` ordered blocks, where `k` mirror pairs form the frame and `p` interior blocks form the encoded payload.

```
M = [ B₁  B₂  ...  Bₖ  |  E(Bₖ₊₁  ...  Bₖ₊ₚ)  |  reverse(Bₖ)  ...  reverse(B₂)  reverse(B₁) ]
         frame_open          encoded_payload                   frame_close
```

**Constraint:** `frame_close = mirror(frame_open)` — the closing blocks are the exact reverse-ordered reflection of the opening blocks.

The payload `E(P)` is the output of an encoding function applied to the raw payload `P`. The encoding is opaque to agents without the appropriate decoding tool.

## Frame Derivation

Frames are not stored or distributed. They are derived from a ratcheting function whose output is a probability distribution over the frame space, parameterized by the current state and channel:

```
F(t, Sₗ, Sg) → D(t, Sₗ, Sg)
frame ~ D(t, Sₗ, Sg)
```

The distribution `D` is a deterministic function of its parameters — given the same `(t, Sₗ, Sg)`, it produces the same distribution. But the frame itself is a sample drawn from `D` by the runtime. The runtime's enforcement behavior is deterministic; frame generation introduces controlled stochasticity.

Where:
- `t` — the transaction step (ensures temporal rotation; a frame valid at step `t` is permanently invalid at `t+1`)
- `Sₗ` — the local ratchet state for the specific channel (per agent-pair; a fixed-size value that chains forward with each exchange via a one-way function)
- `Sg` — the global ratchet state across the runtime (derived from the composition of all local ratchet states; mixes into every channel's frame derivation as a runtime-wide parameter)

### Ratchet State Model

History is not a log. It is a ratchet — a fixed-size state value that chains forward with each exchange:

```
Sₗ₀ = seed(agent_pair, channel_id)
Sₗₙ = f(Sₗₙ₋₁, frameₙ)
```

The frame at step `n` is sampled from `D(t, Sₗₙ₋₁, Sg)`, and once produced, is folded back into the state to produce `Sₗₙ`. Because the sampled frame — not a deterministic output — feeds into the ratchet, the state trajectory itself becomes stochastic. Even a deterministic one-way function `f` produces unpredictable state evolution because its input includes a random sample. The runtime never stores or replays the full message history — it carries a single chained state value forward per channel.

This provides **forward secrecy**: an attacker who obtains the current `Sₗₙ` cannot reverse the one-way function to recover `Sₗₙ₋₁` or any earlier state. Compromising the present does not compromise the past. The stochastic frame generation provides an additional layer: even with knowledge of the state, the attacker cannot predict the exact frame that will be drawn.

The runtime maintains ratchet state at two levels:

- **Local ratchet `Sₗ`** is scoped to a channel (a pair of communicating agents). Each successful exchange advances the channel's ratchet by one step. This is the primary input to `F` for channel-specific frame derivation.
- **Global ratchet `Sg`** is derived from the composition of all local ratchet states across the runtime. It mixes into every local `F` computation as a runtime-wide parameter. Each time any `Sₗ` advances, `Sg` is recomputed. This serves three purposes:
  1. **Entropy amplification.** Even a quiet channel with few exchanges benefits from ratchet advances on busy channels across the runtime. An attacker targeting a low-traffic channel cannot ignore the rest of the system.
  2. **Cross-channel anomaly detection.** Patterns invisible within a single `Sₗ` — correlated frame failures, synchronized probing across channels — become visible through `Sg`.
  3. **Multi-channel agent binding.** An agent participating in multiple channels has all its channels tied together through `Sg`. Correct behavior on one channel and malicious behavior on another are detectable because both contribute to the same global ratchet state.

Ratchet state is maintained solely by the runtime. Agents never see or manipulate it. Since every message passes through the runtime, there is no independent maintenance and no possibility of state divergence within a single runtime. The runtime advances `Sₗ` atomically on successful delivery and recomputes `Sg` accordingly.

**Storage is constant** regardless of communication volume: one fixed-size `Sₗ` per channel, one `Sg` per runtime.

The ratchet is seeded deterministically at bind time. The initial seed requires no secrecy — it can be derived from agent identity, channel identifier, or any agreed-upon starting value. Security does not depend on the seed's entropy but on the one-way chaining: each step digests the previous, making future frames unpredictable without having witnessed every prior exchange.

### Multi-Runtime State Generalization

The single-runtime ratchet model generalizes to a **ratchet state tensor** `S[i, j, k]` for multi-runtime federation:

- `i` — source runtime
- `j` — destination runtime
- `k` — channel within that runtime pair

Projected onto the runtime dimensions, the tensor forms a **ratchet state matrix**:

```
        R_A      R_B      R_C
R_A   [Sg_A]    S_AB     S_AC
R_B    S_BA    [Sg_B]    S_BC
R_C    S_CA     S_CB    [Sg_C]
```

- **Diagonal entries** (`Sg_A`, `Sg_B`, `Sg_C`) are each runtime's internal global state — private, never shared, derived from the composition of all internal `Sₗ` values and all bilateral states in which the runtime participates.
- **Off-diagonal entries** (`S_AB`, `S_BC`, etc.) are **bilateral ratchet states** — jointly maintained by two runtimes, advancing with each cross-runtime exchange. Neither runtime's internal state is exposed; the bilateral state is a joint product of their interaction.

For intra-runtime communication (unchanged):

```
F(t, Sₗ, Sg_A) → D → frame
```

For cross-runtime communication:

```
F(t, Sₗ, S_AB) → D → frame
```

The bilateral state replaces `Sg` in cross-runtime frame derivation. Both runtimes know `S_AB` because they both contributed to it. Neither learns the other's `Sg`.

Each runtime's global state incorporates bilateral activity:

```
Sg_A = compose(all internal Sₗ, S_AB, S_AC, ...)
```

Internal channels benefit from cross-runtime entropy amplification, but the bilateral state is the shared input to cross-runtime frame derivation — no internal state is exposed.

The single-runtime model is a degenerate case: when there is only one runtime, the tensor collapses to a vector of `Sₗ` per channel, and `Sg = compose(S[0, 0, *])`.

**Bilateral ratchet advancement** follows the protocol's existing implicit acknowledgment model, applied at the runtime level:

1. Runtime A frames a message using `S_AB` and sends to Runtime B.
2. Runtime B validates the frame against `S_AB`, advances to `S_AB' = f(S_AB, frame)`.
3. Runtime B's response — itself framed with `S_AB'` — serves as acknowledgment.
4. Runtime A advances on receipt of the valid response.

**Stochastic frames across runtimes:** The bilateral state `S_AB` includes shared randomness (a shared PRNG seed). Both runtimes derive the same distribution `D(t, Sₗ, S_AB)` and draw the same sample, preserving stochastic frame derivation uniformly across intra- and cross-runtime communication.

## Validation Model

A receiving agent processes every inbound message in three mandatory phases:

- **Phase 1 — Frame Check:** The runtime extracts the outer `k` blocks from each end and verifies mirror symmetry. In the single-runtime model, the runtime validates the frame against its own generated sample. In the multi-runtime model, both runtimes derive the same distribution and sample from the shared bilateral state `S_AB`. If the frame is broken or does not match, the entire message is discarded. The payload never enters the agent's reasoning.
- **Phase 2 — Payload Decode:** On valid frame, the runtime invokes the decoding tool to recover the raw payload `P` from `E(P)`.
- **Phase 3 — Payload Read:** The decoded payload is delivered to the agent as actionable content.

## Security Properties

| **Attack Vector** | **Defense Mechanism** |
| ----------------- | --------------------- |
| **Blind injection** | Injected content arrives frameless -> rejected at Phase 1 |
| **Frame guessing** | Frame sampled from a distribution `D(t, Sₗ, Sg)` where `Sₗ` is a one-way ratchet state and `Sg` mixes in runtime-wide ratchet activity; attacker faces two layers: unknown state (ratchet is one-way) and unknown sample (frame is stochastic); the sampled frame feeds back into the ratchet, making state trajectory itself unpredictable; obtaining the current state does not reveal past states (forward secrecy) |
| **Payload interception** | Payload is encoded; without the decode tool, raw content is opaque |
| **Payload tampering** | Payload integrity is enforced by the encoding layer; an attacker without the encode/decode tool cannot produce a valid encoded payload |
| **Nested frame injection** | Frame tokens are forbidden inside the payload; each agent-pair channel has its own `Sₗ`, producing structurally distinct frames by construction |
| **Reflection/echo attack** | Frame state is scoped to `(agent_id, step_t, Sₗ, Sg)`; replaying a past frame fails because the ratchet has advanced irreversibly |
| **Cross-channel attack** | `Sg` ties all channels together; an attacker probing multiple channels simultaneously is detectable through correlated patterns in the global ratchet state |
| **Compromised orchestrator** | Orchestrator is bound by the runtime like any other agent; it cannot bypass frame validation or forge encoded payloads |

## Architecture

### Runtime (Agent Host and Security Enforcer)

The runtime is a deterministic, non-LLM component that serves as the execution environment for all agents. It is not a service that agents connect to — it is the host process in which agents run. The runtime invokes each agent, receives its structured outputs (including tool calls), and mediates all external effects. An agent cannot make direct network calls, access shared memory, or communicate with other agents outside the runtime's mediation. This is a host/sandbox relationship: the agent runs inside the runtime's process boundary.

Because tool calls are structured outputs intercepted by the host, tool invocation is inherently authenticated — the runtime knows which agent is calling because it controls the execution context. There is no separable interface between agent and tool to intercept or spoof.

The runtime's responsibilities:

- **Agent hosting:** The runtime invokes and manages each agent's execution. All agent outputs — including tool calls — pass through the runtime before taking effect.
- **Agent binding:** Before an agent can participate, the runtime binds it — provisioning function parameters for frame derivation, granting encode/decode tool access, and assigning agent identity.
- **Frame validation:** Every inbound message passes through the runtime for frame verification before reaching the destination agent.
- **Tool provisioning:** Encode/decode tools are granted as a privilege. Only bound agents receive them. Tool parameters (encoding algorithm, keys) are managed and rotated by the runtime. The agent never possesses encoding/decoding logic — it requests a service that the runtime performs.
- **Encoding rotation:** The runtime rotates encoding algorithms independently of frame rotation. An agent calls `encode(payload)` — the mechanism underneath changes without the agent's prompt changing.
- **Payload opacity:** No LLM ever sees the encoded form of a payload. The runtime encodes on the sending side and decodes on the receiving side. Agents interact only with raw plaintext payloads.

### Orchestrator (Workflow Coordinator)

The Orchestrator is an unprivileged agent from the runtime's perspective. It coordinates task routing, workflow sequencing, and agent selection — but is itself bound to the same frame and encoding requirements as every other agent. A compromised Orchestrator cannot bypass security because it never held security authority.

### Topology

```
Agent ←→ Runtime ←→ Agent
              ↑
        Orchestrator (bound as agent)
```

The runtime is topology-agnostic. It supports orchestrated workflows, peer-to-peer agent communication, or any hybrid — the security model is invariant to coordination pattern.

In multi-runtime federation, runtimes communicate through bilateral ratchet channels:

```
Agent ←→ Runtime_A ←⟶ Runtime_B ←→ Agent
              ↑          S_AB          ↑
        Orchestrator              Orchestrator
```

Each runtime remains the sole host for its own agents. Cross-runtime messages transit through the bilateral channel, framed using `S_AB` rather than either runtime's internal `Sg`.

## Implicit Acknowledgment

A valid response from an agent — itself wrapped in a correct frame for step `t+1` — simultaneously proves: (1) receipt of the original instruction, (2) continued protocol adherence, (3) causal chain integrity. A compromised or hijacked agent produces frameless, broken-frame, or incorrectly encoded output, enabling immediate detection and channel quarantine by the runtime.

## Design Decisions

1. **Payload encoding with tool-based execution.** Payloads are encoded, not transmitted as plaintext. Encoding and decoding are performed by external tools provisioned by the runtime, not by the LLM itself. This separates policy (what to encode) from mechanism (how to encode) and avoids dependence on LLM reliability for cryptographic or algorithmic operations. Two modes exist: frame-informed encoding (the frame structure determines the encoding scheme) and pre-shared encoding (the agent's bound tool already knows the algorithm). Encoding algorithms are rotated by the runtime.

2. **Frame as ratcheting function with two-level state.** Frames are derived from a function `F(t, Sₗ, Sg)` that parameterizes a probability distribution over the frame space, rather than stored in a registry and distributed per-step. State is not a log — it is a ratchet. Each exchange samples a frame from the current distribution, then folds that frame back into the state via a one-way function: `Sₗₙ = f(Sₗₙ₋₁, frameₙ)`. Because the sampled frame feeds into the ratchet, the state trajectory itself is stochastic. The ratchet operates at two levels: local state `Sₗ` (per-channel, advancing with each exchange between a specific agent pair) and global state `Sg` (derived from the composition of all local states, mixed into every channel as a runtime-wide parameter). Storage is constant — one fixed-size `Sₗ` per channel, one `Sg` per runtime — regardless of communication volume. The one-way chain provides forward secrecy: obtaining the current state does not reveal any prior state. Security grows with communication volume across the entire runtime, not just individual channels. `Sg` provides entropy amplification for quiet channels, cross-channel anomaly detection, and multi-channel agent binding. State is maintained solely by the runtime — agents never see or manipulate it — eliminating divergence within a single runtime. The function is seeded deterministically at bind time; the seed requires no secrecy because security derives from the irreversible chaining, not from seed entropy.

3. **Runtime as security enforcer, separate from Orchestrator.** The security protocol is enforced by a deterministic, non-LLM runtime layer, not by the Orchestrator. Every agent — including the Orchestrator — is bound by the runtime. This eliminates the Orchestrator as a privileged single point of compromise, removes prompt injection surface from the security enforcement path, and makes the security model independent of workflow topology.

4. **Encode/decode tooling as runtime infrastructure.** The encode/decode tools are not application-level services — they are part of the runtime infrastructure, on the same plane as frame validation and message routing. A tool failure is an infrastructure failure: the message is never partially constructed or emitted. The runtime guarantees atomicity — a message either leaves fully formed (valid frame + encoded payload) or does not leave at all. There is no protocol-level state for "valid frame, unencoded payload." This eliminates an entire class of degraded-state edge cases from the protocol's design surface.

5. **Deterministic seeding, dynamic security.** The ratchet is seeded deterministically at bind time — the initial seed requires no secrecy. Security is not front-loaded into seed entropy but accumulates through the one-way chaining of each exchange. This resolves the bootstrap problem: no secret distribution is needed at initialization, and no attestation mechanism is required for first contact. The runtime provisions a deterministic seed derived from agent identity and channel identifier; security emerges from communication itself.

6. **Removal of length and depth parameters.** `L` (communication length) and `d` (chain depth) were removed from `F`. Message-geometry binding (`L`) is redundant: the ratchet state `Sₗ` is unique to each step, so a frame cannot be lifted from one message and applied to another regardless of length difference. Payload integrity is the encoding layer's responsibility, not the frame's. `L` also introduced a fragile definitional problem (token count vs. character count vs. byte length) with no unambiguous answer. Hierarchical depth (`d`) is redundant: each agent-pair channel has its own `Sₗ`, so agents at different depths in a chain already produce structurally distinct frames by construction. `d` also introduced an unsolvable verification problem in dynamic topologies where depth cannot be reliably assigned. The simplified `F(t, Sₗ, Sg)` retains all necessary security properties with fewer, unambiguous, runtime-maintained inputs.

7. **Runtime as agent host.** The runtime is the execution environment in which agents run, not a service agents connect to. The runtime invokes each agent, intercepts its structured outputs, and executes all tool calls on the agent's behalf. This is stronger than a client/server relationship — it is a host/sandbox relationship where the agent runs inside the runtime's process boundary. This resolves the primary concern of Open Question 1: there is no separable interface between agent and tool to intercept, because tool invocation is a call to the host itself. Authentication is structural — the runtime knows which agent is calling because it controls the execution context. This also narrows Open Question 2: since no LLM ever sees the encoded form of a payload — the runtime encodes on the sending side and decodes on the receiving side — encoding schemes need only be deterministic and tamper-evident, not LLM-opaque.

8. **Stochastic frame derivation.** The frame derivation function `F` does not produce a deterministic output. Instead, `F(t, Sₗ, Sg)` parameterizes a probability distribution `D` over the frame space, and the runtime samples from `D` to generate each frame. The distribution is a deterministic function of its parameters; the frame is not. This provides a direct defense against adversarial observation (Open Question 3): an attacker observing frames sees samples from changing distributions, not deterministic outputs from a function they might model. Even with knowledge of the current state, the exact frame is unpredictable. Because the sampled frame feeds back into the ratchet — `Sₗₙ = f(Sₗₙ₋₁, frameₙ)` — the randomness propagates forward: the ratchet trajectory itself becomes stochastic, compounding unpredictability at every step. The distribution is parameterized by both the ratchet state and the channel, providing structural separation — frames from one channel are not merely unlikely but drawn from a different distribution than frames from another. The agent-host architecture (Design Decision 7) makes this feasible: the same runtime generates and validates every frame, so stochastic generation creates no synchronization problem within a single runtime. Cross-runtime stochastic frames are preserved through shared randomness in the bilateral state (Design Decision 9).

9. **Ratchet state tensor for multi-runtime federation.** The single-runtime ratchet model — a vector of per-channel `Sₗ` values composed into one `Sg` — generalizes to a ratchet state tensor `S[i, j, k]` where `i` and `j` are runtimes and `k` is a channel. Diagonal entries are each runtime's private global state; off-diagonal entries are bilateral ratchet states jointly maintained by runtime pairs. This resolves the core challenge of Open Question 4: cross-runtime frame derivation uses the bilateral state `S_AB` rather than either runtime's internal `Sg`, so no internal state is exposed. Each runtime's `Sg` still incorporates bilateral activity (preserving entropy amplification), but the bilateral state is a joint product of interaction, not a window into either runtime's internals. Bilateral ratchet advancement follows the existing implicit acknowledgment model applied at the runtime level — a valid framed response from the receiving runtime serves as atomic advancement of `S_AB`. Stochastic frames are preserved across runtimes through shared randomness embedded in the bilateral state. The single-runtime model is a degenerate case of the tensor.

## Open Questions

1. **Tool interface as attack surface.** The agent-host architecture (Design Decision 7) eliminates the primary concern: tool invocation goes to the runtime itself, not through a separable interface. The remaining question: in implementations where the agent's execution environment is not fully sandboxed (e.g., an agent with network access or filesystem access), can the agent exfiltrate information that weakens the protocol? What are the minimum isolation requirements the host must enforce beyond tool-call mediation?

2. **Encoding algorithm vocabulary.** The agent-host architecture (Design Decision 7) narrows this question: since no LLM ever sees the encoded payload — the runtime encodes on send and decodes on receive — the encoding scheme does not need to be LLM-opaque. The remaining question: which encoding schemes satisfy the requirements of determinism, tamper-evidence, and efficient rotation? What is the minimal encoding that provides payload integrity without redundant overhead given that frame validation already provides message-level authentication?

3. **Adversarial observation.** Stochastic frame derivation (Design Decision 8) addresses the primary concern: an attacker observing frames sees samples from distributions, not deterministic outputs, and the stochastic ratchet trajectory compounds unpredictability. The remaining question: what family of distributions provides sufficient entropy that an attacker cannot guess into the support, while keeping the support narrow enough that collision probability — a forged frame accidentally falling within the valid range — is negligible? What are the concrete bounds on distribution entropy and support width as functions of frame space size?

4. **Multi-runtime federation.** The ratchet state tensor (Design Decision 9) resolves the synchronization and state-exposure problems: bilateral ratchet states replace `Sg` for cross-runtime frame derivation, and no internal state is shared. The remaining questions: (a) How is bilateral ratchet state recovered after a failed cross-runtime exchange (network partition, crash) where the two runtimes may have diverged on whether advancement occurred? This is a two-party distributed state problem with known solutions, but the specific recovery mechanism must be defined. (b) How is the initial bilateral state `S_AB` bootstrapped when two runtimes first establish contact? The deterministic seeding model (Design Decision 5) applies — seed from runtime identities — but cross-organizational trust establishment may require additional ceremony.
