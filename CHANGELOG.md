# Changelog

All notable changes to the Mirror Frame Protocol (MFP) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-10

### Added

**Core Protocol**
- Symmetric mirror frame construction and validation
- ChaCha20-Poly1305 AEAD encryption for payloads
- Temporal ratchet for replay attack prevention
- HMAC-SHA256 based frame derivation
- Forward-secure ratchet state evolution

**Runtime & Agent Lifecycle**
- `Runtime` coordinator for agent management
- `bind()` / `unbind()` agent lifecycle functions
- `AgentHandle` interface for agent operations
- Agent state machine (BOUND → ACTIVE → QUARANTINED → UNBOUND)
- Quarantine system with rate limiting and size constraints
- Retry logic with configurable policies

**Storage**
- SQLite-based persistence engine
- Transactional operations for ratchet state
- Optional at-rest encryption with master key
- WAL mode support for concurrent access
- Message queuing (FIFO per channel)

**Federation**
- Bilateral channel establishment between runtimes
- Deterministic and ceremonial bootstrap modes
- X25519 Diffie-Hellman key exchange
- TCP transport server and client
- Recovery protocol for state divergence detection
- Wire protocol with routing headers

**Protocol Tools**
- `mfp_send()` — send encrypted messages
- `mfp_channels()` — list agent channels
- `mfp_status()` — query agent status

**Standalone Server**
- YAML-based configuration
- CLI with `mfp-server` command
- Support for `python -m mfp` invocation
- Graceful shutdown on SIGINT/SIGTERM

**Testing**
- 604 tests across unit, integration, and e2e suites
- ~95% code coverage
- Property-based tests for frame symmetry
- Federation and recovery protocol tests

**Documentation**
- Comprehensive README with quickstart
- API reference (hand-written)
- Server configuration guide
- Architecture deep-dive
- Security model and threat analysis
- Contributing guidelines
- Protocol design specifications

**Packaging**
- PEP 621 compliant `pyproject.toml`
- pip installable (`pip install mfp`)
- Type annotations with `py.typed` marker
- MIT license
- Python 3.11+ support

### Notes

This is the initial alpha release. All 5 implementation phases complete, with 19 specifications implemented across 25 modules. The protocol is functional and ready for experimental use.

**Not yet production-ready:** This release is for evaluation and testing. Production hardening, audit, and performance optimization are planned for future releases.

---

## Versioning Policy

- **Major (X.0.0)**: Breaking API changes, protocol changes
- **Minor (0.X.0)**: New features, backward-compatible
- **Patch (0.0.X)**: Bug fixes, documentation updates

---

[0.1.0]: https://github.com/Madahub-dev/MFP/releases/tag/v0.1.0
