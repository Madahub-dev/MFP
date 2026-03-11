"""MFP Runtime — the central stateful engine.

Owns all protocol state: agent table, channel registry, global ratchet
state Sg. Orchestrates the message lifecycle by composing pure core
functions with the pipeline, channel, and quarantine modules.

Maps to: impl/I-06_runtime.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mfp.agent.identity import generate_agent_id
from mfp.core.merkle import IncrementalSg
from mfp.core.primitives import random_bytes, sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
    AgentState,
    AgentStatus,
    ChannelId,
    ChannelInfo,
    ChannelStatus,
    FrameValidationError,
    GlobalState,
    Receipt,
    StateValue,
    DEFAULT_FRAME_DEPTH,
)

from mfp.runtime.channels import (
    ChannelRegistry,
    advance_channel,
    close_channel as ch_close,
    establish_channel as ch_establish,
    get_channel,
    get_channels_for_agent,
)
from mfp.runtime.pipeline import (
    AgentCallable,
    RuntimeConfig,
    process_message,
)
from mfp.observability.logging import LogContext, get_logger, log_audit_event
from mfp.observability.metrics import get_metrics_collector
from mfp.runtime.quarantine import (
    check_rate_limit,
    check_validation_failure,
    increment_failure_count,
    quarantine_agent as q_quarantine_agent,
    quarantine_channel as q_quarantine_channel,
    reset_failure_count,
    restore_agent as q_restore_agent,
    restore_channel as q_restore_channel,
)


# ---------------------------------------------------------------------------
# Internal Types
# ---------------------------------------------------------------------------

@dataclass
class AgentRecord:
    """Internal agent record. Not exposed to agents.

    Maps to: I-06 §6.1.
    """
    agent_id: AgentId
    state: AgentState
    callable: AgentCallable
    channels: set[bytes] = field(default_factory=set)
    message_count: int = 0
    quarantine_reason: str = ""


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class Runtime:
    """MFP Runtime — the central stateful engine.

    Owns all protocol state. Orchestrates the message lifecycle.
    Composes pure core functions into a working engine.

    Maps to: runtime-interface.md §1, §9.
    """

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self._config = config or RuntimeConfig()
        self._identity = self._derive_identity()
        self._agents: dict[bytes, AgentRecord] = {}
        self._channels: ChannelRegistry = {}
        self._sg: GlobalState | None = None
        self._incremental_sg: IncrementalSg | None = None  # Merkle tree for O(log N) Sg
        self._agent_counter: int = 0
        self._logger = get_logger(__name__)

        # Log runtime initialization
        context = LogContext(
            correlation_id="init",
            runtime_id=self._identity.data.hex()[:8],
            operation="runtime_init",
        )
        self._logger.info("Runtime initialized", context=context)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def _derive_identity(self) -> StateValue:
        """Derive runtime identity from deployment and instance IDs.

        SHA-256(deployment_id || instance_id). Random values used if
        config fields are empty (sufficient for single-runtime operation).

        Maps to: I-06 §5.
        """
        deployment = self._config.deployment_id or random_bytes(32)
        instance = self._config.instance_id or random_bytes(32)
        return sha256(deployment + instance)

    def _generate_agent_id(self) -> AgentId:
        """Generate a unique agent ID via I-11 identity scheme.

        SHA-256(runtime_identity || counter || random_suffix).
        """
        self._agent_counter += 1
        return generate_agent_id(self._identity, self._agent_counter)

    # ------------------------------------------------------------------
    # Global State
    # ------------------------------------------------------------------

    def _recompute_sg(self) -> None:
        """Update Sg using incremental Merkle tree (O(1) - already updated).

        The Merkle tree is updated incrementally in establish_channel,
        close_channel, and after channel state advances. This method
        just syncs _sg with the tree root.

        Quarantined channels contribute (frozen state included).
        Closed channels do not (removed from registry).

        Maps to: spec.md §4.3, runtime-interface.md §8.3.
        """
        if not self._channels:
            self._sg = None
            self._incremental_sg = None
            return

        # Sg is already up-to-date in the Merkle tree
        if self._incremental_sg:
            self._sg = self._incremental_sg.get_root_hash()

    def _update_channel_in_tree(self, channel_id: ChannelId, new_state: StateValue) -> None:
        """Update a single channel state in the Merkle tree (O(log N))."""
        if self._incremental_sg:
            self._incremental_sg.update_channel(channel_id, new_state)

    # ------------------------------------------------------------------
    # Internal Lookups
    # ------------------------------------------------------------------

    def _lookup_agent(self, agent_id: AgentId) -> AgentRecord:
        """Look up an agent by ID. Raises AgentError if not found."""
        record = self._agents.get(agent_id.value)
        if record is None:
            raise AgentError(AgentErrorCode.UNBOUND, "Agent not bound")
        return record

    def _lookup_channel(self, channel_id: ChannelId) -> "Channel":
        """Look up a channel by ID. Raises AgentError if not found."""
        channel = get_channel(self._channels, channel_id)
        if channel is None:
            raise AgentError(AgentErrorCode.INVALID_CHANNEL, "Channel not found")
        return channel

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def identity(self) -> StateValue:
        """Runtime identity (immutable after init)."""
        return self._identity

    @property
    def global_state(self) -> GlobalState | None:
        """Current Sg. None if no channels exist."""
        return self._sg

    # ------------------------------------------------------------------
    # Layer 2: Administration
    # ------------------------------------------------------------------

    def bind_agent(self, agent_callable: AgentCallable) -> AgentId:
        """Bind a new agent to the runtime.

        Creates an AgentRecord in BOUND state. The agent becomes ACTIVE
        when its first channel is established.

        Maps to: I-06 §7.1.
        """
        # Check max_agents limit
        if len(self._agents) >= self._config.max_agents:
            raise AgentError(
                AgentErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Maximum agent limit reached ({self._config.max_agents})"
            )

        agent_id = self._generate_agent_id()
        record = AgentRecord(
            agent_id=agent_id,
            state=AgentState.BOUND,
            callable=agent_callable,
        )
        self._agents[agent_id.value] = record

        # Log agent binding
        context = LogContext(
            correlation_id=f"bind-{agent_id.value.hex()[:8]}",
            runtime_id=self._identity.data.hex()[:8],
            agent_id=agent_id.value.hex()[:8],
            operation="bind_agent",
        )
        log_audit_event("agent_bound", context, agent_count=len(self._agents))

        return agent_id

    def unbind_agent(self, agent_id: AgentId) -> None:
        """Unbind an agent from the runtime.

        Closes all channels, sets state to TERMINATED, removes from registry.

        Maps to: I-06 §7.2.
        """
        record = self._lookup_agent(agent_id)

        # Close all channels the agent participates in
        for ch_id_bytes in list(record.channels):
            self.close_channel(ChannelId(ch_id_bytes))

        record.state = AgentState.TERMINATED
        del self._agents[agent_id.value]

    def establish_channel(
        self,
        agent_a: AgentId,
        agent_b: AgentId,
        depth: int = DEFAULT_FRAME_DEPTH,
    ) -> ChannelId:
        """Establish a channel between two bound agents.

        Derives Sl0, registers channel, adds to agents' channel sets,
        transitions BOUND agents to ACTIVE, recomputes Sg.

        Maps to: I-06 §8.1.
        """
        rec_a = self._lookup_agent(agent_a)
        rec_b = self._lookup_agent(agent_b)

        if rec_a.state == AgentState.QUARANTINED:
            raise AgentError(AgentErrorCode.QUARANTINED, "Agent A is quarantined")
        if rec_b.state == AgentState.QUARANTINED:
            raise AgentError(AgentErrorCode.QUARANTINED, "Agent B is quarantined")

        # Check max_channels_per_agent limit
        if len(rec_a.channels) >= self._config.max_channels_per_agent:
            raise AgentError(
                AgentErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Agent A channel limit reached ({self._config.max_channels_per_agent})"
            )
        if len(rec_b.channels) >= self._config.max_channels_per_agent:
            raise AgentError(
                AgentErrorCode.RESOURCE_LIMIT_EXCEEDED,
                f"Agent B channel limit reached ({self._config.max_channels_per_agent})"
            )

        channel = ch_establish(
            registry=self._channels,
            runtime_identity=self._identity,
            agent_a=agent_a,
            agent_b=agent_b,
            depth=depth,
        )

        # Register channel with both agents
        rec_a.channels.add(channel.channel_id.value)
        rec_b.channels.add(channel.channel_id.value)

        # Transition BOUND → ACTIVE
        if rec_a.state == AgentState.BOUND:
            rec_a.state = AgentState.ACTIVE
        if rec_b.state == AgentState.BOUND:
            rec_b.state = AgentState.ACTIVE

        # Add channel to Merkle tree or rebuild if first channel
        if self._incremental_sg is None:
            # First channel - build initial tree
            channel_states = [
                (ch.channel_id, ch.state.local_state)
                for ch in self._channels.values()
            ]
            self._incremental_sg = IncrementalSg.from_channel_states(channel_states)
        else:
            # Add new channel to existing tree
            self._incremental_sg.add_channel(channel.channel_id, channel.state.local_state)

        self._recompute_sg()  # Sync _sg with tree root
        return channel.channel_id

    def close_channel(self, channel_id: ChannelId) -> None:
        """Close a channel and zero its state.

        Removes from agents' channel sets, zeros Sl, recomputes Sg.

        Maps to: I-06 §8.2.
        """
        channel = self._lookup_channel(channel_id)

        # Remove from agents' channel sets
        rec_a = self._agents.get(channel.agent_a.value)
        if rec_a:
            rec_a.channels.discard(channel_id.value)
        rec_b = self._agents.get(channel.agent_b.value)
        if rec_b:
            rec_b.channels.discard(channel_id.value)

        # Remove from Merkle tree before closing
        if self._incremental_sg:
            self._incremental_sg.remove_channel(channel_id)

        ch_close(self._channels, channel_id)
        self._recompute_sg()  # Sync _sg with tree root

        # Deactivate agents that have no remaining channels (ACTIVE → BOUND)
        for rec in (rec_a, rec_b):
            if rec and rec.state == AgentState.ACTIVE and not rec.channels:
                rec.state = AgentState.BOUND

    def quarantine_agent(self, agent_id: AgentId, reason: str = "") -> None:
        """Quarantine an agent and all its active channels.

        Maps to: runtime-interface.md §8.3.
        """
        record = self._lookup_agent(agent_id)
        q_quarantine_agent(record, self._channels, reason)

        # Record metrics
        metrics = get_metrics_collector()
        metrics.increment_quarantine_events(reason=reason or "unknown")

    def quarantine_channel(self, channel_id: ChannelId, reason: str = "") -> None:
        """Quarantine a single channel.

        Maps to: runtime-interface.md §8.3.
        """
        channel = self._lookup_channel(channel_id)
        q_quarantine_channel(channel, reason)

        # Record metrics
        metrics = get_metrics_collector()
        metrics.increment_quarantine_events(reason=reason or "unknown")

    def restore_agent(self, agent_id: AgentId) -> None:
        """Restore a quarantined agent and its channels.

        Maps to: runtime-interface.md §8.4.
        """
        record = self._lookup_agent(agent_id)
        q_restore_agent(record, self._channels)

    def restore_channel(self, channel_id: ChannelId) -> None:
        """Restore a quarantined channel.

        Maps to: runtime-interface.md §8.4.
        """
        channel = self._lookup_channel(channel_id)
        q_restore_channel(channel)

    # ------------------------------------------------------------------
    # Layer 1: Agent-Facing
    # ------------------------------------------------------------------

    def send(
        self,
        sender: AgentId,
        channel_id: ChannelId,
        payload: bytes,
    ) -> Receipt:
        """Process a message through the six-stage pipeline.

        On success: advance ratchet, recompute Sg, return receipt.
        On validation failure: increment failure count, auto-quarantine
        if threshold reached, re-raise.
        On any failure: no state change (atomic).

        Maps to: runtime-interface.md §4.
        """
        # Generate correlation ID for this send operation
        from mfp.core.primitives import random_bytes
        correlation_id = random_bytes(16).hex()[:16]

        context = LogContext(
            correlation_id=correlation_id,
            runtime_id=self._identity.data.hex()[:8],
            agent_id=sender.value.hex()[:8],
            channel_id=channel_id.value.hex()[:8],
            operation="send",
        )

        self._logger.debug("Message send initiated", context=context, payload_size=len(payload))

        sender_rec = self._lookup_agent(sender)

        # Verify sender state
        if sender_rec.state == AgentState.QUARANTINED:
            raise AgentError(AgentErrorCode.QUARANTINED, "Sender is quarantined")
        if sender_rec.state != AgentState.ACTIVE:
            raise AgentError(AgentErrorCode.UNBOUND, "Sender is not active")

        # Rate limit check
        if check_rate_limit(sender_rec.message_count, self._config.max_message_rate):
            self.quarantine_agent(sender, "Rate limit exceeded")
            raise AgentError(AgentErrorCode.QUARANTINED, "Rate limit exceeded")

        channel = self._lookup_channel(channel_id)

        # Determine destination agent
        if sender.value == channel.agent_a.value:
            dest_id = channel.agent_b
        elif sender.value == channel.agent_b.value:
            dest_id = channel.agent_a
        else:
            raise AgentError(AgentErrorCode.INVALID_CHANNEL, "Sender not on channel")

        dest_rec = self._lookup_agent(dest_id)

        # Global state must exist (channels exist)
        if self._sg is None:
            raise AgentError(AgentErrorCode.INVALID_CHANNEL, "No global state")

        try:
            result = process_message(
                sender=sender,
                channel=channel,
                payload=payload,
                global_state=self._sg,
                config=self._config,
                deliver=dest_rec.callable,
                correlation_id=correlation_id,
            )
        except AgentError as e:
            # Agent timeout: quarantine the destination agent
            if e.code == AgentErrorCode.TIMEOUT:
                self._logger.warning(
                    f"Agent timeout, quarantining destination agent: {str(e)}",
                    context=context,
                    dest_agent=dest_id.value.hex()[:8],
                )
                self.quarantine_agent(dest_id, f"Agent callable timeout: {str(e)}")
            raise
        except FrameValidationError as e:
            # Validation failure: track and potentially quarantine
            increment_failure_count(channel)

            # Record metrics
            metrics = get_metrics_collector()
            metrics.increment_validation_failures(error_type=type(e).__name__)

            if check_validation_failure(
                channel, self._config.validation_failure_threshold
            ):
                q_quarantine_channel(channel)
            raise

        # Success: advance state
        advance_channel(channel, result.new_local_state)
        reset_failure_count(channel)
        sender_rec.message_count += 1

        # Update Merkle tree incrementally (O(log N) instead of O(N))
        self._update_channel_in_tree(channel_id, result.new_local_state)
        self._recompute_sg()  # Sync _sg with tree root

        # Add correlation_id to receipt
        receipt = Receipt(
            message_id=result.receipt.message_id,
            channel=result.receipt.channel,
            step=result.receipt.step,
            correlation_id=correlation_id,
        )

        self._logger.info("Message sent successfully", context=context, step=result.receipt.step)

        # Record metrics
        metrics = get_metrics_collector()
        metrics.increment_messages_sent(
            agent_id=sender.value.hex()[:8],
            channel_id=channel_id.value.hex()[:8],
        )
        metrics.increment_messages_received(
            agent_id=dest_id.value.hex()[:8],
        )
        metrics.observe_message_size(len(payload))

        return receipt

    def get_channels(self, agent_id: AgentId) -> list[ChannelInfo]:
        """Return agent-visible channel information.

        Maps to: runtime-interface.md §3.2.
        """
        self._lookup_agent(agent_id)  # verify bound
        return get_channels_for_agent(self._channels, agent_id)

    def get_status(self, agent_id: AgentId) -> AgentStatus:
        """Return agent-visible status.

        Maps to: runtime-interface.md §3.3.
        """
        record = self._lookup_agent(agent_id)
        return AgentStatus(
            agent_id=agent_id,
            state=record.state,
            channel_count=len(record.channels),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Graceful shutdown. Close all channels, terminate agents, zero state.

        Maps to: I-06 §11.
        """
        # Close all channels (zeros Sl for each)
        for ch_id_bytes in list(self._channels.keys()):
            ch_close(self._channels, ChannelId(ch_id_bytes))

        # Terminate all agents
        for record in self._agents.values():
            record.state = AgentState.TERMINATED

        self._agents.clear()
        self._channels.clear()
        self._sg = None
        self._agent_counter = 0
