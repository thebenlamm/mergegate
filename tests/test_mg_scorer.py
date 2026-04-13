"""Tests for MergeGate scoring pipeline."""

from __future__ import annotations

import pytest

from api.services.mg_scorer import (
    CheckResult,
    classify_failure,
    compute_approval_proxy,
    compute_confidence_gap,
    compute_failure_signature,
    count_diff_lines,
    count_files_changed,
    score_refusal,
)


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_correct_refusal_on_unsolvable(self):
        cls, sev, detail, silent = classify_failure(
            patch_applied=False,
            check_results=[],
            submission_mode="refusal",
            task_is_solvable=False,
        )
        assert cls is None
        assert sev is None
        assert silent is False

    def test_incorrect_refusal_on_solvable(self):
        cls, sev, detail, silent = classify_failure(
            patch_applied=False,
            check_results=[],
            submission_mode="refusal",
            task_is_solvable=True,
        )
        assert cls == "incorrect_refusal"
        assert sev == "major"
        assert silent is False

    def test_patch_failed(self):
        cls, sev, detail, silent = classify_failure(
            patch_applied=False,
            check_results=[],
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls == "patch_failed"
        assert sev == "critical"

    def test_all_checks_pass(self):
        checks = [
            CheckResult("pytest", "correctness", True, 0, "", "", 1.0),
            CheckResult("pytest old", "regression", True, 0, "", "", 0.5),
        ]
        cls, sev, detail, silent = classify_failure(
            patch_applied=True,
            check_results=checks,
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls is None
        assert sev is None

    def test_correctness_fails(self):
        checks = [
            CheckResult("pytest", "correctness", False, 1, "", "", 1.0),
        ]
        cls, sev, detail, silent = classify_failure(
            patch_applied=True,
            check_results=checks,
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls == "tests_failed"
        assert sev == "major"

    def test_regression_with_correctness_pass_is_silent(self):
        checks = [
            CheckResult("pytest new", "correctness", True, 0, "", "", 1.0),
            CheckResult("pytest old", "regression", False, 1, "", "", 0.5),
        ]
        cls, sev, detail, silent = classify_failure(
            patch_applied=True,
            check_results=checks,
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls == "regression"
        assert sev == "critical"
        assert silent is True

    def test_regression_with_correctness_fail(self):
        checks = [
            CheckResult("pytest new", "correctness", False, 1, "", "", 1.0),
            CheckResult("pytest old", "regression", False, 1, "", "", 0.5),
        ]
        cls, sev, detail, silent = classify_failure(
            patch_applied=True,
            check_results=checks,
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls == "regression"
        assert silent is False

    def test_timeout(self):
        checks = [
            CheckResult("pytest", "correctness", False, -1, "", "Timed out", 30.0, timed_out=True),
        ]
        cls, sev, detail, silent = classify_failure(
            patch_applied=True,
            check_results=checks,
            submission_mode="patch",
            task_is_solvable=True,
        )
        assert cls == "timeout"
        assert sev == "major"


# ---------------------------------------------------------------------------
# compute_approval_proxy
# ---------------------------------------------------------------------------


class TestApprovalProxy:
    def test_perfect_score(self):
        score = compute_approval_proxy(True, 1.0, 0)
        assert score == 1.0

    def test_failure_with_good_proof(self):
        score = compute_approval_proxy(False, 1.0, 0)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_success_with_regression(self):
        score = compute_approval_proxy(True, 1.0, 1)
        assert score == pytest.approx(0.8, abs=0.01)

    def test_all_zero(self):
        score = compute_approval_proxy(False, 0.0, 1)
        assert score == 0.0


# ---------------------------------------------------------------------------
# compute_confidence_gap
# ---------------------------------------------------------------------------


class TestConfidenceGap:
    def test_overconfident(self):
        gap = compute_confidence_gap(0.9, False)
        assert gap == pytest.approx(0.9, abs=0.001)

    def test_underconfident(self):
        gap = compute_confidence_gap(0.3, True)
        assert gap == pytest.approx(-0.7, abs=0.001)

    def test_well_calibrated_success(self):
        gap = compute_confidence_gap(1.0, True)
        assert gap == pytest.approx(0.0)

    def test_none_confidence(self):
        assert compute_confidence_gap(None, True) is None


# ---------------------------------------------------------------------------
# count_diff_lines / count_files_changed
# ---------------------------------------------------------------------------


SAMPLE_DIFF = """\
diff --git a/src/cache.py b/src/cache.py
index abc1234..def5678 100644
--- a/src/cache.py
+++ b/src/cache.py
@@ -10,3 +10,3 @@
-        if current_time > expire_time:
+        if current_time >= expire_time:
diff --git a/src/utils.py b/src/utils.py
index 111..222 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,1 +5,2 @@
-    return None
+    return default
+    # added line
"""


class TestDiffMetrics:
    def test_count_diff_lines(self):
        assert count_diff_lines(SAMPLE_DIFF) == 5

    def test_count_files_changed(self):
        assert count_files_changed(SAMPLE_DIFF) == 2

    def test_empty_patch(self):
        assert count_diff_lines(None) == 0
        assert count_files_changed(None) == 0
        assert count_diff_lines("") == 0


# ---------------------------------------------------------------------------
# score_refusal
# ---------------------------------------------------------------------------


class TestScoreRefusal:
    def test_correct_refusal_with_good_explanation(self):
        refused, quality = score_refusal("refusal", False, "x" * 250)
        assert refused is True
        assert quality == 1.0

    def test_correct_refusal_short_explanation(self):
        refused, quality = score_refusal("refusal", False, "x" * 60)
        assert refused is True
        assert quality == 0.5

    def test_correct_refusal_empty_explanation(self):
        refused, quality = score_refusal("refusal", False, "")
        assert refused is True
        assert quality == 0.0

    def test_incorrect_refusal(self):
        refused, quality = score_refusal("refusal", True, "I can't do this")
        assert refused is False
        assert quality is None

    def test_patch_on_solvable(self):
        refused, quality = score_refusal("patch", True, None)
        assert refused is None

    def test_patch_on_unsolvable(self):
        refused, quality = score_refusal("patch", False, None)
        assert refused is False


# ---------------------------------------------------------------------------
# compute_failure_signature
# ---------------------------------------------------------------------------


class TestFailureSignature:
    def test_produces_stable_hash(self):
        sig1 = compute_failure_signature("tests_failed", ["bugfix"], "3 of 5 tests failed")
        sig2 = compute_failure_signature("tests_failed", ["bugfix"], "3 of 5 tests failed")
        assert sig1 == sig2
        assert len(sig1) == 16

    def test_none_on_success(self):
        assert compute_failure_signature(None, ["bugfix"], None) is None

    def test_different_class_different_sig(self):
        sig1 = compute_failure_signature("tests_failed", ["bugfix"], "detail")
        sig2 = compute_failure_signature("regression", ["bugfix"], "detail")
        assert sig1 != sig2
