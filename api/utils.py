"""Utility helpers for MergeGate API.

UUID7 generation for primary keys.
Rating display utilities (retained from the legacy leaderboard schema).
"""

from uuid import UUID

from uuid_utils import uuid7


def compute_tier(rating: float) -> str:
    """Compute tier label from Glicko-2 rating.

    Args:
        rating: Glicko-2 rating (initial default is 1500.0).

    Returns:
        One of: "bronze", "silver", "gold", "platinum", "diamond", "grandmaster".
    """
    if rating < 1000:
        return "bronze"
    elif rating < 1200:
        return "silver"
    elif rating < 1400:
        return "gold"
    elif rating < 1600:
        return "platinum"
    elif rating < 1800:
        return "diamond"
    else:
        return "grandmaster"


def format_rating_display(rating: float, rating_deviation: float) -> str:
    """Format Glicko-2 rating as a confidence interval display string.

    Both values are rounded to the nearest integer before formatting,
    so "1847.3 +/- 43.7" becomes "1847 +/- 44".

    Args:
        rating: Glicko-2 rating value.
        rating_deviation: Glicko-2 rating deviation (RD).

    Returns:
        String in format "1500 +/- 350".
    """
    return f"{round(rating)} \u00b1 {round(rating_deviation)}"


def generate_uuid7() -> UUID:
    """Generate a UUID7 value for use as a primary key.

    UUID7 is time-ordered (monotonically increasing within a millisecond),
    making it suitable for primary keys where insert order matters for
    index locality.

    Returns a stdlib uuid.UUID object — asyncpg accepts UUID objects directly
    for PostgreSQL UUID columns. The uuid_utils.UUID type is NOT a subclass of
    stdlib uuid.UUID, so we convert via string to ensure compatibility with
    isinstance checks and asyncpg's type handling.
    """
    return UUID(str(uuid7()))
