"""Tests for MergeGate Pydantic models."""

import pytest
from pydantic import ValidationError

from api.models.mergegate import (
    CreateSessionRequest,
    PredictionPayload,
    ProofBundlePayload,
    RecentSessionItem,
    ReflectionRequest,
    SessionCreatedResponse,
    SessionResultResponse,
    SessionSubmittedResponse,
    SubmitRequest,
    TaskSummary,
)


class TestPredictionPayload:
    def test_valid_prediction(self):
        p = PredictionPayload(
            confidence=0.85,
            reasoning="Standard cache bug",
            estimated_difficulty="medium",
            expected_approach="Fix TTL check",
            known_risks=["concurrency"],
        )
        assert p.confidence == 0.85
        assert p.known_risks == ["concurrency"]

    def test_confidence_bounds_high(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=1.5)

    def test_confidence_bounds_low(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=-0.1)

    def test_invalid_difficulty(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=0.5, estimated_difficulty="trivial")

    def test_minimal_prediction(self):
        p = PredictionPayload(confidence=0.5)
        assert p.reasoning is None
        assert p.known_risks is None

    def test_null_bytes_in_reasoning(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=0.5, reasoning="bad\x00string")

    def test_null_bytes_in_expected_approach(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=0.5, expected_approach="bad\x00string")

    def test_confidence_edge_zero(self):
        p = PredictionPayload(confidence=0.0)
        assert p.confidence == 0.0

    def test_confidence_edge_one(self):
        p = PredictionPayload(confidence=1.0)
        assert p.confidence == 1.0

    def test_all_difficulties(self):
        for diff in ("easy", "medium", "hard", "nightmare"):
            p = PredictionPayload(confidence=0.5, estimated_difficulty=diff)
            assert p.estimated_difficulty == diff


class TestCreateSessionRequest:
    def test_with_task_id(self):
        r = CreateSessionRequest(task_id="mg_task_0001")
        assert r.task_id == "mg_task_0001"
        assert r.use_next is False

    def test_use_next(self):
        r = CreateSessionRequest(use_next=True)
        assert r.use_next is True
        assert r.task_id is None

    def test_with_prediction(self):
        r = CreateSessionRequest(
            task_id="mg_task_0001",
            prediction=PredictionPayload(confidence=0.9),
        )
        assert r.prediction.confidence == 0.9

    def test_default_values(self):
        r = CreateSessionRequest()
        assert r.task_id is None
        assert r.use_next is False
        assert r.prediction is None

    def test_null_bytes_in_task_id(self):
        with pytest.raises(ValidationError):
            CreateSessionRequest(task_id="mg_task\x00001")


class TestProofBundlePayload:
    def test_valid_patch_bundle(self):
        b = ProofBundlePayload(
            schema_version="1.0",
            tests_run=[{"name": "test_x", "passed": True}],
            files_changed=[{"path": "a.py", "change_type": "modified", "summary": "fix"}],
            assumptions=["key is string"],
            correctness_argument="The bug was a strict inequality...",
            rollback_plan="revert abc123",
            residual_risks=["edge case"],
        )
        assert b.schema_version == "1.0"
        assert b.submission_mode == "patch"

    def test_refusal_bundle(self):
        b = ProofBundlePayload(
            schema_version="1.0",
            submission_mode="refusal",
            correctness_argument="Cannot be solved because...",
        )
        assert b.submission_mode == "refusal"

    def test_final_confidence_bounds(self):
        with pytest.raises(ValidationError):
            ProofBundlePayload(schema_version="1.0", final_confidence=1.5)

    def test_final_confidence_negative(self):
        with pytest.raises(ValidationError):
            ProofBundlePayload(schema_version="1.0", final_confidence=-0.1)

    def test_final_confidence_valid(self):
        b = ProofBundlePayload(schema_version="1.0", final_confidence=0.75)
        assert b.final_confidence == 0.75

    def test_null_bytes_in_correctness_argument(self):
        with pytest.raises(ValidationError):
            ProofBundlePayload(
                schema_version="1.0",
                correctness_argument="bad\x00string",
            )

    def test_null_bytes_in_rollback_plan(self):
        with pytest.raises(ValidationError):
            ProofBundlePayload(
                schema_version="1.0",
                rollback_plan="bad\x00plan",
            )

    def test_invalid_submission_mode(self):
        with pytest.raises(ValidationError):
            ProofBundlePayload(schema_version="1.0", submission_mode="invalid")


class TestSubmitRequest:
    def test_patch_submission(self):
        r = SubmitRequest(
            submission_mode="patch",
            patch_text="diff --git a/foo.py...",
            proof_bundle=ProofBundlePayload(schema_version="1.0"),
        )
        assert r.submission_mode == "patch"
        assert r.patch_format == "git_diff"

    def test_refusal_no_patch(self):
        r = SubmitRequest(
            submission_mode="refusal",
            proof_bundle=ProofBundlePayload(
                schema_version="1.0",
                submission_mode="refusal",
                correctness_argument="unsolvable",
            ),
        )
        assert r.patch_text is None

    def test_invalid_mode(self):
        with pytest.raises(ValidationError):
            SubmitRequest(
                submission_mode="invalid",
                proof_bundle=ProofBundlePayload(schema_version="1.0"),
            )

    def test_clarification_request_mode(self):
        r = SubmitRequest(
            submission_mode="clarification_request",
            proof_bundle=ProofBundlePayload(schema_version="1.0"),
        )
        assert r.submission_mode == "clarification_request"

    def test_null_bytes_in_patch_text(self):
        with pytest.raises(ValidationError):
            SubmitRequest(
                submission_mode="patch",
                patch_text="diff\x00bad",
                proof_bundle=ProofBundlePayload(schema_version="1.0"),
            )

    def test_null_bytes_in_submission_notes(self):
        with pytest.raises(ValidationError):
            SubmitRequest(
                submission_mode="patch",
                submission_notes="bad\x00note",
                proof_bundle=ProofBundlePayload(schema_version="1.0"),
            )


class TestReflectionRequest:
    def test_valid_reflection(self):
        r = ReflectionRequest(
            was_surprised=True,
            failure_explanation="Missed the unicode edge case",
            root_cause_guess="edge_case_miss",
            would_change="Add unicode normalization",
            updated_confidence=0.6,
        )
        assert r.was_surprised is True

    def test_minimal_reflection(self):
        r = ReflectionRequest()
        assert r.was_surprised is None

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            ReflectionRequest(updated_confidence=2.0)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            ReflectionRequest(updated_confidence=-0.5)

    def test_null_bytes_in_failure_explanation(self):
        with pytest.raises(ValidationError):
            ReflectionRequest(failure_explanation="bad\x00string")

    def test_null_bytes_in_would_change(self):
        with pytest.raises(ValidationError):
            ReflectionRequest(would_change="bad\x00string")


class TestTaskSummary:
    def test_valid_task(self):
        t = TaskSummary(
            id="mg_task_0001",
            title="Fix Cache Bug",
            difficulty="medium",
            category=["bugfix"],
            is_solvable=True,
            max_duration_s=600,
        )
        assert t.id == "mg_task_0001"

    def test_invalid_difficulty(self):
        with pytest.raises(ValidationError):
            TaskSummary(
                id="mg_task_0001",
                title="Test",
                difficulty="trivial",
                category=["bugfix"],
                is_solvable=True,
                max_duration_s=300,
            )


class TestSessionCreatedResponse:
    def test_valid_response(self):
        r = SessionCreatedResponse(
            session_id="abc-123",
            variant_id="mg_task_0001_v001",
            spec_text="Fix the cache bug...",
            repo_download_url="https://example.com/repo.tar.gz",
            deadline="2026-04-10T01:00:00Z",
            submission_contract={"required_fields": ["proof_bundle"]},
        )
        assert r.session_id == "abc-123"
        assert r.submission_contract == {"required_fields": ["proof_bundle"]}


class TestSessionSubmittedResponse:
    def test_default_eta(self):
        r = SessionSubmittedResponse(session_id="abc", status="scoring")
        assert r.scoring_eta_s == 30

    def test_custom_eta(self):
        r = SessionSubmittedResponse(session_id="abc", status="scoring", scoring_eta_s=60)
        assert r.scoring_eta_s == 60


class TestSessionResultResponse:
    def test_pending_result(self):
        r = SessionResultResponse(session_id="abc", status="pending")
        assert r.task_success is None
        assert r.failure_signature is None

    def test_complete_result(self):
        r = SessionResultResponse(
            session_id="abc",
            status="completed",
            task_success=True,
            mergeable=True,
            hidden_tests_passed=10,
            hidden_tests_total=10,
            proof_completeness=0.95,
            review_cost_proxy=3.2,
            quality_floor_passed=True,
            safety_floor_passed=True,
        )
        assert r.mergeable is True

    def test_failed_result_with_failure_details(self):
        r = SessionResultResponse(
            session_id="abc",
            status="completed",
            task_success=False,
            mergeable=False,
            failure_class="wrong_answer",
            failure_severity="critical",
            failure_detail="Off by one in loop bound",
            failure_signature="obo_loop",
            is_silent_failure=False,
        )
        assert r.failure_class == "wrong_answer"
        assert r.is_silent_failure is False


class TestRecentSessionItem:
    def test_valid_item(self):
        i = RecentSessionItem(
            session_id="abc",
            variant_id="mg_task_0001_v001",
            status="completed",
            task_success=True,
            mergeable=True,
            proof_completeness=0.88,
            created_at="2026-04-10T00:00:00Z",
        )
        assert i.status == "completed"

    def test_minimal_item(self):
        i = RecentSessionItem(
            session_id="abc",
            variant_id="mg_task_0001_v001",
            status="pending",
            created_at="2026-04-10T00:00:00Z",
        )
        assert i.task_success is None
        assert i.mergeable is None
        assert i.proof_completeness is None
