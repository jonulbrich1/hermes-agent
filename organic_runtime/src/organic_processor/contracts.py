from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProcessorBudgets:
    max_candidate_paths: int = 16
    max_path_length: int = 12
    max_operator_steps: int = 128


@dataclass(frozen=True)
class ThoughtLimits:
    max_thought_tokens: int = 2048
    max_cycles: int = 20
    max_context_tokens: int = 32_000
    max_retrieval_calls: int = 14
    max_model_tokens_per_decision: int = 192


@dataclass(frozen=True)
class CognitiveContext:
    """Canonical state supplied by Cognition rather than free-form model labels."""

    goal: str
    feature_keys: tuple[str, ...] = ()
    operator_feature_keys: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    uncertainty: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationReceipt:
    accepted: bool
    verifier: str
    reason: str = ""
    confidence: float = 1.0
    evidence_refs: tuple[str, ...] = ()
    reward: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)
    expected: Any = None


@dataclass
class VerifiedAttempt:
    trace: Any
    verification: VerificationReceipt
    seed_score: float = 0.0


@dataclass
class ProcessOutcome:
    status: str
    answer: Any = None
    trace: Any = None
    verification: VerificationReceipt | None = None
    capability_gap: str | None = None
    missing_primitives: list[str] = field(default_factory=list)
    attempts: list[VerifiedAttempt] = field(default_factory=list)
    seed_score: float | None = None


@dataclass(frozen=True)
class ActionProposal:
    action: str
    argument: str | None = None
    evidence_id: str | None = None

    def key(self) -> tuple[str, str | None, str | None]:
        return self.action, self.argument, self.evidence_id


@dataclass(frozen=True)
class ContextRequest:
    """A request to Cognition, never direct processor access to external state."""

    action: str
    query: str | None = None
    evidence_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Evidence:
    doc_id: str
    text: str
    token_estimate: int = 0
    score: float = 0.0
    authoritative: bool = False


@dataclass
class ThoughtState:
    goal: str
    current_query: str
    cycle: int = 0
    thought_tokens_used: int = 0
    retrieval_calls: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    candidate: str | None = None
    candidate_evidence_id: str | None = None
    last_verification: bool | None = None
    verified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ThoughtDecision:
    selected: ActionProposal
    token_cost: int
    scores: tuple[float, ...]
    authorized_actions: tuple[str, ...]
    mode: str

    def context_request(self) -> ContextRequest | None:
        if self.selected.action not in {"retrieve", "follow_reference"}:
            return None
        return ContextRequest(
            action=self.selected.action,
            query=self.selected.argument,
            evidence_id=self.selected.evidence_id,
            reason="Bounded thought requested additional context from Cognition.",
        )


@dataclass(frozen=True)
class PresenterPacket:
    """Trusted boundary object supplied to the final Hermes presenter."""

    question: str
    status: str
    verified: bool
    answer: Any = None
    evidence_refs: tuple[str, ...] = ()
    processor_path: tuple[str, ...] = ()
    verifier: str | None = None
    uncertainty: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
