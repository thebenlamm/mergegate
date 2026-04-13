"""Proof bundle completeness and review cost scoring.

v1 completeness is a **structural proxy**, not a semantic quality score.
It measures whether expected fields are present and non-trivial (non-empty
lists, non-whitespace strings, minimum-length arguments). It does NOT
measure whether the content is accurate, useful, or truthful.
"""

from __future__ import annotations

# Field weights for completeness scoring.
# Sum to 1.0 across the seven proof bundle fields.
_FIELD_WEIGHTS: dict[str, float] = {
    "tests_run": 0.20,
    "files_changed": 0.15,
    "assumptions": 0.15,
    "correctness_argument": 0.20,
    "rollback_plan": 0.10,
    "residual_risks": 0.10,
    "not_verified": 0.10,
}

_CORRECTNESS_ARG_MIN_CHARS = 50

# Review cost constants.
_BASE_COST_MINUTES = 3.0
_COMPLETENESS_PENALTY_FACTOR = 0.3
_DIFF_LINE_THRESHOLD = 50
_DIFF_LINE_COST_PER = 0.01
_FILE_THRESHOLD = 3
_FILE_COST_PER = 0.1


def _field_present(value: object) -> bool:
    """Return True if a field value is non-trivially present."""
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        return len(value.strip()) > 0
    return True


def score_proof_completeness(bundle: dict) -> float:
    """Score a proof bundle for structural completeness.

    This is a weighted structural proxy (0.0 to 1.0). It checks whether
    expected fields are present and non-trivial. It does NOT evaluate
    semantic quality or correctness of the content.

    Weights:
        tests_run:              0.20
        files_changed:          0.15
        assumptions:            0.15
        correctness_argument:   0.20  (must be >= 50 chars)
        rollback_plan:          0.10
        residual_risks:         0.10
        not_verified:           0.10

    Returns:
        Float between 0.0 (empty bundle) and 1.0 (all fields present).
    """
    score = 0.0
    for field, weight in _FIELD_WEIGHTS.items():
        value = bundle.get(field)
        if not _field_present(value):
            continue
        # correctness_argument has a minimum length requirement.
        if field == "correctness_argument":
            assert isinstance(value, str)  # guaranteed by _field_present
            if len(value.strip()) < _CORRECTNESS_ARG_MIN_CHARS:
                continue
        score += weight
    return round(score, 10)


def estimate_review_cost(
    proof_completeness: float,
    diff_lines: int,
    files_changed: int,
) -> float:
    """Estimate human review cost in minutes.

    Formula:
        base * completeness_factor * diff_factor * file_factor

    Where:
        base = 3.0 minutes
        completeness_factor = 1 + 0.3 * (1 - proof_completeness)
        diff_factor = 1 + 0.01 * max(0, diff_lines - 50)
        file_factor = 1 + 0.1 * max(0, files_changed - 3)

    Returns:
        Estimated review time in minutes.
    """
    completeness_factor = 1.0 + _COMPLETENESS_PENALTY_FACTOR * (1.0 - proof_completeness)
    diff_factor = 1.0 + _DIFF_LINE_COST_PER * max(0, diff_lines - _DIFF_LINE_THRESHOLD)
    file_factor = 1.0 + _FILE_COST_PER * max(0, files_changed - _FILE_THRESHOLD)
    return _BASE_COST_MINUTES * completeness_factor * diff_factor * file_factor
