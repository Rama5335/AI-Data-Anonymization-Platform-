"""Tests for the AnonymizationAgent (LLM-driven anonymization).

Verifies:
- Batch anonymization via LLM
- Deterministic caching within a job
- Fallback when LLM fails
- None/null propagation
- Batch size limits
"""

from __future__ import annotations

from app.agents.anonymization import (
    BATCH_SIZE,
    AnonymizationAgent,
    AnonymizationContext,
)
from app.classification.llm_client import LLMClient, LLMResponseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubLLM(LLMClient):
    """Returns canned responses for anonymize_batch calls."""

    def __init__(self, responses: list[list[str]] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0

    def complete_json(self, system: str, user: str) -> dict:
        raise NotImplementedError("Should not be called via anonymize_batch")

    def anonymize_batch(self, system: str, user: str) -> list[str]:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        raise LLMResponseError("No more responses")


class _FailingLLM(LLMClient):
    def complete_json(self, system: str, user: str) -> dict:
        raise RuntimeError("LLM is down")

    def anonymize_batch(self, system: str, user: str) -> list[str]:
        raise RuntimeError("LLM is down")


class _PadLLM(LLMClient):
    """Returns wrong-length result to test padding/truncation."""

    def __init__(self, resp: list[str]) -> None:
        self._resp = resp

    def complete_json(self, system: str, user: str) -> dict:
        raise NotImplementedError

    def anonymize_batch(self, system: str, user: str) -> list[str]:
        return self._resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnonymizationAgent:
    def test_basic_anonymization(self):
        llm = _StubLLM(responses=[["Alice Smith", "Bob Jones"]])
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="Customers",
            column="Name",
            data_type="nvarchar",
            sensitivity="Name",
            strategy="Fake Name",
            values=["John Doe", "Jane Smith"],
        )
        assert result == ["Alice Smith", "Bob Jones"]

    def test_none_propagation(self):
        llm = _StubLLM(responses=[["fake@email.com"]])
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="Customers",
            column="Email",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=[None, "real@email.com", None],
        )
        assert result[0] is None
        assert result[1] == "fake@email.com"
        assert result[2] is None

    def test_empty_values(self):
        llm = _StubLLM()
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=[],
        )
        assert result == []
        # LLM should NOT have been called.
        assert llm._call_count == 0

    def test_deterministic_caching(self):
        llm = _StubLLM(responses=[["anon@email.com"]])
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        # First call
        result1 = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=["original@email.com"],
        )
        # Second call with same value — should use cache, not call LLM again.
        result2 = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=["original@email.com"],
        )
        assert result1 == result2
        assert llm._call_count == 1  # Only one LLM call total.

    def test_different_columns_no_cache_collision(self):
        llm = _StubLLM(responses=[["anon_a"], ["anon_b"]])
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        val = "same_value"
        r1 = agent.anonymize_column(
            ctx,
            table="T",
            column="ColA",
            data_type="nvarchar",
            sensitivity="Name",
            strategy="Fake Name",
            values=[val],
        )
        r2 = agent.anonymize_column(
            ctx,
            table="T",
            column="ColB",
            data_type="nvarchar",
            sensitivity="Name",
            strategy="Fake Name",
            values=[val],
        )
        # Different columns get different anonymizations.
        assert r1[0] != r2[0]
        assert llm._call_count == 2

    def test_llm_failure_fallback(self):
        llm = _FailingLLM()
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=["fallback@test.com"],
        )
        # Falls back to str(original).
        assert result == ["fallback@test.com"]

    def test_wrong_length_result_padded(self):
        llm = _PadLLM(resp=["only_one"])
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=["a@b.com", "c@d.com", "e@f.com"],
        )
        assert len(result) == 3
        assert result[0] == "only_one"
        assert result[1] == ""
        assert result[2] == ""

    def test_batch_splitting(self):
        # Create more values than BATCH_SIZE to test splitting.
        values = [f"val{i}@test.com" for i in range(BATCH_SIZE + 10)]
        # Return enough responses for 2 batches.
        responses = [
            [f"anon{i}@fake.com" for i in range(BATCH_SIZE)],
            [f"anon{i}@fake.com" for i in range(10)],
        ]
        llm = _StubLLM(responses=responses)
        agent = AnonymizationAgent(llm)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm)
        result = agent.anonymize_column(
            ctx,
            table="T",
            column="C",
            data_type="nvarchar",
            sensitivity="Email",
            strategy="Fake Email",
            values=values,
        )
        assert len(result) == len(values)
        # Two LLM calls (one per batch).
        assert llm._call_count == 2
