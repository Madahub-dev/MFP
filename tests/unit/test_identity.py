"""Unit tests for mfp/agent/identity.py (I-11)."""

import pytest

from mfp.agent.identity import generate_agent_id
from mfp.core.primitives import random_state_value
from mfp.core.types import AgentId, StateValue


# ---------------------------------------------------------------------------
# generate_agent_id()
# ---------------------------------------------------------------------------

class TestGenerateAgentId:
    def test_returns_agent_id(self):
        identity = random_state_value()
        result = generate_agent_id(identity, 0)
        assert isinstance(result, AgentId)

    def test_value_is_32_bytes(self):
        """SHA-256 output is always 32 bytes."""
        identity = random_state_value()
        result = generate_agent_id(identity, 0)
        assert len(result.value) == 32

    def test_different_counters_produce_different_ids(self):
        identity = random_state_value()
        id_0 = generate_agent_id(identity, 0)
        id_1 = generate_agent_id(identity, 1)
        assert id_0.value != id_1.value

    def test_different_runtime_identities_produce_different_ids(self):
        id_a = generate_agent_id(StateValue(b"\x01" * 32), 0)
        id_b = generate_agent_id(StateValue(b"\x02" * 32), 0)
        assert id_a.value != id_b.value

    def test_same_inputs_differ_due_to_random_suffix(self):
        """Two calls with identical runtime_identity and counter still differ
        because an 8-byte random suffix is mixed in."""
        identity = StateValue(b"\xAA" * 32)
        id_a = generate_agent_id(identity, 42)
        id_b = generate_agent_id(identity, 42)
        assert id_a.value != id_b.value

    def test_counter_value_incorporated(self):
        """Counter 0 vs counter 1 with same identity must differ.
        (Even ignoring random, the hash inputs differ.)"""
        identity = random_state_value()
        results_0 = {generate_agent_id(identity, 0).value for _ in range(5)}
        results_1 = {generate_agent_id(identity, 1).value for _ in range(5)}
        # The two sets must be disjoint — no accidental collision
        assert results_0.isdisjoint(results_1)

    def test_large_counter_value(self):
        """Counter encoded as u64 big-endian should handle large values."""
        identity = random_state_value()
        result = generate_agent_id(identity, 2**63 - 1)
        assert isinstance(result, AgentId)
        assert len(result.value) == 32
