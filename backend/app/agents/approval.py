"""Phase 8 — Human Approval Workflow (Validation Agent).

Single-approver: any active user can approve / reject / override a
classification. Override allows the reviewer to change the masking
strategy (e.g. "this Email column should be Hashed, not Fake Email")
or exclude the column from any downstream update.

The agent never advances the workflow on its own — that's Phase 13.
It just persists the human decision and exposes the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    Approval,
    ClassificationResult,
    User,
)


ALLOWED_DECISIONS = frozenset({"approve", "reject", "override"})
ALLOWED_STRATEGIES = frozenset(
    {
        "Fake Name",
        "Fake Email",
        "Masked Phone",
        "Hash",
        "Tokenization",
        "Random Replacement",
        "Preserve Format",
        "Null",
        "Custom Rule",
        "LLM Anonymize",
    }
)


@dataclass(frozen=True)
class ApprovalDecision:
    classification_id: str
    decision: str
    approved_by_user_id: str
    masking_strategy: str | None
    comment: str | None
    excluded: bool
    approved_at: datetime
    id: str


class ApprovalAgent:
    """Record and query human approval decisions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def submit(
        self,
        classification_id: str,
        approver_user_id: str,
        decision: str,
        masking_strategy: str | None = None,
        comment: str | None = None,
        excluded: bool = False,
    ) -> ApprovalDecision:
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(ALLOWED_DECISIONS)}; got {decision!r}"
            )
        if decision in ("approve", "override") and excluded:
            # Excluding a column is a stronger "no" than approving it.
            raise ValueError(
                "Cannot both approve and exclude a column; use decision='reject' with excluded=True."
            )
        if decision == "override":
            if not masking_strategy:
                raise ValueError("decision='override' requires a non-empty masking_strategy.")
            if masking_strategy not in ALLOWED_STRATEGIES:
                raise ValueError(
                    f"masking_strategy must be one of {sorted(ALLOWED_STRATEGIES)}; got {masking_strategy!r}"
                )
        if not self._user_exists(approver_user_id):
            raise ValueError(f"User {approver_user_id!r} not found.")
        if not self._classification_exists(classification_id):
            raise ValueError(f"ClassificationResult {classification_id!r} not found.")
        if not self._user_active(approver_user_id):
            raise ValueError(f"User {approver_user_id!r} is not active.")

        # Upsert: one approval row per classification.
        existing = self._session.execute(
            select(Approval).where(Approval.classification_result_id == classification_id)
        ).scalar_one_or_none()
        if existing is not None:
            row = existing
        else:
            row = Approval(
                classification_result_id=classification_id,
                approved_by_user_id=approver_user_id,
                decision=decision,
                masking_strategy=masking_strategy,
                comment=comment,
                excluded=excluded,
            )
            self._session.add(row)
        row.approved_by_user_id = approver_user_id
        row.decision = decision
        row.masking_strategy = masking_strategy
        row.comment = comment
        row.excluded = excluded
        self._session.commit()
        self._session.refresh(row)
        # Look up the classification to find the job id (decisions are
        # per-column, but the audit log is per-job).
        from app.agents.audit import AuditAgent
        from app.models.entities import ClassificationResult

        cls = self._session.get(ClassificationResult, classification_id)
        if cls is not None:
            AuditAgent(self._session).write(
                event_type=f"approval.{decision}",
                outcome="ok" if not excluded else "excluded",
                job_id=cls.job_id,
                user_id=approver_user_id,
                details={
                    "classification_id": classification_id,
                    "table": cls.table_name,
                    "column": cls.column_name,
                    "masking_strategy": masking_strategy,
                    "comment": comment,
                },
            )
        return ApprovalDecision(
            classification_id=classification_id,
            decision=row.decision,
            approved_by_user_id=row.approved_by_user_id,
            masking_strategy=row.masking_strategy,
            comment=row.comment,
            excluded=row.excluded,
            approved_at=row.updated_at,
            id=row.id,
        )

    def pending_for_job(self, job_id: str) -> list[ClassificationResult]:
        """Return the classification rows in a job with no approval yet.

        Excludes classifications whose underlying LLM call failed
        (``ok=False``): the operator should re-classify, not approve
        a failed call.
        """
        rows = list(
            self._session.execute(
                select(ClassificationResult)
                .where(ClassificationResult.job_id == job_id)
                .order_by(
                    ClassificationResult.schema_name,
                    ClassificationResult.table_name,
                    ClassificationResult.column_name,
                )
            ).scalars()
        )
        approved_ids = {
            row.classification_result_id
            for row in self._session.execute(select(Approval)).scalars()
        }
        return [r for r in rows if r.id not in approved_ids and r.ok]

    def approvals_for_job(self, job_id: str) -> list[Approval]:
        rows = list(
            self._session.execute(
                select(Approval)
                .join(
                    ClassificationResult,
                    Approval.classification_result_id == ClassificationResult.id,
                )
                .where(ClassificationResult.job_id == job_id)
            ).scalars()
        )
        return rows

    def _user_exists(self, user_id: str) -> bool:
        return self._session.get(User, user_id) is not None

    def _user_active(self, user_id: str) -> bool:
        u = self._session.get(User, user_id)
        return u is not None and u.is_active

    def _classification_exists(self, classification_id: str) -> bool:
        return self._session.get(ClassificationResult, classification_id) is not None


__all__ = [
    "ALLOWED_DECISIONS",
    "ALLOWED_STRATEGIES",
    "ApprovalAgent",
    "ApprovalDecision",
]
