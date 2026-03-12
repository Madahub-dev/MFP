# Contributing Guide

Thank you for your interest in contributing to the Mirror Frame Protocol! This guide covers development setup, coding standards, and the contribution workflow.

---

## Development Setup

### Prerequisites

- **Python 3.11+** (3.12 or 3.13 recommended)
- **Git** for version control
- **pip** for dependency management

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/Madahub-dev/MFP.git
cd MFP

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

This installs:
- **Core dependencies:** `cryptography`, `pyyaml`
- **Dev dependencies:** `pytest`, `pytest-asyncio`

### Verify Installation

```bash
# Check package is installed
python -c "import mfp; print(mfp.__version__)"

# Run the test suite
pytest

# Check CLI works
mfp-server --help
```

Expected output:
```
0.1.0
============================== test session starts ===============================
...
============================== 604 passed in X.XXs ===============================
usage: mfp.server [-h] [--config CONFIG] ...
```

---

## Project Structure

```
mirror-frame-protocol/
├── mfp/                    # Source code
│   ├── core/               # Cryptographic primitives, frames, ratchet
│   ├── runtime/            # Execution pipeline, quarantine
│   ├── agent/              # Lifecycle, tools
│   ├── storage/            # SQLite persistence
│   ├── federation/         # Cross-runtime transport
│   ├── server.py           # Standalone server
│   └── __init__.py         # Public API exports
│
├── tests/                  # Test suite
│   ├── unit/               # Unit tests (pure functions)
│   ├── integration/        # Integration tests (runtime + storage)
│   └── e2e/                # End-to-end tests (full scenarios)
│
├── docs/                   # User documentation
│   ├── quickstart.md
│   ├── api-reference.md
│   ├── server-guide.md
│   ├── architecture.md
│   ├── security.md
│   └── contributing.md     # ← You are here
│
├── design/                 # Protocol design specs
│   ├── spec.md             # Formal specification
│   ├── federation.md       # Multi-runtime extension
│   ├── threat-model.md     # Security analysis
│   └── ...
│
├── LICENSE                 # Apache 2.0 license
├── README.md               # Project overview
└── pyproject.toml          # Package metadata
```

---

## Running Tests

### Full Test Suite

```bash
pytest
```

### Specific Test Modules

```bash
# Unit tests only
pytest tests/unit/

# E2E tests only
pytest tests/e2e/

# Specific file
pytest tests/unit/test_frame.py

# Specific test function
pytest tests/e2e/test_agent_e2e.py::TestTwoAgentConversationViaTools::test_10_message_alternating_via_tools
```

### Coverage Report

```bash
pytest --cov=mfp --cov-report=term-missing
```

Aim for **>90% coverage** on new code.

### Watch Mode (Auto-Rerun on Changes)

```bash
pip install pytest-watch
ptw
```

---

## Code Style

### Python Version

- **Minimum:** Python 3.11 (for type hints like `str | None`)
- **Target:** Python 3.12+

### Type Hints

**Always use type annotations:**

```python
# Good
def derive_agent_id(runtime_id: bytes, seed: bytes) -> bytes:
    ...

# Bad
def derive_agent_id(runtime_id, seed):
    ...
```

### Formatting

**Follow PEP 8:**
- 4 spaces for indentation (no tabs)
- Line length: 88 characters (Black default)
- Use `from __future__ import annotations` for forward references

**Run Black (optional but recommended):**

```bash
pip install black
black mfp/ tests/
```

### Docstrings

Use **concise docstrings** for public API functions:

```python
def mfp_send(handle: AgentHandle, channel_id: bytes, plaintext: bytes) -> Receipt:
    """Send an encrypted message on a channel.

    Args:
        handle: Sending agent's handle
        channel_id: Target channel (32 bytes)
        plaintext: Message payload

    Returns:
        Receipt with message ID and metadata

    Raises:
        AgentError: If agent is not in ACTIVE state
    """
    ...
```

For internal functions, a one-line docstring is sufficient.

### Naming Conventions

- **Functions/variables:** `snake_case`
- **Classes:** `PascalCase`
- **Constants:** `UPPER_SNAKE_CASE`
- **Type aliases:** `PascalCase` (e.g., `AgentId`)

---

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

**Branch naming:**
- `feature/` — new functionality
- `fix/` — bug fixes
- `docs/` — documentation changes
- `refactor/` — code restructuring (no behavior change)

### 2. Write Code

- Keep changes focused (one logical change per PR)
- Add tests for new functionality
- Update documentation if adding public API surface

### 3. Run Tests Locally

```bash
# Full test suite
pytest

# With coverage
pytest --cov=mfp --cov-report=term-missing
```

All tests must pass before submitting a PR.

### 4. Commit

**Use descriptive commit messages:**

```bash
git commit -m "Add support for custom transport backends"
```

**Good commit messages:**
- Start with a verb: "Add", "Fix", "Update", "Remove"
- Be specific: "Fix ratchet state corruption on quarantine" (not "Fix bug")
- Keep subject line under 72 characters

### 5. Push and Open a PR

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub.

---

## Pull Request Guidelines

### PR Title

Use the same style as commit messages:

```
Add federation recovery protocol tests
```

### PR Description

Include:
- **What** — What does this PR change?
- **Why** — Why is this change needed?
- **Testing** — How did you test it?

**Template:**

```markdown
## Summary
Adds support for custom storage backends by extracting an abstract `StorageEngine` interface.

## Motivation
Allows users to plug in Postgres, DynamoDB, etc. without forking the codebase.

## Changes
- Extract `StorageEngine` abstract base class
- Refactor `SQLiteStorage` as concrete implementation
- Add `StorageBackend` configuration parameter

## Testing
- Added unit tests for `StorageEngine` interface
- Verified SQLite backend still passes all 604 tests
- Manually tested with mock Postgres backend
```

### Review Process

1. **Automated checks:** CI runs tests on Python 3.11, 3.12, 3.13
2. **Code review:** Maintainer reviews code, provides feedback
3. **Revisions:** Address feedback, push updates
4. **Approval:** Maintainer approves and merges

---

## Testing Guidelines

### Write Tests for All Changes

- **New features:** Add integration tests
- **Bug fixes:** Add regression tests
- **Refactors:** Ensure existing tests still pass

### Test Structure

```python
import pytest
from mfp import Runtime, RuntimeConfig, bind

def test_agent_binding():
    """Test that agents can be bound to a runtime."""
    # Arrange
    runtime = Runtime(RuntimeConfig())
    def agent(channel_id, message):
        return {"status": "ok"}

    # Act
    handle = bind(runtime, agent)

    # Assert
    assert handle.agent_id is not None
    assert len(handle.agent_id) == 32

    # Cleanup
    runtime.shutdown()
```

### Use Fixtures

```python
@pytest.fixture
def runtime():
    """Provide a fresh runtime for each test."""
    rt = Runtime(RuntimeConfig())
    yield rt
    rt.shutdown()

def test_with_fixture(runtime):
    # runtime is automatically created and cleaned up
    handle = bind(runtime, lambda ch, msg: {})
    assert handle is not None
```

### Async Tests

Use `pytest-asyncio`:

```python
import pytest

@pytest.mark.asyncio
async def test_async_transport():
    server = await start_server()
    result = await server.receive()
    assert result is not None
```

---

## Documentation Updates

### When to Update Docs

- **New public API:** Update `docs/api-reference.md`
- **New configuration option:** Update `docs/server-guide.md`
- **Behavioral change:** Update relevant guide
- **Breaking change:** Update `README.md` and `CHANGELOG.md`

### Style

- Use clear, concise language
- Include code examples
- Use `bash` and `python` code fences for syntax highlighting
- Link to related sections: `[API Reference](api-reference.md)`

---

## Code Review Checklist

Before requesting review, ensure:

- [ ] All tests pass (`pytest`)
- [ ] Coverage is maintained or improved
- [ ] Code follows style guidelines (PEP 8, type hints)
- [ ] Public API changes are documented
- [ ] Commit messages are descriptive
- [ ] PR description is complete

---

## Reporting Issues

Found a bug? Open an issue on GitHub:

1. **Search first** — Check if it's already reported
2. **Use issue templates** — Follow the provided format
3. **Include details:**
   - MFP version (`import mfp; print(mfp.__version__)`)
   - Python version (`python --version`)
   - Operating system
   - Minimal reproduction steps
   - Expected vs actual behavior

---

## Security Issues

**Do not** report security vulnerabilities in public issues.

Email: `security@mada.os` (or your configured contact)

See [Security Model](security.md) for responsible disclosure guidelines.

---

## Community Guidelines

- **Be respectful** — Assume good intent
- **Be constructive** — Offer solutions, not just criticism
- **Be patient** — Maintainers are volunteers

---

## Getting Help

- **Documentation:** Start with [README.md](../README.md) and [Quickstart](quickstart.md)
- **Issues:** Search existing issues or open a new one
- **Discussions:** Use GitHub Discussions for questions
- **Architecture questions:** See [Architecture](architecture.md) and [`design/`](../design/)

---

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0 (see [LICENSE](../LICENSE)).

---

**Thank you for contributing to MFP!** Your work helps build a more secure foundation for LLM agent communication.
