"""Application configuration via pydantic-settings.

All configuration is sourced from environment variables (with .env file support).
DATABASE_URL is typed as str to avoid PostgresDsn canonicalization stripping
the +asyncpg driver suffix (see RESEARCH.md Pitfall 5).
"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic_settings import BaseSettings


def _read_pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        return tomllib.load(f)["project"]["version"]


class Settings(BaseSettings):
    database_url: str = "postgresql://mergegate:mergegate@localhost:5432/mergegate"
    version: str = _read_pyproject_version()
    test_database_url: str = "postgresql://mergegate:mergegate@localhost:5433/mergegate_test"
    log_format: str = "json"  # "json" for production, "console" for dev
    log_level: str = "INFO"
    api_version: str = "v1"
    app_name: str = "mergegate"
    sandbox_workdir: str | None = None  # shared host dir for Docker socket forwarding
    seccomp_profile: str | None = None  # host-visible path to seccomp JSON for sandbox
    admin_token: str | None = None  # X-Admin-Token for admin endpoints; None = all admin disabled
    admin_api_key: str | None = (
        None  # Required for variance report endpoint; set via ADMIN_API_KEY env var
    )
    category_weights_json: str = (
        ""  # JSON dict of adversarial_category -> float weight; empty = equal weights
    )

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton Settings instance.

    Cached after first call so env vars are read once per process.
    Call get_settings.cache_clear() in tests to force re-read.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
