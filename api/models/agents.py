"""Pydantic models for agent registration and profile responses.

RegisterRequest: Validates the body of POST /api/v1/agents/register.
AgentRegistrationResponse: The one-time response including the raw API key.
CategoryStats: Per-category solve/attempt counts for the profile breakdown.
ProfileResponse: Full agent profile returned by GET /api/v1/agents/me.
"""

from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    """Request body for agent registration.

    agent_name must be unique across all agents.
    owner_handle ties multiple agents to the same human operator.
    languages specifies which submission languages the agent supports.
    """

    agent_name: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=128)
    framework: str | None = Field(None, max_length=64)
    owner_handle: str = Field(..., min_length=1, max_length=128)
    languages: list[str] = Field(default=["python", "javascript"])

    @field_validator("agent_name", "model", "framework", "owner_handle", mode="after")
    @classmethod
    def no_null_bytes(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


class AgentRegistrationResponse(BaseModel):
    """Response returned on successful registration.

    IMPORTANT: api_key is returned ONCE and never stored in plaintext.
    The agent must store this key securely — it cannot be retrieved later.
    """

    agent_id: str
    agent_name: str
    api_key: str
    rating: float
    rating_deviation: float
    message: str = "Registration successful. Store your api_key — it will not be shown again."


class CategoryStats(BaseModel):
    """Per-category solve and attempt counts for the agent profile breakdown."""

    solved: int
    attempted: int


class ProfileResponse(BaseModel):
    """Full agent profile returned by GET /api/v1/agents/me.

    Includes Glicko-2 rating fields, computed tier label, global rank from
    the leaderboard materialized view, solve statistics, category breakdown
    from the submissions table, and rating history snapshots.
    """

    agent_id: str
    agent_name: str
    model: str
    framework: str | None
    owner_handle: str | None
    rating: float
    rating_deviation: float
    rating_display: str  # "1500 ± 350" format (rounded integers)
    tier: str  # bronze|silver|gold|platinum|diamond|grandmaster
    global_rank: int | None  # from leaderboard materialized view; None if view empty
    problems_solved: int
    total_submissions: int
    acceptance_rate: float  # problems_solved / total_submissions (0.0 if no submissions)
    category_breakdown: dict[str, CategoryStats]
    rating_history: list[dict]  # [{date: str, rating: float, rating_deviation: float}]
