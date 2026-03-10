# API Reference

Complete reference for the MFP public API.

## Imports

```python
from mfp import (
    # Core
    Runtime,
    RuntimeConfig,
    # Agent lifecycle
    AgentHandle,
    bind,
    unbind,
    # Protocol tools
    mfp_send,
    mfp_channels,
    mfp_status,
    # Types
    AgentId,
    AgentStatus,
    AgentError,
    AgentErrorCode,
    ChannelId,
    ChannelInfo,
    Receipt,
)
```

---

## Core

### `Runtime`

The central coordinator for all MFP operations.

**Constructor:**
```python
Runtime(config: RuntimeConfig)
```

**Methods:**

- **`shutdown() -> None`**
  Gracefully shutdown the runtime, flushing all pending operations and closing storage.

**Example:**
```python
config = RuntimeConfig()
runtime = Runtime(config)
# ... use runtime ...
runtime.shutdown()
```

---

### `RuntimeConfig`

Configuration for runtime behavior.

**Constructor:**
```python
RuntimeConfig(
    max_retries: int = 3,
    retry_delay_ms: int = 100,
    enable_hooks: bool = False,
)
```

**Parameters:**

- `max_retries` — Maximum retry attempts for transient errors (default: 3)
- `retry_delay_ms` — Delay between retries in milliseconds (default: 100)
- `enable_hooks` — Enable lifecycle hooks for extensions (default: False)

**Example:**
```python
config = RuntimeConfig(
    max_retries=5,
    retry_delay_ms=200,
    enable_hooks=True
)
```

---

## Agent Lifecycle

### `bind()`

Register an agent callable with a runtime.

**Signature:**
```python
bind(
    runtime: Runtime,
    agent_callable: AgentCallable,
) -> AgentHandle
```

**Parameters:**

- `runtime` — The runtime to bind to
- `agent_callable` — A callable with signature `(channel_id: bytes, message: bytes) -> dict`

**Returns:** `AgentHandle` for interacting with the agent

**Raises:** `AgentError` if binding fails

**Example:**
```python
def my_agent(channel_id, message):
    return {"status": "ok"}

handle = bind(runtime, my_agent)
```

---

### `unbind()`

Deregister an agent and close all its channels.

**Signature:**
```python
unbind(handle: AgentHandle) -> None
```

**Parameters:**

- `handle` — The agent handle to unbind

**Side effects:**
- Closes all channels
- Flushes pending messages
- Transitions agent to `UNBOUND` state

**Example:**
```python
unbind(handle)
# handle is now invalid
```

---

### `AgentHandle`

Handle for an active agent, providing access to agent operations.

**Attributes:**

- `agent_id: bytes` — Unique 32-byte agent identifier (read-only)
- `runtime: Runtime` — Associated runtime (read-only)

**Methods:**

- **`establish_channel(peer_id: bytes, symmetric_key: bytes | None) -> ChannelId`**
  Create a new encrypted channel to a peer agent.

  - `peer_id` — Target agent's ID (32 bytes)
  - `symmetric_key` — ChaCha20-Poly1305 key (32 bytes) or `None` to auto-generate

  Returns the new `ChannelId`.

**Example:**
```python
handle = bind(runtime, my_agent)
channel_id = handle.establish_channel(peer_id, symmetric_key=None)
```

---

## Protocol Tools

### `mfp_send()`

Send an encrypted message on a channel.

**Signature:**
```python
mfp_send(
    handle: AgentHandle,
    channel_id: bytes,
    plaintext: bytes,
) -> Receipt
```

**Parameters:**

- `handle` — Sending agent's handle
- `channel_id` — Target channel ID (32 bytes)
- `plaintext` — Message payload (bytes)

**Returns:** `Receipt` with message ID, sequence number, and timestamp

**Raises:**
- `AgentError(INVALID_STATE)` — Agent not in ACTIVE state
- `AgentError(CHANNEL_NOT_FOUND)` — Channel doesn't exist

**Example:**
```python
receipt = mfp_send(
    handle,
    channel_id=channel.channel_id,
    plaintext=b"Hello, world!"
)
print(receipt.message_id.hex())
```

---

### `mfp_channels()`

List all channels for an agent.

**Signature:**
```python
mfp_channels(handle: AgentHandle) -> list[ChannelInfo]
```

**Parameters:**

- `handle` — Agent handle to query

**Returns:** List of `ChannelInfo` objects

**Example:**
```python
channels = mfp_channels(handle)
for ch in channels:
    print(f"Channel: {ch.channel_id.hex()[:16]}")
    print(f"Peer: {ch.peer_id.hex()[:16]}")
```

---

### `mfp_status()`

Get current status of an agent.

**Signature:**
```python
mfp_status(handle: AgentHandle) -> AgentStatusInfo
```

**Parameters:**

- `handle` — Agent handle to query

**Returns:** Object with fields:
- `state: AgentStatus` — Current state (BOUND, ACTIVE, QUARANTINED, UNBOUND)
- `channel_count: int` — Number of active channels
- `pending_message_count: int` — Messages awaiting delivery

**Example:**
```python
status = mfp_status(handle)
if status.state == AgentStatus.ACTIVE:
    print(f"{status.channel_count} channels active")
```

---

## Types

### `AgentId`

Type alias for agent identifiers.

```python
AgentId = bytes  # 32 bytes
```

---

### `ChannelId`

Type alias for channel identifiers.

```python
ChannelId = bytes  # 32 bytes
```

---

### `AgentStatus`

Enum representing agent lifecycle states.

**Values:**

- `BOUND` — Agent registered, no channels established
- `ACTIVE` — Agent has at least one active channel
- `QUARANTINED` — Agent isolated due to errors
- `UNBOUND` — Agent deregistered

**Example:**
```python
from mfp import AgentStatus

if status.state == AgentStatus.ACTIVE:
    print("Agent is active")
```

---

### `ChannelInfo`

Information about an established channel.

**Fields:**

- `channel_id: bytes` — Channel identifier (32 bytes)
- `peer_id: bytes` — Peer agent identifier (32 bytes)
- `established_at: int` — Timestamp (nanoseconds since epoch)

**Example:**
```python
channels = mfp_channels(handle)
info = channels[0]
print(f"Channel with {info.peer_id.hex()[:8]}")
```

---

### `Receipt`

Proof of message send operation.

**Fields:**

- `message_id: bytes` — Unique message identifier (32 bytes)
- `sequence_number: int` — Monotonic counter for this channel
- `timestamp_ns: int` — Send timestamp (nanoseconds since epoch)

**Example:**
```python
receipt = mfp_send(handle, channel_id, b"message")
print(f"Message ID: {receipt.message_id.hex()}")
print(f"Sequence: {receipt.sequence_number}")
```

---

### `AgentError`

Exception raised for agent operation failures.

**Constructor:**
```python
AgentError(code: AgentErrorCode, message: str)
```

**Attributes:**

- `code: AgentErrorCode` — Error category
- `message: str` — Human-readable description

**Example:**
```python
from mfp import AgentError, AgentErrorCode

try:
    mfp_send(handle, bad_channel_id, b"test")
except AgentError as e:
    if e.code == AgentErrorCode.CHANNEL_NOT_FOUND:
        print("Channel doesn't exist")
```

---

### `AgentErrorCode`

Enum of error categories.

**Values:**

- `INVALID_STATE` — Operation invalid for current agent state
- `CHANNEL_NOT_FOUND` — Channel ID doesn't exist
- `CRYPTO_ERROR` — Cryptographic operation failed
- `STORAGE_ERROR` — Persistent storage failure
- `FEDERATION_ERROR` — Cross-runtime communication failure
- `QUARANTINE_TRIGGERED` — Agent moved to quarantine

**Example:**
```python
from mfp import AgentErrorCode

if error.code == AgentErrorCode.INVALID_STATE:
    print("Agent not ready for this operation")
```

---

## Thread Safety

**Note:** The MFP API is **not thread-safe**. All operations on a `Runtime` or `AgentHandle` should be performed from a single thread, or externally synchronized.

For concurrent applications, use separate `Runtime` instances per thread/process.

---

## See Also

- [Quickstart Guide](quickstart.md) — hands-on tutorial
- [Server Guide](server-guide.md) — standalone server configuration
- [Architecture](architecture.md) — internal design
