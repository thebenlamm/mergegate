"""Tests for proof bundle completeness and review cost scoring."""

from api.services.proof_scoring import score_proof_completeness, estimate_review_cost


class TestProofCompleteness:
    def test_perfect_bundle(self):
        bundle = {
            "tests_run": [{"name": "test_x", "passed": True}],
            "files_changed": [{"path": "a.py"}],
            "assumptions": ["key is string"],
            "correctness_argument": "The bug was a strict inequality that should have been a non-strict check on the TTL boundary",
            "rollback_plan": "revert commit abc123",
            "residual_risks": ["concurrent access"],
            "not_verified": ["load testing"],
        }
        score = score_proof_completeness(bundle)
        assert score == 1.0

    def test_empty_bundle(self):
        score = score_proof_completeness({})
        assert score == 0.0

    def test_partial_bundle_tests_and_argument(self):
        bundle = {
            "tests_run": [{"name": "test_x", "passed": True}],
            "correctness_argument": "The bug was a strict inequality that should have been a non-strict check on the TTL boundary",
        }
        score = score_proof_completeness(bundle)
        # tests_run (0.20) + correctness_argument (0.20) = 0.40
        assert abs(score - 0.40) < 0.01

    def test_short_correctness_argument_not_counted(self):
        """correctness_argument must be >= 50 chars to count."""
        bundle = {"correctness_argument": "works"}
        score = score_proof_completeness(bundle)
        assert score == 0.0

    def test_correctness_argument_exactly_50_chars(self):
        bundle = {"correctness_argument": "x" * 50}
        score = score_proof_completeness(bundle)
        assert abs(score - 0.20) < 0.01

    def test_empty_lists_not_counted(self):
        bundle = {
            "tests_run": [],
            "assumptions": [],
            "residual_risks": [],
        }
        score = score_proof_completeness(bundle)
        assert score == 0.0

    def test_none_values_not_counted(self):
        bundle = {
            "tests_run": None,
            "correctness_argument": None,
        }
        score = score_proof_completeness(bundle)
        assert score == 0.0

    def test_whitespace_only_strings_not_counted(self):
        bundle = {
            "rollback_plan": "   ",
            "correctness_argument": "   ",
        }
        score = score_proof_completeness(bundle)
        assert score == 0.0

    def test_single_field_weights(self):
        """Each field alone should produce exactly its weight."""
        field_weights = {
            "tests_run": ([{"name": "t"}], 0.20),
            "files_changed": ([{"path": "f.py"}], 0.15),
            "assumptions": (["assumption"], 0.15),
            "rollback_plan": ("revert abc", 0.10),
            "residual_risks": (["risk"], 0.10),
            "not_verified": (["item"], 0.10),
        }
        for field, (value, expected_weight) in field_weights.items():
            bundle = {field: value}
            score = score_proof_completeness(bundle)
            assert abs(score - expected_weight) < 0.01, (
                f"{field}: expected {expected_weight}, got {score}"
            )


class TestReviewCostEstimate:
    def test_minimal_cost_perfect_bundle(self):
        """Perfect bundle, small diff = base cost."""
        cost = estimate_review_cost(
            proof_completeness=1.0,
            diff_lines=20,
            files_changed=1,
        )
        assert abs(cost - 3.0) < 0.01

    def test_zero_completeness_increases_cost(self):
        cost = estimate_review_cost(
            proof_completeness=0.0,
            diff_lines=20,
            files_changed=1,
        )
        # base * (1 + 0.3) = 3.9
        assert abs(cost - 3.9) < 0.01

    def test_large_diff_increases_cost(self):
        cost = estimate_review_cost(
            proof_completeness=1.0,
            diff_lines=150,
            files_changed=1,
        )
        # base * 1 * (1 + 0.01 * 100) = 3 * 2 = 6.0
        assert abs(cost - 6.0) < 0.01

    def test_many_files_increases_cost(self):
        cost = estimate_review_cost(
            proof_completeness=1.0,
            diff_lines=20,
            files_changed=8,
        )
        # base * 1 * 1 * (1 + 0.1 * 5) = 3 * 1.5 = 4.5
        assert abs(cost - 4.5) < 0.01

    def test_worst_case_combined(self):
        cost = estimate_review_cost(
            proof_completeness=0.0,
            diff_lines=250,
            files_changed=13,
        )
        # base * 1.3 * (1 + 0.01*200) * (1 + 0.1*10) = 3 * 1.3 * 3.0 * 2.0 = 23.4
        assert abs(cost - 23.4) < 0.01

    def test_small_diff_no_penalty(self):
        """Diffs under 50 lines get no diff penalty."""
        cost = estimate_review_cost(
            proof_completeness=1.0,
            diff_lines=50,
            files_changed=1,
        )
        assert abs(cost - 3.0) < 0.01

    def test_few_files_no_penalty(self):
        """3 or fewer files get no file penalty."""
        cost = estimate_review_cost(
            proof_completeness=1.0,
            diff_lines=20,
            files_changed=3,
        )
        assert abs(cost - 3.0) < 0.01
