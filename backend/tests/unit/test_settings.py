from pathlib import Path

from app.core.config.settings import Settings


def test_example_environment_satisfies_settings_constraints() -> None:
    example_path = Path(__file__).resolve().parents[2] / ".env.example"

    settings = Settings(_env_file=example_path)

    assert settings.student_dashboard_cache_ttl_seconds >= 300
    assert settings.student_bitmap_cache_ttl_seconds >= 3600
