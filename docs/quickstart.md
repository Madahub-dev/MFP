# Quickstart Guide

This guide walks you through creating your first MFP agents, establishing a secure channel, and exchanging messages.

## Prerequisites

- Python 3.11+
- MFP installed: `pip install mfp`

## Step 1: Create a Runtime

Every MFP application starts with a `Runtime`:

```python
from mfp import Runtime, RuntimeConfig

# Create runtime configuration
config = RuntimeConfig()

# Initialize the runtime
runtime = Runtime(config)
```

The `Runtime` manages agent lifecycles, message routing, and cryptographic operations.

## Step 2: Define Agent Callables

Agents are Python callables that receive and process messages:

```python
def alice(channel_id, message):
    """Alice's message handler."""
    print(f"[Alice] Received on {channel_id.hex()[:8]}: {message.decode()}")
    return {"status": "received", "agent": "alice"}

def bob(channel_id, message):
    """Bob's message handler."""
    print(f"[Bob] Received on {channel_id.hex()[:8]}: {message.decode()}")
    return {"status": "received", "agent": "bob"}
```

**Signature:** `(channel_id: bytes, message: bytes) -> dict`

The return value is metadata that can be used for logging, debugging, or hook integration.

## Step 3: Bind Agents to the Runtime

Binding registers an agent with the runtime and assigns it a unique `AgentId`:

```python
from mfp import bind

# Bind agents
alice_handle = bind(runtime, alice)
bob_handle = bind(runtime, bob)

print(f"Alice ID: {alice_handle.agent_id.hex()[:16]}...")
print(f"Bob ID: {bob_handle.agent_id.hex()[:16]}...")
```

**AgentHandle** provides the interface for all agent operations: establishing channels, sending messages, querying status.

## Step 4: Establish a Channel

Create an encrypted channel between Alice and Bob:

```python
# Alice initiates a channel to Bob
alice_handle.establish_channel(
    peer_id=bob_handle.agent_id,
    symmetric_key=None  # Auto-generate a secure key
)

print("Channel established!")
```

When `symmetric_key=None`, MFP generates a 32-byte ChaCha20-Poly1305 key automatically.

## Step 5: Send Messages

Use the `mfp_send` tool to send encrypted messages:

```python
from mfp import mfp_send, mfp_channels

# Get Alice's channels
channels = mfp_channels(alice_handle)
channel = channels[0]

print(f"Channel ID: {channel.channel_id.hex()[:16]}...")
print(f"Peer: {channel.peer_id.hex()[:16]}...")

# Alice sends to Bob
receipt = mfp_send(
    alice_handle,
    channel_id=channel.channel_id,
    plaintext=b"Hello Bob, this is Alice!"
)

print(f"Message sent! Receipt: {receipt.message_id.hex()[:16]}...")
```

**Receipt** contains:
- `message_id`: unique message identifier
- `sequence_number`: monotonic counter for ordering
- `timestamp_ns`: nanosecond-precision timestamp

## Step 6: Query Agent Status

Check agent state at any time:

```python
from mfp import mfp_status

# Get Alice's status
status = mfp_status(alice_handle)

print(f"Agent state: {status.state}")  # AgentStatus.ACTIVE
print(f"Channels: {status.channel_count}")
print(f"Pending messages: {status.pending_message_count}")
```

**AgentStatus** states:
- `BOUND` — registered, no channels yet
- `ACTIVE` — has at least one channel
- `QUARANTINED` — isolated due to errors
- `UNBOUND` — deregistered

## Step 7: Two-Way Communication

Bob can send back to Alice on the same channel:

```python
# Bob gets his channels (same channel, from his perspective)
bob_channels = mfp_channels(bob_handle)
bob_channel = bob_channels[0]

# Bob replies
reply_receipt = mfp_send(
    bob_handle,
    channel_id=bob_channel.channel_id,
    plaintext=b"Hi Alice! Message received."
)

print(f"Reply sent: {reply_receipt.message_id.hex()[:16]}...")
```

Both Alice and Bob share the same `ChannelId`, ensuring symmetric communication.

## Step 8: Shutdown

Clean up when done:

```python
from mfp import unbind

# Unbind agents (closes channels, flushes state)
unbind(alice_handle)
unbind(bob_handle)

# Shutdown the runtime
runtime.shutdown()

print("Runtime shut down cleanly.")
```

## Complete Example

```python
from mfp import (
    Runtime, RuntimeConfig,
    bind, unbind,
    mfp_send, mfp_channels, mfp_status
)

# Agent callables
def alice(channel_id, message):
    print(f"[Alice] {message.decode()}")
    return {"status": "ok"}

def bob(channel_id, message):
    print(f"[Bob] {message.decode()}")
    return {"status": "ok"}

# Initialize
config = RuntimeConfig()
runtime = Runtime(config)

# Bind
alice_handle = bind(runtime, alice)
bob_handle = bind(runtime, bob)

# Establish channel
alice_handle.establish_channel(bob_handle.agent_id, symmetric_key=None)

# Send message
channels = mfp_channels(alice_handle)
receipt = mfp_send(
    alice_handle,
    channel_id=channels[0].channel_id,
    plaintext=b"Hello from Alice!"
)

print(f"Sent: {receipt.message_id.hex()[:16]}...")

# Cleanup
unbind(alice_handle)
unbind(bob_handle)
runtime.shutdown()
```

## Next Steps

- **[API Reference](api-reference.md)** — explore the full library interface
- **[Server Guide](server-guide.md)** — run MFP as a standalone process
- **[Architecture](architecture.md)** — understand the internal design
- **[Security Model](security.md)** — learn about MFP's security guarantees

---

**Tip:** For production use, persist agent state using the `StorageEngine` and configure federation via `TransportServer`. See the [Server Guide](server-guide.md) for details.
