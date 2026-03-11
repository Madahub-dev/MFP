# Formal Specification

```yaml
id: mfp-spec
type: spec
status: complete
created: 2026-02-28
revised: 2026-02-28
authors:
  - Akil Abderrahim
  - Claude Opus 4.6
tags: [formal-spec, ratchet, frame, encoding, distribution]
```

## Table of Contents

1. Overview
2. Notation and Conventions
3. Frame Space
4. Ratchet State Model
5. Frame Derivation and Distribution Family
6. Encoding Interface Contract
7. Multi-Runtime Tensor Operations
8. Security Bounds
9. References

---

## 1. Overview

This document is the formal specification of the Mirror Frame Protocol (MFP). It defines every mathematical object, function, and constraint that the abstract (`abstract.md`) names but does not pin down.

The abstract establishes *what* MFP is: a structural integrity mechanism that wraps agent-to-agent payloads in mirror frames derived from a ratcheting function, enforced by a deterministic runtime. This specification establishes *how* — the concrete definitions required to implement and verify the protocol:

- The frame space: what constitutes a block, what alphabet blocks are drawn from, and what dimensions a frame occupies.
- The ratchet state model: the one-way function `f`, the seed derivation algorithm, and the composition function that derives global state from local states.
- The distribution family `D`: its parameterization, its support, and the sampling procedure the runtime executes.
- The encoding interface: input and output types, integrity guarantees, and rotation semantics.
- The multi-runtime tensor: structure, bilateral state operations, and advancement protocol.
- Security bounds: concrete entropy requirements and collision probability constraints.

**Scope.** This document specifies the single-runtime protocol completely and the multi-runtime extension at the state-management level. Wire format, bootstrap ceremony, and recovery procedures for multi-runtime federation are deferred to `federation.md`. Threat analysis is deferred to `threat-model.md`. Runtime API surface and agent lifecycle are deferred to `runtime-interface.md` and `agent-lifecycle.md` respectively.

**Normative status.** All definitions in this document are normative. An implementation that deviates from any definition herein is non-conformant. Where the abstract and this specification conflict, this specification governs.

---

## 2. Notation and Conventions

### Symbols

| Symbol | Type | Definition |
|--------|------|------------|
| `M` | message | A complete protocol message: frame + encoded payload |
| `B` | block | An atomic unit of the frame space (defined in §3) |
| `k` | integer, `k ≥ 1` | Number of mirror pairs in a frame |
| `p` | integer, `p ≥ 1` | Number of blocks in the encoded payload |
| `P` | byte string | Raw payload (plaintext) |
| `E` | function | Encoding function: `P → E(P)` |
| `E⁻¹` | function | Decoding function: `E(P) → P` |
| `F` | function | Frame derivation function: `(t, Sₗ, Sg) → D` |
| `D` | distribution | Probability distribution over the frame space |
| `t` | integer, `t ≥ 0` | Transaction step (monotonically increasing per channel) |
| `Sₗ` | fixed-size byte string | Local ratchet state for a specific channel |
| `Sg` | fixed-size byte string | Global ratchet state for a runtime |
| `S_AB` | fixed-size byte string | Bilateral ratchet state between runtimes A and B |
| `f` | function | One-way ratchet advancement: `(Sₗ, frame) → Sₗ'` |
| `seed` | function | Deterministic seed derivation: `(agent_pair, channel_id) → Sₗ₀` |
| `compose` | function | Global state derivation: `(Sₗ₁, Sₗ₂, ..., Sₗₙ) → Sg` |
| `mirror` | function | Block-sequence reversal with per-block reflection (defined in §3) |
| `reverse` | function | Per-block content reflection (defined in §3) |
| `S[i, j, k]` | tensor | Ratchet state tensor for multi-runtime federation |

### Conventions

- **Byte strings** are written as sequences of octets. All fixed-size values in this specification are 32 bytes (256 bits) unless stated otherwise.
- **Function notation.** `f: A → B` denotes a function from domain `A` to codomain `B`. All functions are total unless explicitly marked partial.
- **Indexing.** Transaction steps `t` are zero-indexed per channel. The initial state before any exchange is `t = 0`; the first exchange produces the frame at `t = 0` and advances state to `t = 1`.
- **Equality.** `=` denotes definitional equality. `≡` denotes byte-level identity (the two values are the same octet sequence).
- **Sampling.** `x ← D` denotes drawing a sample `x` from distribution `D`. This is the only source of non-determinism in the protocol.
- **Concatenation.** `a ‖ b` denotes the concatenation of byte strings `a` and `b`.

---

## 3. Frame Space

### 3.1 Block Definition

A **block** `B` is a fixed-length byte string of `b` bytes:

```
B ∈ {0, 1}^(8b)    where b = 16 (128 bits)
```

The block size `b` is a protocol constant. Each block carries 128 bits of entropy — sufficient for frame security (bounds derived in §8) while remaining compact enough for efficient message construction.

### 3.2 Alphabet

The **block alphabet** `Σ` is the set of all possible blocks:

```
Σ = {0, 1}^(8b)
|Σ| = 2^128
```

Blocks are opaque byte strings. They have no internal structure, no character encoding, and no semantic content. The runtime generates and validates blocks as raw byte sequences. Agents never see blocks — only decoded payloads.

### 3.3 Frame Structure

A **frame** of depth `k` is an ordered sequence of `k` blocks:

```
frame_open  = (B₁, B₂, ..., Bₖ)
frame_close = (reverse(Bₖ), ..., reverse(B₂), reverse(B₁))
```

The frame depth `k` is a per-channel parameter set by the runtime at channel establishment. It is not negotiable by agents.

### 3.4 Mirror Operation

The **mirror** operation on a frame is the composition of two transformations:

1. **Sequence reversal.** The order of blocks is reversed: `(B₁, B₂, ..., Bₖ) → (Bₖ, ..., B₂, B₁)`.
2. **Per-block reflection.** Each block's byte sequence is reversed: `reverse(B) = (bₙ, bₙ₋₁, ..., b₂, b₁)` where `B = (b₁, b₂, ..., bₙ)`.

Formally:

```
mirror(B₁, B₂, ..., Bₖ) = (reverse(Bₖ), ..., reverse(B₂), reverse(B₁))
```

**Constraint.** A message `M` is structurally valid only if:

```
frame_close ≡ mirror(frame_open)
```

This is a necessary condition for validity, not sufficient. Frame validation additionally requires that `frame_open` matches the runtime's expected sample from `D` (§5).

### 3.5 Message Assembly

A complete message `M` is the concatenation of three regions:

```
M = frame_open ‖ E(P) ‖ frame_close
```

In block terms:

```
M = (B₁, ..., Bₖ, E(P), reverse(Bₖ), ..., reverse(B₁))
```

The total message size is `2k·b + |E(P)|` bytes. The encoded payload `E(P)` is variable-length; the frame is fixed-length for a given channel.

### 3.6 Frame Space Cardinality

The **frame space** for depth `k` is:

```
Φₖ = Σᵏ = ({0, 1}^(8b))ᵏ
|Φₖ| = 2^(128k)
```

For the default depth `k = 4`: `|Φ₄| = 2^512`.

The distribution `D` (§5) is defined over `Φₖ`. Security bounds on collision probability and guessing resistance are derived in §8 as functions of `|Φₖ|` and the entropy of `D`.

---

## 4. Ratchet State Model

### 4.1 One-Way Function `f`

The ratchet advancement function `f` takes the current local state and the sampled frame, and produces the next local state:

```
f: (Sₗ, frame) → Sₗ'
f(Sₗ, frame) = HMAC-SHA-256(key: Sₗ, message: frame₁ ‖ frame₂ ‖ ... ‖ frameₖ)
```

Where `frame₁ ‖ ... ‖ frameₖ` is the concatenation of all `k` blocks in `frame_open`.

**Properties:**

- **One-way.** Given `Sₗ'`, recovering `(Sₗ, frame)` is computationally infeasible (HMAC preimage resistance).
- **Deterministic.** The same `(Sₗ, frame)` always produces the same `Sₗ'`.
- **Fixed-size output.** `|Sₗ'| = 32` bytes regardless of frame depth `k`.
- **Forward secrecy.** Compromising `Sₗₙ` does not reveal `Sₗₙ₋₁` or any earlier state.

### 4.2 Seed Derivation

The initial local state for a channel is derived deterministically at bind time:

```
seed: (agent_pair, channel_id) → Sₗ₀
seed(agent_pair, channel_id) = HMAC-SHA-256(key: runtime_identity, message: agent_a ‖ agent_b ‖ channel_id)
```

Where:

- `agent_a`, `agent_b` are the canonical agent identifiers, lexicographically ordered so that `seed(A, B, ch) ≡ seed(B, A, ch)`. This ensures both directions of a channel share the same initial state.
- `channel_id` is a runtime-assigned channel identifier.
- `runtime_identity` is the runtime's own stable identifier, used as the HMAC key. It need not be secret — it serves as a domain separator ensuring seeds from different runtimes never collide.

**No secrecy requirement.** The seed is not a secret. Security derives from the irreversible chaining of subsequent exchanges (§4.1), not from seed entropy.

### 4.3 Global State Composition

The global ratchet state `Sg` is derived from all local states within the runtime:

```
compose: (Sₗ₁, Sₗ₂, ..., Sₗₙ) → Sg
compose(Sₗ₁, ..., Sₗₙ) = SHA-256(Sₗ₁ ‖ Sₗ₂ ‖ ... ‖ Sₗₙ)
```

The local states are concatenated in a **canonical order** defined by lexicographic sorting of channel identifiers. This ensures `Sg` is deterministic regardless of the order in which channels were established or advanced.

**Recomputation trigger.** `Sg` is recomputed every time any `Sₗ` advances. The runtime maintains `Sg` as a derived value — it is never stored independently or advanced on its own.

**Properties:**

- **Deterministic.** The same set of local states always produces the same `Sg`.
- **Fixed-size.** `|Sg| = 32` bytes regardless of the number of channels.
- **Commutative over channel set.** Canonical ordering removes dependency on insertion or advancement order.
- **Mixing.** A change to any single `Sₗ` produces an unpredictably different `Sg` (SHA-256 avalanche property).

### 4.4 State Advancement Protocol

The complete state advancement for a single exchange on channel `c` at step `t`:

```
1. Derive distribution:   D = F(t, Sₗₜ, Sg)
2. Sample frame:          frame ← D
3. Advance local state:   Sₗₜ₊₁ = f(Sₗₜ, frame)
4. Recompute global:      Sg' = compose(..., Sₗₜ₊₁, ...)
```

Steps 1–4 are **atomic**. If any step fails, no state is modified. The runtime guarantees that partial advancement — where `Sₗ` advances but `Sg` does not, or vice versa — cannot occur.

**Ordering constraint.** Step 3 must follow step 2 because the sampled frame is an input to `f`. Step 4 must follow step 3 because the new `Sₗ` is an input to `compose`. Steps 1 and 2 depend on the current (pre-advancement) state.

---

## 5. Frame Derivation and Distribution Family

### 5.1 Frame Derivation Function `F`

The frame derivation function maps the current transaction step, local state, and global state to a probability distribution over the frame space:

```
F: (t, Sₗ, Sg) → D
```

`F` is deterministic: the same inputs always produce the same distribution. The non-determinism in the protocol arises solely from sampling a frame from `D`, not from `F` itself.

### 5.2 Distribution Construction

`F` constructs `D` in two stages: seed derivation and block generation.

**Stage 1 — Distribution seed.** A 32-byte distribution seed `ds` is derived from the inputs:

```
ds = HMAC-SHA-256(key: Sₗ, message: encode_u64_be(t) ‖ Sg)
```

This binds the distribution to all three parameters. Changing any one of `t`, `Sₗ`, or `Sg` produces an unpredictably different `ds`.

**Stage 2 — PRNG instantiation.** The distribution seed `ds` initializes a cryptographic PRNG (ChaCha20) that generates the block candidates for sampling:

```
prng = ChaCha20(seed: ds)
```

The PRNG state is ephemeral — created for a single frame derivation and discarded after sampling. It is never persisted or reused.

### 5.3 Sampling Procedure

The runtime samples a frame of depth `k` from `D` as follows:

```
For i = 1 to k:
    candidate = prng.next_bytes(b)         // b = 16 bytes per block
    jitter = runtime_entropy(b)            // b bytes from OS CSPRNG
    Bᵢ = candidate XOR jitter
```

Where:

- `candidate` is the deterministic output of the seeded PRNG. Given the same `ds`, the same candidate sequence is produced.
- `jitter` is fresh entropy drawn from the operating system's CSPRNG at sampling time. This is the sole source of stochasticity.
- `XOR` combines the deterministic candidate with the fresh entropy, producing a block that is unpredictable even to an attacker who knows the ratchet state.

The resulting frame is:

```
frame = (B₁, B₂, ..., Bₖ)
```

### 5.4 Validation-Side Reconstruction

The runtime that generated the frame also validates it. Within a single runtime, there is no reconstruction problem — the runtime holds the sampled frame in memory from generation through validation within the same message lifecycle.

For cross-runtime validation, both runtimes must produce the same frame. This requires shared jitter, which is achieved through a per-step PRNG derived from the bilateral state's shared PRNG seed (§7) and the transaction step `t`. In cross-runtime mode, the sampling procedure becomes:

```
jitter_seed = HMAC-SHA-256(key: S_AB.shared_prng_seed, message: encode_u64_be(t))
jitter_prng = ChaCha20(seed: jitter_seed)

For i = 1 to k:
    candidate = prng.next_bytes(b)                  // from ds, identical on both sides
    jitter = jitter_prng.next_bytes(b)              // from per-step PRNG, identical on both sides
    Bᵢ = candidate XOR jitter
```

The per-step PRNG replaces OS entropy, making the frame deterministic given the bilateral state and step. The `jitter_prng` is ephemeral — derived fresh for each frame derivation from `(shared_prng_seed, t)` and discarded after sampling. It is not carried across messages. This eliminates PRNG synchronization as a failure mode: both runtimes derive the same jitter for step `t` independently, deterministically, and idempotently — regardless of how many times either side attempted and failed to send at that step.

### 5.5 Distribution Properties

The distribution `D` produced by `F` has the following properties:

- **Full support.** Every element of `Φₖ` has non-zero probability. No frame is *a priori* impossible. (This follows from the XOR of a uniform jitter over `{0,1}^(8b)` with any fixed candidate.)
- **Uniform marginals.** Each block `Bᵢ` is uniformly distributed over `Σ`, independent of all other blocks — because each jitter draw is independent.
- **State-dependent joint.** The joint distribution over `(B₁, ..., Bₖ)` is determined by `ds`, which is determined by `(t, Sₗ, Sg)`. Different states produce different candidate sequences, hence different joint distributions.
- **Ephemeral.** `D` exists only for the duration of a single frame derivation. It is not stored, cached, or reusable.

### 5.6 Frame Depth Selection

The frame depth `k` is set per channel at establishment time by the runtime. It is a security-performance tradeoff:

| Depth `k` | Frame entropy | Frame overhead |
|-----------|---------------|----------------|
| 1 | 128 bits | 32 bytes |
| 2 | 256 bits | 64 bytes |
| 4 (default) | 512 bits | 128 bytes |
| 8 | 1024 bits | 256 bytes |

The default depth `k = 4` provides 512 bits of frame entropy — well beyond the bounds required in §8. The runtime may increase `k` for high-security channels or decrease it where bandwidth is constrained, provided the minimum entropy bound (§8) is satisfied.

---

## 6. Encoding Interface Contract

### 6.1 Purpose

The encoding layer transforms raw payloads into an opaque form that cannot be read or tampered with outside the runtime. Encoding is infrastructure — it operates on the same plane as frame derivation and message routing, not as an application-level service.

Encoding is distinct from framing. The frame authenticates the message envelope; encoding protects the payload content. Neither substitutes for the other.

### 6.2 Interface

The runtime exposes two internal operations. These are not agent-callable tools — they are runtime-internal functions invoked automatically during message assembly and delivery.

**Encode:**

```
encode: (P, ctx) → E(P)

  P    — raw payload, arbitrary-length byte string
  ctx  — encoding context (algorithm identifier, key material, channel metadata)

  Returns E(P), a byte string satisfying the guarantees in §6.4.
```

**Decode:**

```
decode: (E(P), ctx) → P | ⊥

  E(P) — encoded payload
  ctx  — encoding context (must match the context used for encoding)

  Returns P on success, or ⊥ (failure) if integrity verification fails.
```

Decode is a partial function. If the encoded payload has been tampered with, truncated, or produced under a different context, decode returns `⊥` and the runtime discards the message. There is no partial decode — the payload is recovered in full or not at all.

### 6.3 Encoding Context

The encoding context `ctx` bundles the parameters the runtime uses to select and configure the encoding algorithm:

```
ctx = (algorithm_id, key, channel_id, t)
```

Where:

- `algorithm_id` — identifies the encoding algorithm in use. The runtime selects this; agents have no visibility into or control over the choice.
- `key` — symmetric key material for the encoding algorithm. Derived by the runtime, never exposed to agents.
- `channel_id` — binds the encoding to a specific channel, preventing cross-channel payload transplant.
- `t` — binds the encoding to a specific transaction step, preventing replay of encoded payloads from prior steps.

The context is constructed by the runtime at encode time and reconstructed at decode time. It is never serialized into the message or transmitted.

### 6.4 Guarantees

An encoding scheme is conformant if and only if it satisfies all of the following:

1. **Correctness.** For all `P` and valid `ctx`: `decode(encode(P, ctx), ctx) ≡ P`.
2. **Tamper evidence.** For any `E'(P) ≠ E(P)`: `decode(E'(P), ctx) = ⊥` with overwhelming probability. Modifying any bit of the encoded payload causes decode to fail.
3. **Context binding.** For any `ctx' ≠ ctx`: `decode(E(P, ctx), ctx') = ⊥` with overwhelming probability. An encoded payload is not portable across channels, steps, or algorithm rotations.
4. **Opacity.** `E(P)` reveals no information about `P` to an observer without `ctx`. Formally: for any two payloads `P₁`, `P₂` of equal length, `E(P₁, ctx)` and `E(P₂, ctx)` are computationally indistinguishable without knowledge of `key`.
5. **Atomicity.** Encoding and decoding are all-or-nothing. The runtime never emits a partially encoded payload or delivers a partially decoded one.

### 6.5 Concrete Algorithm

The default encoding algorithm is **AES-256-GCM**:

```
encode(P, ctx):
    nonce = HMAC-SHA-256(key: ctx.key, message: ctx.channel_id ‖ encode_u64_be(ctx.t))[:12]
    aad   = ctx.channel_id ‖ encode_u64_be(ctx.t)
    E(P)  = AES-256-GCM-Encrypt(key: ctx.key, nonce: nonce, plaintext: P, aad: aad)

decode(E(P), ctx):
    nonce = HMAC-SHA-256(key: ctx.key, message: ctx.channel_id ‖ encode_u64_be(ctx.t))[:12]
    aad   = ctx.channel_id ‖ encode_u64_be(ctx.t)
    P | ⊥ = AES-256-GCM-Decrypt(key: ctx.key, nonce: nonce, ciphertext: E(P), aad: aad)
```

Where:

- The nonce is derived deterministically from the key, channel, and step — ensuring uniqueness per (channel, step) pair without requiring nonce state.
- The AAD (additional authenticated data) binds the ciphertext to the channel and step, providing context binding (§6.4.3) through GCM's authentication tag.
- The 128-bit GCM authentication tag provides tamper evidence (§6.4.2).

### 6.6 Key Derivation

The encoding key for a channel is derived from the local ratchet state:

```
ctx.key = HMAC-SHA-256(key: Sₗ, message: "mfp-encoding-key" ‖ ctx.algorithm_id)
```

The key is re-derived on every exchange because `Sₗ` advances with each step. This provides automatic key rotation without an explicit rotation mechanism — every message is encrypted under a different key.

### 6.7 Algorithm Rotation

The runtime may rotate the encoding algorithm independently of frame rotation. Rotation is a runtime decision; agents are unaffected because they interact only with raw payloads.

Rotation procedure:

1. The runtime selects a new `algorithm_id`.
2. Subsequent `encode` calls use the new algorithm. The key derivation (§6.6) incorporates `algorithm_id`, so the key changes automatically.
3. No negotiation or signaling is required within a single runtime — the same runtime encodes and decodes.
4. For cross-runtime channels, algorithm agreement is part of the bilateral channel contract (deferred to `federation.md`).

---

## 7. Multi-Runtime Tensor Operations

### 7.1 Tensor Structure

The ratchet state tensor `S[i, j, k]` indexes all ratchet state in a federated deployment:

```
S[i, j, k]

  i — source runtime index,      i ∈ {0, ..., n-1}
  j — destination runtime index,  j ∈ {0, ..., n-1}
  k — channel index,              k ∈ {0, ..., cᵢⱼ-1}
```

Where `n` is the number of runtimes and `cᵢⱼ` is the number of channels between runtimes `i` and `j`.

### 7.2 Diagonal Entries — Internal State

Diagonal entries `S[i, i, k]` are the local ratchet states for channels internal to runtime `i`. These are the `Sₗ` values defined in §4. They are private to runtime `i` — never shared with any other runtime.

The global state for runtime `i` is:

```
Sg_i = compose(S[i, i, 0], S[i, i, 1], ..., S[i, i, cᵢᵢ-1],
               B_i0, B_i1, ..., B_i(n-1))
```

Where `B_ij` is the bilateral state between runtime `i` and runtime `j` (defined in §7.3). Internal channels and bilateral states both contribute to the global state, providing cross-runtime entropy amplification to internal channels.

The `compose` function is the same SHA-256 concatenation defined in §4.3. Inputs are concatenated in canonical order: internal channel states sorted by channel identifier, followed by bilateral states sorted by remote runtime identifier.

### 7.3 Off-Diagonal Entries — Bilateral State

Off-diagonal entries represent **bilateral ratchet states** between runtime pairs. For runtimes `A` and `B`, the bilateral state is:

```
S_AB = (ratchet_state, shared_prng_seed)
```

Where:

- `ratchet_state` — a 32-byte value that advances with each cross-runtime exchange, following the same one-way function `f` defined in §4.1.
- `shared_prng_seed` — a 32-byte value used to instantiate the shared PRNG for cross-runtime frame generation (§5.4). It advances in lockstep with `ratchet_state`.

Both components are jointly known by runtimes `A` and `B`. Neither component is derived from or reveals either runtime's internal `Sg`.

**Symmetry.** `S_AB ≡ S_BA`. The bilateral state is a joint product — there is no directional asymmetry.

### 7.4 Bilateral Seed Derivation

The initial bilateral state between runtimes `A` and `B` is derived deterministically:

```
S_AB₀.ratchet_state    = HMAC-SHA-256(key: "mfp-bilateral", message: runtime_a ‖ runtime_b)
S_AB₀.shared_prng_seed = HMAC-SHA-256(key: "mfp-bilateral-prng", message: runtime_a ‖ runtime_b)
```

Where `runtime_a`, `runtime_b` are the canonical runtime identifiers, lexicographically ordered so that `S_AB₀ ≡ S_BA₀`.

This deterministic bootstrap suffices for runtimes within the same trust domain. Cross-organizational trust establishment may require additional ceremony (deferred to `federation.md`).

### 7.5 Bilateral State Advancement

Bilateral state advances on each successful cross-runtime exchange:

```
1. Runtime A derives frame:    D = F(t, Sₗ, S_AB.ratchet_state)
                               frame ← D  (using per-step jitter from S_AB.shared_prng_seed and t, §5.4)
2. Runtime A sends framed message to Runtime B.
3. Runtime B reconstructs frame using same D, shared_prng_seed, and t.
4. Runtime B validates frame.
5. On valid frame, both runtimes advance:
     S_AB'.ratchet_state    = f(S_AB.ratchet_state, frame)
     S_AB'.shared_prng_seed = HMAC-SHA-256(key: S_AB.shared_prng_seed, message: frame₁ ‖ ... ‖ frameₖ)
6. Both runtimes recompute their respective Sg.
```

The `shared_prng_seed` advances on each successful exchange to ensure that jitter at step `t+1` is unpredictable even to an attacker who learned the jitter at step `t`. However, frame derivation at any given step is idempotent — both runtimes derive identical jitter from `(shared_prng_seed, t)` via the per-step PRNG construction (§5.4), regardless of how many derivation attempts occur at that step.

**Implicit acknowledgment.** Runtime B's response — itself framed using the advanced `S_AB'` — serves as acknowledgment that advancement occurred. Runtime A does not advance until it receives and validates this response.

**Advancement is bilateral.** Both runtimes perform the same computation on the same inputs and arrive at the same `S_AB'`. No explicit state synchronization message is required.

### 7.6 Cross-Runtime Frame Derivation

For a message from runtime `A` to runtime `B`, the frame derivation substitutes the bilateral state for the global state:

```
D = F(t, Sₗ, S_AB.ratchet_state)
```

Where `Sₗ` is the local state of the specific cross-runtime channel and `S_AB.ratchet_state` replaces `Sg`. The bilateral state serves the same mixing role as `Sg` but is scoped to the runtime pair — neither runtime's internal `Sg` is exposed.

### 7.7 Degenerate Case

When there is a single runtime (`n = 1`), the tensor collapses:

- No off-diagonal entries exist.
- `S[0, 0, k] = Sₗ` for each channel `k`.
- `Sg = compose(S[0, 0, 0], ..., S[0, 0, c-1])` — the standard single-runtime global state from §4.3.

All definitions in §4 and §5 are the degenerate case of the tensor model. No special-casing is required — the single-runtime protocol is the tensor protocol with `n = 1`.

---

## 8. Security Bounds

### 8.1 Scope

This section derives concrete bounds for the two central security parameters left open by the abstract:

1. **Frame collision probability** — the probability that an attacker's forged frame accidentally matches a valid frame.
2. **Guessing resistance** — the expected work required for an attacker to produce a valid frame without knowledge of the ratchet state.

All bounds assume a computationally bounded adversary who cannot invert SHA-256, HMAC-SHA-256, or AES-256-GCM.

### 8.2 Frame Guessing — Single Attempt

An attacker who does not know the ratchet state `(Sₗ, Sg)` must guess a frame drawn uniformly from the frame space `Φₖ`.

The probability of a correct guess on a single attempt:

```
P(guess) = 1 / |Φₖ| = 1 / 2^(128k)
```

For the default depth `k = 4`:

```
P(guess) = 1 / 2^512 ≈ 7.46 × 10⁻¹⁵⁵
```

This is the per-message forgery probability. Each message requires an independent guess because the ratchet advances on every exchange — a failed guess does not provide information about the next frame.

### 8.3 Frame Guessing — Repeated Attempts

An attacker making `q` independent guessing attempts against distinct messages on the same channel:

```
P(any success in q attempts) = 1 - (1 - 1/2^(128k))^q ≈ q / 2^(128k)    for q << 2^(128k)
```

For `k = 4` and `q = 2^64` (an astronomically large number of attempts):

```
P ≈ 2^64 / 2^512 = 1 / 2^448 ≈ 1.38 × 10⁻¹³⁵
```

The protocol provides a security margin of at least `128k - 64` bits against online guessing attacks at the `2^64` query level.

### 8.4 Frame Collision — Birthday Bound

The birthday bound governs the probability that two independently sampled frames collide (are identical). This is relevant if an attacker can observe frames and hopes to find a repeated frame to exploit.

For `q` frames sampled from `Φₖ`:

```
P(collision) ≈ q² / (2 · |Φₖ|) = q² / 2^(128k + 1)
```

For `k = 4` and `q = 2^64`:

```
P(collision) ≈ 2^128 / 2^513 = 1 / 2^385 ≈ 1.59 × 10⁻¹¹⁶
```

Even at the birthday bound, the collision probability is negligible for any practical number of observations.

### 8.5 Stochastic Trajectory Unpredictability

Because the sampled frame feeds back into the ratchet — `Sₗₙ = f(Sₗₙ₋₁, frameₙ)` — the state trajectory is stochastic. An attacker who knows `Sₗₙ₋₁` but not the sampled frame cannot predict `Sₗₙ`.

The entropy of `Sₗₙ` given `Sₗₙ₋₁`:

```
H(Sₗₙ | Sₗₙ₋₁) = H(frameₙ) = 128k bits
```

This follows from the uniform marginals property (§5.5): each block contributes 128 bits of entropy from the OS CSPRNG jitter, and the `k` blocks are independent.

After `m` steps without observation, the cumulative uncertainty is:

```
H(Sₗₙ₊ₘ | Sₗₙ) = min(256, 128k · m) bits
```

Capped at 256 bits by the state size (32 bytes). For `k = 4`, the state reaches full entropy saturation after a single step (`512 > 256`).

### 8.6 Forward Secrecy Bound

An attacker who compromises the current state `Sₗₙ` attempts to recover a prior state `Sₗₙ₋ⱼ`. This requires inverting:

```
Sₗₙ = f(f(...f(Sₗₙ₋ⱼ, frameₙ₋ⱼ)..., frameₙ₋₁), frameₙ)
```

Each application of `f = HMAC-SHA-256` requires a preimage attack. The work for recovering `Sₗₙ₋ⱼ` from `Sₗₙ`:

```
W(recover Sₗₙ₋ⱼ) ≥ j · 2^256 HMAC evaluations
```

For `j = 1` (recovering the immediately prior state): `W ≥ 2^256`. Forward secrecy holds with 256-bit security per step.

### 8.7 Encoding Security

The encoding layer (§6) using AES-256-GCM provides:

- **Confidentiality:** IND-CPA security with 256-bit key strength. An attacker without the key cannot distinguish encoded payloads from random.
- **Integrity:** INT-CTXT security with 128-bit authentication tag. The probability of forging a valid ciphertext is at most `1 / 2^128` per attempt.
- **Nonce uniqueness:** The deterministic nonce derivation (§6.5) guarantees uniqueness as long as `(channel_id, t)` pairs are unique — which the protocol guarantees by construction (monotonically increasing `t` per channel).

### 8.8 Minimum Entropy Requirement

The minimum frame depth `k` for a conformant implementation is constrained by:

```
128k ≥ 256
k ≥ 2
```

This ensures:

- At least 256 bits of frame entropy per message — exceeding the birthday bound for `2^128` observations.
- Single-step entropy saturation of the 256-bit ratchet state.
- A guessing probability of at most `1 / 2^256` per attempt.

The default `k = 4` exceeds the minimum by a factor of two, providing defense in depth. Implementations MUST NOT use `k < 2`. Implementations SHOULD use `k ≥ 4` unless bandwidth constraints require otherwise.

### 8.9 Summary of Bounds

| Parameter | Bound | At `k = 4` |
|-----------|-------|------------|
| Single-guess forgery | `1 / 2^(128k)` | `1 / 2^512` |
| Online guessing (`q = 2^64`) | `q / 2^(128k)` | `1 / 2^448` |
| Birthday collision (`q = 2^64`) | `q² / 2^(128k+1)` | `1 / 2^385` |
| Per-step trajectory entropy | `128k` bits | 512 bits |
| Forward secrecy per step | `2^256` HMAC work | `2^256` |
| Encoding forgery | `1 / 2^128` per attempt | `1 / 2^128` |
| Minimum `k` | `k ≥ 2` | satisfied |

---

## 9. References

| Ref | Document | Relevance |
|-----|----------|-----------|
| [1] | `abstract.md` | MFP abstract — defines core structure, design decisions, and open questions that this specification formalizes |
| [2] | NIST SP 800-185 | SHA-256 and HMAC specification — basis for `f`, `seed`, `compose` |
| [3] | RFC 8439 | ChaCha20 specification — basis for PRNG in frame derivation (§5.2) |
| [4] | NIST SP 800-38D | AES-GCM specification — basis for default encoding algorithm (§6.5) |
| [5] | `threat-model.md` | Threat analysis — traces attack vectors through the constructions defined here |
| [6] | `runtime-interface.md` | Runtime API — implements the interfaces and lifecycle defined here |
| [7] | `agent-lifecycle.md` | Agent binding — implements seed derivation and channel establishment defined here |
| [8] | `federation.md` | Multi-runtime federation — extends bilateral state operations defined in §7 |

---

*MFP — authored by Akil Abderrahim and Claude Opus 4.6*
