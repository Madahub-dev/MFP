# Server Guide

The MFP standalone server runs as a configured process, managing agents, channels, and federation peers. It's ideal for production deployments where agents need persistent state and cross-runtime communication.

## Overview

The server composes three components:
- **Runtime** — agent lifecycle and message routing
- **StorageEngine** — persistent state (SQLite)
- **TransportServer** — TCP federation endpoint

Configuration is declarative via YAML files.

## Quick Start

Create a configuration file `mfp.yaml`:

```yaml
runtime:
  deployment_id: "prod-cluster-01"
  instance_id: "node-alpha"
  default_frame_depth: 4

storage:
  path: "/var/lib/mfp/mfp.db"
  encrypt_at_rest: false
  wal_mode: true

transport:
  host: "0.0.0.0"
  port: 9876

agents:
  - name: "alice"
    type: "callback"
  - name: "bob"
    type: "callback"

channels:
  - agents: ["alice", "bob"]
    depth: 4

log_level: "INFO"
```

Launch the server:

```bash
mfp-server --config mfp.yaml
```

Or:

```bash
python -m mfp --config mfp.yaml
```

## Configuration Reference

### Runtime

Controls runtime behavior and quarantine policies.

```yaml
runtime:
  deployment_id: "my-deployment"    # Deployment identifier (string)
  instance_id: "instance-001"       # Instance identifier (string)
  default_frame_depth: 4            # Default onion layers (int, default: 4)

quarantine:
  validation_failure_threshold: 3   # Errors before quarantine (int, default: 3)
  max_message_rate: 100             # Messages/second limit (0 = unlimited)
  max_payload_size: 1048576         # Max message bytes (0 = unlimited)
```

**deployment_id / instance_id:**
Used for federation identity. Together they form the `RuntimeId`.

**default_frame_depth:**
Number of onion encryption layers for outbound messages.

**Quarantine:**
- `validation_failure_threshold`: Isolate agent after N crypto/validation errors
- `max_message_rate`: Rate limit (messages per second, 0 disables)
- `max_payload_size`: Maximum plaintext size in bytes (0 disables)

---

### Storage

Configures the persistent storage engine (SQLite).

```yaml
storage:
  path: "/var/lib/mfp/state.db"     # Database file path (string)
  encrypt_at_rest: false            # Encrypt database (bool, default: false)
  master_key_file: ""               # Path to 32-byte key file (string)
  wal_mode: true                    # Enable Write-Ahead Log (bool, default: true)
```

**path:**
SQLite database file location. Created if it doesn't exist.

**encrypt_at_rest:**
If `true`, encrypts the database using SQLCipher. Requires `master_key_file`.

**master_key_file:**
Path to a file containing a 32-byte master key (for encryption at rest).

**wal_mode:**
Enables SQLite Write-Ahead Logging for better concurrency.

---

### Transport

TCP server for federated message exchange.

```yaml
transport:
  host: "0.0.0.0"          # Bind address (string, default: "0.0.0.0")
  port: 9876               # Listen port (int, default: 9876)
  connect_timeout: 30.0    # Connect timeout (seconds, default: 30.0)
  read_timeout: 30.0       # Read timeout (seconds, default: 30.0)
  write_timeout: 30.0      # Write timeout (seconds, default: 30.0)
```

**host / port:**
Server bind address and port.

**Timeouts:**
Connection and I/O timeout values for TCP operations.

---

### Recovery

Federation recovery protocol settings.

```yaml
recovery:
  max_step_gap: 5          # Max allowed step sequence gap (int, default: 5)
  max_attempts: 3          # Max recovery attempts (int, default: 3)
  timeout_seconds: 30      # Recovery timeout (int, default: 30)
```

**max_step_gap:**
If sequence numbers differ by more than this, trigger recovery.

**max_attempts:**
Maximum recovery negotiation rounds before giving up.

**timeout_seconds:**
Time limit for the entire recovery process.

---

### Agents

Define agents to bind at server startup.

```yaml
agents:
  - name: "alice"
    type: "callback"

  - name: "bob"
    type: "callback"
```

**name:**
Unique agent identifier (string).

**type:**
Agent implementation type. Supported values:
- `"callback"` — Simple in-process callable
- `"subprocess"` — External process (future)
- `"webhook"` — HTTP endpoint (future)
- `"llm_api"` — LLM provider integration (future)

**Note:** Only `"callback"` is currently implemented. Other types are placeholders for future extensions.

---

### Channels

Pre-establish channels between agents at startup.

```yaml
channels:
  - agents: ["alice", "bob"]
    depth: 4

  - agents: ["bob", "charlie"]
    depth: 6
```

**agents:**
Two-element list `[sender, receiver]` mapping to agent names.

**depth:**
Onion encryption depth for this channel (default: 4).

---

### Federation

Configure remote runtime peers for federated channels.

```yaml
federation:
  peers:
    - runtime_id: "remote-cluster-01/node-beta"
      endpoint: "192.168.1.10:9876"
      bootstrap: "deterministic"

    - runtime_id: "partner-org/gateway"
      endpoint: "partner.example.com:9876"
      bootstrap: "ceremonial"
```

**runtime_id:**
Remote runtime identifier, format: `"deployment_id/instance_id"`.

**endpoint:**
TCP address in `"host:port"` format.

**bootstrap:**
Key exchange method:
- `"deterministic"` — Derive shared key from runtime IDs (no handshake)
- `"ceremonial"` — X25519 Diffie-Hellman key exchange

**Deterministic bootstrap:**
Fast, no network round-trip, but both runtimes must share the same derivation secret.

**Ceremonial bootstrap:**
Secure key exchange via DH, suitable for untrusted networks.

---

### Logging

Set log verbosity.

```yaml
log_level: "INFO"   # DEBUG, INFO, WARNING, ERROR
```

---

## Example: Two-Node Federation

**Node A** (`node-a.yaml`):

```yaml
runtime:
  deployment_id: "prod"
  instance_id: "node-a"

storage:
  path: "/var/lib/mfp/node-a.db"

transport:
  host: "0.0.0.0"
  port: 9876

agents:
  - name: "alice"
    type: "callback"

federation:
  peers:
    - runtime_id: "prod/node-b"
      endpoint: "192.168.1.20:9876"
      bootstrap: "deterministic"

log_level: "INFO"
```

**Node B** (`node-b.yaml`):

```yaml
runtime:
  deployment_id: "prod"
  instance_id: "node-b"

storage:
  path: "/var/lib/mfp/node-b.db"

transport:
  host: "0.0.0.0"
  port: 9876

agents:
  - name: "bob"
    type: "callback"

federation:
  peers:
    - runtime_id: "prod/node-a"
      endpoint: "192.168.1.10:9876"
      bootstrap: "deterministic"

log_level: "INFO"
```

Launch both:

```bash
# Terminal 1
mfp-server --config node-a.yaml

# Terminal 2
mfp-server --config node-b.yaml
```

Alice (on node-a) can now establish channels with Bob (on node-b) via the transport layer.

---

## CLI Options

```
usage: mfp-server [-h] [--config CONFIG] [--log-level {DEBUG,INFO,WARNING,ERROR}]

options:
  -h, --help            Show help message
  --config CONFIG       Path to YAML configuration file
  --log-level LEVEL     Override log level from config (DEBUG, INFO, WARNING, ERROR)
```

**Examples:**

```bash
# Use config file
mfp-server --config prod.yaml

# Override log level
mfp-server --config prod.yaml --log-level DEBUG

# Python module syntax
python -m mfp --config dev.yaml
```

---

## Operational Notes

### Persistence

All agent state, channel records, and message queues are stored in the SQLite database specified by `storage.path`. Stopping and restarting the server preserves state.

### Shutdown

The server handles `SIGINT` (Ctrl+C) and `SIGTERM` gracefully:
1. Stops accepting new messages
2. Flushes pending queues
3. Closes storage cleanly

### Monitoring

Server logs include:
- Agent bind/unbind events
- Channel establishment
- Message send/receive
- Quarantine triggers
- Federation handshakes
- Recovery protocol activations

Use `--log-level DEBUG` for verbose diagnostic output.

### Security

- **Transport encryption:** Federation uses ChaCha20-Poly1305 AEAD
- **At-rest encryption:** Enable via `storage.encrypt_at_rest` with a secure `master_key_file`
- **Firewall:** Restrict access to the transport port (default 9876) to trusted peers only

---

## See Also

- [API Reference](api-reference.md) — library interface
- [Quickstart Guide](quickstart.md) — hands-on tutorial
- [Architecture](architecture.md) — internal design
- [Security Model](security.md) — threat model and guarantees
