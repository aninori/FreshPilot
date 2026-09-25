"""M0 scaffold smoke test — real config tests land alongside the modules that need them."""

from freshpilot.config import SEED, load_settings


def test_seed_is_deterministic_default() -> None:
    assert SEED == 42


def test_fallback_mode_with_no_api_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CLAUDE.md rule 4: must run with FRESHPILOT_LLM=off and no ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("FRESHPILOT_LLM", "off")
    settings = load_settings()
    assert settings.llm_enabled is False
