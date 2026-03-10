# Mirror Frame Protocol (MFP)

**Symmetric frame envelope for LLM agent communication**

MFP is a protocol and runtime for secure, peer-to-peer communication between autonomous agents. It provides end-to-end encrypted channels, federated message transport, and a minimal API for agent lifecycle management.

## Features

- **Symmetric design** — no client/server distinction, all agents are peers
- **End-to-end encryption** — ChaCha20-Poly1305 AEAD with X25519 key exchange
- **Federation-ready** — bilateral channels, recovery protocols, TCP transport
- **Library-first** — runtime embeds in any Python process
- **Standalone server** — YAML-configured process for managing agents
- **Type-safe** — full type annotations with `py.typed` marker

## Installation

```bash
pip install mfp
```

Or install from source:

```bash
git clone https://github.com/Madahub-dev/MFP.git
cd MFP
pip install -e .
```

## Quickstart

**5-minute hello world between two agents:**

```python
from mfp import Runtime, RuntimeConfig, bind, mfp_send, mfp_channels

# Create a runtime
config = RuntimeConfig()
runtime = Runtime(config)

# Define two simple agent callables
def alice(channel_id, message):
    print(f"Alice received: {message.decode()}")
    return {"status": "ok"}

def bob(channel_id, message):
    print(f"Bob received: {message.decode()}")
    return {"status": "ok"}

# Bind agents to the runtime
alice_handle = bind(runtime, alice)
bob_handle = bind(runtime, bob)

# Establish a channel between them
alice_handle.establish_channel(bob_handle.agent_id, symmetric_key=None)

# Send a message from Alice to Bob
channels = mfp_channels(alice_handle)
channel_id = channels[0].channel_id

receipt = mfp_send(
    alice_handle,
    channel_id=channel_id,
    plaintext=b"Hello from Alice!"
)

print(f"Message sent, receipt: {receipt.message_id.hex()[:16]}...")

# Shutdown
runtime.shutdown()
```

Run this script to see agents communicate through MFP channels.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Application                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Public API: Runtime, bind/unbind, mfp_send/channels/status │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   ┌─────────┐         ┌─────────┐        ┌──────────┐
   │ Runtime │◄────────┤  Agent  │◄───────┤ Storage  │
   │ Pipeline│         │Lifecycle│        │  Engine  │
   └─────────┘         └─────────┘        └──────────┘
        │                                       │
        ▼                                       ▼
   ┌─────────┐                            ┌──────────┐
   │  Core:  │                            │Federation│
   │ Crypto, │                            │Transport │
   │ Frames  │                            └──────────┘
   └─────────┘
```

**Layers:**
- **Core** — cryptographic primitives, frame construction/validation
- **Runtime** — agent callable pipeline, hooks, error handling
- **Agent** — lifecycle management (bind/unbind), tool interface
- **Storage** — persistent state, message queues, channel records
- **Federation** — bilateral channels, recovery, wire protocol, TCP server

## Documentation

- [Quickstart Guide](docs/quickstart.md) — step-by-step tutorial
- [API Reference](docs/api-reference.md) — library interface documentation
- [Server Guide](docs/server-guide.md) — standalone server configuration
- [Architecture](docs/architecture.md) — design deep-dive
- [Security Model](docs/security.md) — threat model and guarantees
- [Contributing](docs/contributing.md) — development setup and guidelines

## CLI Usage

Launch the standalone server:

```bash
mfp-server --config runtime.yaml
```

Or using Python module syntax:

```bash
python -m mfp --config runtime.yaml
```

## Development

Set up development environment:

```bash
# Clone and install with dev dependencies
git clone https://github.com/madaOS/mirror-frame-protocol.git
cd mirror-frame-protocol
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=mfp --cov-report=term-missing
```

## Project Status

**Version:** 0.1.0 (Alpha)

All 5 implementation phases complete. 19 specifications implemented across 25 modules, 604 tests passing. The protocol is functional and ready for experimental use.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Credits

**Mada OS** — authored by Akil Abderrahim and Claude Opus 4.6

---

For protocol design documentation, see [design/](design/).
