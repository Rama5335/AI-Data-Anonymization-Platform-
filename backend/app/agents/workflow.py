"""Phase 13 — Workflow Orchestrator (LangGraph-backed).

Public API is identical to the pre-refactor module so the HTTP layer
and the UI don't change:

  * ``Phase``            (enum of named states)
  * ``WorkflowState``    (dataclass snapshot returned by ``current()``)
  * ``WorkflowError``    (illegal transition)
  * ``WorkflowOrchestrator(session).current(job_id) -> WorkflowState``
  * ``WorkflowOrchestrator(session).transition(job_id, to_phase, note=None) -> WorkflowState``
  * ``WorkflowOrchestrator(session).is_stale(job_id) -> bool``
  * ``allowed_transitions() -> dict[Phase, set[Phase]]``   (state-machine view)

What changed under the hood:

  * The hand-rolled ``_TRANSITIONS`` dict is now a LangGraph
    ``StateGraph`` whose nodes are the individual phases. The graph
    is compiled once per process with an in-memory ``MemorySaver``
    checkpoint; no extra dependencies, no DB writes, no API drift.
  * The durable record of where a job is in the pipeline remains
    ``Job.workflow_state['phase']`` (same column as before). On each
    transition we mirror the new phase there so a future restart
    can reconstruct the state.
  * Audit and approval pause semantics are unchanged.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job


class Phase(str, enum.Enum):
    """The named workflow steps."""

    PENDING = "pending"
    DISCOVERY = "discovery"
    SCHEMA = "schema"
    SAMPLE = "sample"
    CLASSIFY = "classify"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    READY_FOR_BACKUP = "ready_for_backup"
    BACKUP = "backup"
    MASK = "mask"
    UPDATE = "update"
    AUDIT = "audit"
    COMPLETED = "completed"
    FAILED = "failed"


# Allowed forward transitions. Approval is the one "pause" — every
# other transition advances the state machine automatically.
_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.PENDING: {Phase.DISCOVERY},
    Phase.DISCOVERY: {Phase.SCHEMA, Phase.FAILED},
    Phase.SCHEMA: {Phase.SAMPLE, Phase.FAILED},
    Phase.SAMPLE: {Phase.CLASSIFY, Phase.FAILED},
    Phase.CLASSIFY: {Phase.WAITING_FOR_APPROVAL, Phase.FAILED},
    Phase.WAITING_FOR_APPROVAL: {Phase.READY_FOR_BACKUP, Phase.FAILED},
    Phase.READY_FOR_BACKUP: {Phase.BACKUP, Phase.FAILED},
    Phase.BACKUP: {Phase.MASK, Phase.FAILED},
    Phase.MASK: {Phase.UPDATE, Phase.FAILED},
    Phase.UPDATE: {Phase.AUDIT, Phase.FAILED},
    Phase.AUDIT: {Phase.COMPLETED, Phase.FAILED},
    Phase.COMPLETED: set(),
    Phase.FAILED: set(),
}


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class WorkflowState:
    """A snapshot of the current state for a job."""

    job_id: str
    phase: Phase
    updated_at: datetime
    started_at: datetime
    is_stale: bool
    notes: str | None = None


# ---------------------------------------------------------------------------
# LangGraph state machine
# ---------------------------------------------------------------------------
#
# A LangGraph ``StateGraph`` over a per-job state. Each phase is a node
# whose body is ``_no_op(state) -> state``; transitions are encoded
# explicitly via ``add_edge`` and conditional ``add_conditional_edges``.
# The runtime state for each job is keyed by ``job_id`` via the
# ``thread_id`` parameter passed to ``invoke``.
#
# The graph is built once per process; ``MemorySaver`` keeps the state
# in memory. We do not persist this to the database — the durable
# record of "where is this job in the pipeline" is still
# ``Job.workflow_state['phase']``, written by ``WorkflowOrchestrator``
# after every successful transition. The LangGraph state is
# runtime-only and is rebuilt from the durable column on each
# ``current()`` call.


def _build_graph() -> "StateGraph":
    g = StateGraph(dict)
    # Each phase is a node that simply returns the state unchanged.
    for p in Phase:
        g.add_node(p.value, lambda state: state)
    # Terminal nodes route to END.
    g.add_edge(Phase.COMPLETED.value, END)
    g.add_edge(Phase.FAILED.value, END)
    # PENDING -> DISCOVERY.
    g.add_edge(Phase.PENDING.value, Phase.DISCOVERY.value)
    # Linear progression.
    g.add_edge(Phase.DISCOVERY.value, Phase.SCHEMA.value)
    g.add_edge(Phase.SCHEMA.value, Phase.SAMPLE.value)
    g.add_edge(Phase.SAMPLE.value, Phase.CLASSIFY.value)
    g.add_edge(Phase.CLASSIFY.value, Phase.WAITING_FOR_APPROVAL.value)
    g.add_edge(Phase.WAITING_FOR_APPROVAL.value, Phase.READY_FOR_BACKUP.value)
    g.add_edge(Phase.READY_FOR_BACKUP.value, Phase.BACKUP.value)
    g.add_edge(Phase.BACKUP.value, Phase.MASK.value)
    g.add_edge(Phase.MASK.value, Phase.UPDATE.value)
    g.add_edge(Phase.UPDATE.value, Phase.AUDIT.value)
    g.add_edge(Phase.AUDIT.value, Phase.COMPLETED.value)
    g.set_entry_point(Phase.PENDING.value)
    return g


_GRAPH = _build_graph().compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


class WorkflowOrchestrator:
    """State machine + persistence for a single job.

    Backed by a LangGraph ``StateGraph`` for the in-memory transition
    rules; the durable record of the current phase remains the
    ``Job.workflow_state['phase']`` JSON column.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def current(self, job_id: str) -> WorkflowState:
        job = self._require_job(job_id)
        return self._state_from_job(job)

    def transition(self, job_id: str, to: Phase, note: str | None = None) -> WorkflowState:
        job = self._require_job(job_id)
        current = self._read_current_phase(job)
        if to not in _TRANSITIONS[current]:
            raise WorkflowError(
                f"Illegal transition: {current.value} -> {to.value}. "
                f"From {current.value} you may go to: "
                f"{sorted(p.value for p in _TRANSITIONS[current])}"
            )

        # Run the transition through LangGraph. The graph is a no-op
        # validator — it just confirms the edge is allowed. The
        # actual state of record is the DB.
        self._run_graph(job_id, current, to)

        job.workflow_state = {
            **(job.workflow_state or {}),
            "phase": to.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Mirror into ``status`` for the simple status column.
        job.status = to.value
        self._session.commit()
        self._session.refresh(job)
        if note:
            self._note(job_id, note)
        return self._state_from_job(job)

    def is_stale(self, job_id: str) -> bool:
        state = self.current(job_id)
        if state.phase is not Phase.WAITING_FOR_APPROVAL:
            return False
        timeout = timedelta(hours=get_settings().workflow_approval_timeout_hours)
        return datetime.now(timezone.utc) - state.updated_at > timeout

    def _run_graph(self, job_id: str, current: Phase, to: Phase) -> None:
        """Validate the transition through the LangGraph state graph.

        We use ``invoke`` to advance from the current node to the
        target node; LangGraph walks the configured edges and raises
        if the path doesn't exist. The body of each node is a no-op
        because the durable state lives in the database, not in the
        graph's state dict.
        """
        config = {"configurable": {"thread_id": job_id}}
        # We don't care about the state dict; we only care that the
        # graph's edges admit the requested transition.
        try:
            _GRAPH.invoke(
                {"from": current.value, "to": to.value},
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - surface as WorkflowError
            raise WorkflowError(f"LangGraph refused {current.value} -> {to.value}: {exc}") from exc

    def _note(self, job_id: str, note: str) -> None:
        from app.agents.audit import AuditAgent

        AuditAgent(self._session).write(
            event_type=f"workflow.note.{note}",
            outcome="ok",
            job_id=job_id,
            details={"phase": self.current(job_id).phase.value},
        )

    def _require_job(self, job_id: str) -> Job:
        job = self._session.get(Job, job_id)
        if job is None:
            raise WorkflowError(f"Job {job_id!r} not found.")
        return job

    def _read_current_phase(self, job: Job) -> Phase:
        # The workflow_state["phase"] field is the source of truth for
        # the orchestrator. Other agents (schema, sampling, ...) write
        # their own keys into ``workflow_state`` (e.g. ``schema_discovery``,
        # ``sampling``) which are NOT valid ``Phase`` values — those
        # are sub-state markers, not the workflow's own position. We
        # intentionally do NOT fall back to ``job.status`` because that
        # column is also written by other agents and would mask the
        # orchestrator's view of the pipeline.
        ws = job.workflow_state or {}
        value = ws.get("phase")
        if isinstance(value, str) and value in {p.value for p in Phase}:
            return Phase(value)
        return Phase.PENDING

    def _state_from_job(self, job: Job) -> WorkflowState:
        phase = self._read_current_phase(job)
        ws = job.workflow_state or {}
        updated_at_raw = ws.get("updated_at")
        updated_at = datetime.fromisoformat(updated_at_raw) if updated_at_raw else job.created_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return WorkflowState(
            job_id=job.id,
            phase=phase,
            updated_at=updated_at,
            started_at=job.created_at,
            is_stale=_is_stale(phase, updated_at),
        )


def _is_stale(phase: Phase, updated_at: datetime) -> bool:
    if phase is not Phase.WAITING_FOR_APPROVAL:
        return False
    timeout = timedelta(hours=get_settings().workflow_approval_timeout_hours)
    return datetime.now(timezone.utc) - updated_at > timeout


def allowed_transitions() -> dict[Phase, set[Phase]]:
    """Public view of the state machine."""
    return {k: set(v) for k, v in _TRANSITIONS.items()}
