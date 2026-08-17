from __future__ import annotations

import re
from typing import Protocol

from organic_runtime.contracts import (
    IntentEnvelope,
    RuntimeStateSnapshot,
    SemanticCompletenessReview,
)


def enforce_objective_coverage(
    request: str,
    answer: str,
    metadata: dict,
    review: SemanticCompletenessReview,
) -> SemanticCompletenessReview:
    """Apply deterministic coverage floors after the semantic model review."""
    decision = metadata.get("decision") or {}
    validation_checks = decision.get("validation_checks") or {}
    if (
        metadata.get("reasoning_mode") == "self_contained"
        and metadata.get("semantic_completeness_complete") is True
        and validation_checks
        and str(decision.get("result_code") or "").startswith("verified_")
        and validation_checks.get("external_resources_used") is False
        and all(
            bool(value)
            for key, value in validation_checks.items()
            if key != "external_resources_used"
        )
    ):
        return SemanticCompletenessReview(
            complete=True,
            needs_tool_loop=False,
            missing=[],
            reason=(
                "Coverage accepted from the externally verified closed-world result; "
                "the Semantic Interface cannot rejudge its truth."
            ),
        )

    missing = list(review.missing)
    complete = bool(review.complete)
    needs_tool_loop = bool(review.needs_tool_loop)
    reason = review.reason

    if bool(metadata.get("hard_blocked")) or metadata.get(
        "semantic_completeness_complete"
    ) is False:
        complete = False

    request_terms = set(re.findall(r"[a-z]+", request.lower()))
    evidence_versions = {
        str(version)
        for version in (metadata.get("grounded_version_tokens") or [])
        if re.fullmatch(r"\d+(?:\.\d+){1,3}", str(version))
    }
    answer_versions = set(re.findall(r"\b\d+(?:\.\d+){1,3}\b", answer))
    asks_for_release_version = bool(
        {"release", "version"} & request_terms
        and {"current", "latest", "newest", "recent", "stable"} & request_terms
    )
    if asks_for_release_version and evidence_versions and not (
        answer_versions & evidence_versions
    ):
        complete = False
        needs_tool_loop = True
        detail = "The result omits the specific grounded release version."
        if detail not in missing:
            missing.append(detail)
        reason = "Deterministic objective coverage guard rejected a truncated version result."

    requested_qualifiers = {
        term for term in ("current", "latest", "newest", "recent", "stable")
        if term in request_terms
    }
    answer_terms = set(re.findall(r"[a-z]+", answer.lower()))
    if asks_for_release_version and requested_qualifiers and not (
        requested_qualifiers & answer_terms
    ):
        complete = False
        needs_tool_loop = True
        detail = "The result does not identify the version with the requested freshness qualifier."
        if detail not in missing:
            missing.append(detail)
        reason = "Deterministic objective coverage guard found an incomplete version summary."

    return SemanticCompletenessReview(
        complete=complete,
        needs_tool_loop=needs_tool_loop,
        missing=missing,
        reason=reason,
    )


def clarification_subject(request: str) -> str | None:
    """Return the subject of a short preference that lacks an actionable objective."""
    text = " ".join(request.strip().split()).rstrip(".! ")
    match = re.fullmatch(
        r"(?:i\s+)?(?:want|need|would\s+like|feel\s+like)\s+(.{1,80})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    subject = match.group(1).strip()
    lowered = subject.lower()
    explicit_starts = (
        "to ",
        "you to ",
        "find ",
        "explain ",
        "research ",
        "build ",
        "create ",
        "write ",
        "compare ",
        "show ",
        "tell ",
        "check ",
        "debug ",
        "fix ",
    )
    if lowered.startswith(explicit_starts) or len(subject.split()) > 8:
        return None
    return subject


def looks_self_contained_reasoning(request: str) -> bool:
    """Recognize tasks whose complete premises are supplied by the user."""
    text = " ".join(request.strip().lower().split())
    if any(
        phrase in text
        for phrase in (
            "logic puzzle",
            "logic problem",
            "brain teaser",
            "solve this puzzle",
            "solve the puzzle",
            "deduce ",
            "riddle:",
        )
    ):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\s*[+*/-]\s*\d+(?:\.\d+)?\b", text):
        return True
    return all(phrase in text for phrase in ("in front of", "behind", "in the middle"))


class SemanticInterface(Protocol):
    async def analyze(
        self,
        request: str,
        state: RuntimeStateSnapshot,
    ) -> IntentEnvelope:
        """Translate raw user language into a typed intent envelope."""
        ...

    async def respond_fast(
        self,
        request: str,
        envelope: IntentEnvelope,
        state: RuntimeStateSnapshot,
    ) -> str:
        """Respond only after the programmatic gate authorizes the fast path."""
        ...

    async def review_completeness(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> SemanticCompletenessReview:
        """Check whether the result addresses the objective, never whether it is true."""
        ...

    async def present_result(
        self,
        request: str,
        envelope: IntentEnvelope,
        answer: str,
        metadata: dict,
    ) -> str:
        """Present a validated Organic result without adding factual content."""
        ...
