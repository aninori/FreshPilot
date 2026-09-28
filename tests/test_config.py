"""M0 scaffold smoke test — real config tests land alongside the modules that need them."""

import pytest
from freshpilot.config import SEED, load_settings


def test_seed_is_deterministic_default() -> None:
    assert SEED == 42


def test_fallback_mode_with_no_api_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CLAUDE.md rule 4: must run with FRESHPILOT_LLM=off and no API key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FRESHPILOT_LLM", "off")
    settings = load_settings()
    assert settings.llm_enabled is False


def test_demo_date_accepts_iso_and_empty(monkeypatch):
    monkeypatch.setenv("FRESHPILOT_DEMO_DATE", "2025-06-30")
    assert load_settings().demo_date == "2025-06-30"
    monkeypatch.setenv("FRESHPILOT_DEMO_DATE", "")
    assert load_settings().demo_date is None


def test_demo_date_rejects_invalid_date(monkeypatch):
    monkeypatch.setenv("FRESHPILOT_DEMO_DATE", "2025-02-31")
    with pytest.raises(ValueError):
        load_settings()
