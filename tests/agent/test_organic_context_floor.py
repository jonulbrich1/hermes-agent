from agent.model_metadata import (
    MINIMUM_CONTEXT_LENGTH,
    ORGANIC_SEMANTIC_MINIMUM_CONTEXT_LENGTH,
    required_minimum_context_length,
)


def test_normal_hermes_context_floor_is_unchanged(monkeypatch):
    monkeypatch.delenv("ORGANIC_SEMANTIC_INTERFACE", raising=False)
    assert required_minimum_context_length() == MINIMUM_CONTEXT_LENGTH == 64_000


def test_organic_semantic_role_has_truthful_32k_floor(monkeypatch):
    monkeypatch.setenv("ORGANIC_SEMANTIC_INTERFACE", "1")
    assert required_minimum_context_length() == ORGANIC_SEMANTIC_MINIMUM_CONTEXT_LENGTH == 32_000
