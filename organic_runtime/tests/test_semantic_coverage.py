from organic_runtime.contracts import SemanticCompletenessReview
from organic_runtime.semantic.base import enforce_objective_coverage
from organic_runtime.semantic.pydantic_ai_adapter import PydanticAISemanticInterface


def test_coverage_guard_rejects_truncated_current_release_version():
    review = enforce_objective_coverage(
        "What is the latest stable Runtime release version?",
        "Runtime release Runtime 3.",
        {
            "semantic_completeness_complete": True,
            "grounded_version_tokens": ["3.14.7", "3.14.6"],
        },
        SemanticCompletenessReview(
            complete=True,
            needs_tool_loop=False,
            reason="Model accepted the result.",
        ),
    )

    assert review.complete is False
    assert review.needs_tool_loop is True
    assert "specific grounded release version" in review.missing[0]


def test_coverage_guard_accepts_grounded_current_release_version():
    review = enforce_objective_coverage(
        "What is the latest stable Runtime release version?",
        "Runtime 3.14.7 is the latest stable release.",
        {
            "semantic_completeness_complete": True,
            "grounded_version_tokens": ["3.14.7", "3.14.6"],
        },
        SemanticCompletenessReview(
            complete=True,
            needs_tool_loop=False,
            reason="Model accepted the result.",
        ),
    )

    assert review.complete is True
    assert review.needs_tool_loop is False


def test_coverage_guard_requires_requested_freshness_qualifier():
    review = enforce_objective_coverage(
        "What is the latest stable Runtime release version?",
        "Runtime 3.14.7 released on August.",
        {
            "semantic_completeness_complete": True,
            "grounded_version_tokens": ["3.14.7"],
        },
        SemanticCompletenessReview(complete=True),
    )

    assert review.complete is False
    assert review.needs_tool_loop is True


def test_qwen_cannot_rejudge_externally_verified_closed_world_result():
    review = enforce_objective_coverage(
        "How many objects are there?",
        "3 objects.",
        {
            "reasoning_mode": "self_contained",
            "semantic_completeness_complete": True,
            "decision": {
                "result_code": "verified_minimum_model",
                "validation_checks": {
                    "all_constraints_satisfied": True,
                    "minimal_model_verified": True,
                    "external_resources_used": False,
                }
            },
        },
        SemanticCompletenessReview(
            complete=False,
            needs_tool_loop=True,
            missing=["The model tried to re-solve the task."],
        ),
    )

    assert review.complete is True
    assert review.needs_tool_loop is False
    assert review.missing == []


def test_qwen_presentation_guard_rejects_new_numeric_facts():
    original = "Runtime 3.14.7 is the latest stable release."

    guarded = PydanticAISemanticInterface._bounded_presentation(
        "What is the latest stable Runtime release version?",
        original,
        "The latest stable Runtime release is 3.99.0.",
        {"grounded_version_tokens": ["3.14.7"]},
    )

    assert guarded == original


def test_qwen_presentation_guard_accepts_grounded_version_summary():
    original = "Runtime releases by version number include Runtime 3.14.7."

    guarded = PydanticAISemanticInterface._bounded_presentation(
        "What is the latest stable Runtime release version?",
        original,
        "The latest stable Runtime release is 3.14.7.",
        {"grounded_version_tokens": ["3.14.7"]},
    )

    assert guarded == "The latest stable Runtime release is 3.14.7."


def test_qwen_presentation_guard_removes_incomplete_month_fragment():
    guarded = PydanticAISemanticInterface._bounded_presentation(
        "What is the latest stable Runtime release version?",
        "Runtime releases by version number include Runtime 3.14.7 Aug.",
        "Latest stable Runtime release: Runtime 3.14.7 (Aug.)",
        {"grounded_version_tokens": ["3.14.7"]},
    )

    assert guarded == "Latest stable Runtime release: Runtime 3.14.7."
