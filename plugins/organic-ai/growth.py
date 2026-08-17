"""Growth Frontier policy.

Key rule: lexical/index terms are not growth tasks.
Only promoted, typed, structurally relevant nodes are eligible.
"""

from dataclasses import dataclass
from typing import Any


ALLOWED_KINDS = {
    "ENTITY",
    "CONCEPT",
    "PROCESS",
    "EVENT",
    "PROCEDURE",
    "TOPIC",
}

# This list is intentionally small. The primary protection is typed promotion,
# not an endlessly maintained stopword list.
HARD_REJECT_LABELS = {
    "may",
    "one",
    "two",
    "part",
    "according",
    "including",
    "since",
    "even though",
    "new",
}


@dataclass
class FrontierDecision:
    accepted: bool
    score: float
    reasons: list[str]


class GrowthFrontierGuard:
    def evaluate(self, node: dict[str, Any]) -> FrontierDecision:
        reasons: list[str] = []
        label = str(node.get("label") or "").strip()
        normalized = label.lower()
        kind = str(node.get("kind") or "").upper()
        degree = int(node.get("degree") or 0)
        unresolved = bool(node.get("unresolved") or False)
        task_relevance = float(node.get("task_relevance") or 0.0)
        mention_count = int(node.get("mention_count") or 0)
        source_count = int(node.get("source_count") or 0)
        uncertainty = float(node.get("uncertainty") or 0.0)

        if not label:
            return FrontierDecision(False, -10.0, ["Missing label."])

        if normalized in HARD_REJECT_LABELS:
            return FrontierDecision(False, -10.0, ["Known lexical/function-word artifact."])

        if kind not in ALLOWED_KINDS:
            return FrontierDecision(
                False,
                -8.0,
                [f"Node is not a promoted semantic type (kind={kind or 'NULL'})."],
            )

        if degree <= 0 and not unresolved and task_relevance <= 0:
            return FrontierDecision(
                False,
                -6.0,
                ["No structural edge, unresolved state, or user/task relevance."],
            )

        # Recurrence is useful only after semantic/structural qualification.
        score = 0.0
        score += min(2.0, degree * 0.25)
        score += min(1.5, mention_count / 20.0)
        score += min(1.0, source_count * 0.2)
        score += min(1.5, uncertainty)
        score += min(2.0, task_relevance)
        if unresolved:
            score += 1.0

        reasons.append("Promoted semantic node.")
        reasons.append("Structural/task relevance requirement passed.")
        return FrontierDecision(True, round(score, 4), reasons)


def select_frontier(nodes: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    guard = GrowthFrontierGuard()
    accepted = []
    for node in nodes:
        decision = guard.evaluate(node)
        if decision.accepted:
            item = dict(node)
            item["growth_score"] = decision.score
            item["growth_reasons"] = decision.reasons
            accepted.append(item)
    accepted.sort(key=lambda x: (-float(x["growth_score"]), str(x.get("label", "")).lower()))
    return accepted[:limit]
