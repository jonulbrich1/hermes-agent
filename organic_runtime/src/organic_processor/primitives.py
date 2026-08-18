from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrimitiveSpec:
    """Typed, bounded contract for one processor cognitive atom."""

    name: str
    category: str
    requires: frozenset[str] = frozenset()
    provides: frozenset[str] = frozenset()
    goals: frozenset[str] = frozenset()
    required_kinds: frozenset[str] = frozenset()
    verifier_contract: str = "external_verification_required"
    max_applications: int = 1


def _spec(
    name: str,
    category: str,
    *,
    requires: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    goals: tuple[str, ...] = (),
    kinds: tuple[str, ...] = (),
    verifier: str = "external_verification_required",
) -> PrimitiveSpec:
    return PrimitiveSpec(
        name=name,
        category=category,
        requires=frozenset(requires),
        provides=frozenset(provides),
        goals=frozenset(goals),
        required_kinds=frozenset(kinds),
        verifier_contract=verifier,
    )


# This is the seed vocabulary, not a pathway library. A spec is executable only
# when a handler with the same name exists in operators.OPERATORS. Missing
# handlers remain explicit frontier needs instead of being simulated by an LLM.
PRIMITIVE_SPECS: tuple[PrimitiveSpec, ...] = (
    _spec("DEFINE_VARIABLE", "representation", provides=("variables",)),
    _spec("CREATE_CONSTANT", "representation", provides=("constants",)),
    _spec("CREATE_RELATION", "representation", provides=("relations",)),
    _spec(
        "CREATE_EQUATION",
        "representation",
        requires=("variables",),
        provides=("linear_rows", "linear_target"),
        goals=("solve_linear_target",),
        kinds=("linear_equation", "linear_target"),
    ),
    _spec("SET_GOAL", "representation", provides=("goal",)),
    _spec(
        "SUBSTITUTE",
        "algebra",
        requires=("linear_rows",),
        provides=("linear_echelon",),
        goals=("solve_linear_target",),
        kinds=("linear_equation",),
    ),
    _spec(
        "ISOLATE_VARIABLE",
        "algebra",
        requires=("variables", "linear_echelon"),
        provides=("linear_solution",),
        goals=("solve_linear_target",),
        kinds=("linear_equation",),
    ),
    _spec(
        "EVALUATE_EXPRESSION",
        "algebra",
        requires=("linear_solution", "linear_target"),
        provides=("target_value",),
        goals=("solve_linear_target",),
        kinds=("linear_target",),
    ),
    _spec("SIMPLIFY", "algebra", requires=("expression",), provides=("expression",)),
    _spec(
        "SOLVE_LINEAR_SYSTEM",
        "algebra",
        requires=("variables", "linear_rows"),
        provides=("linear_solution",),
        goals=("solve_linear_target",),
        kinds=("linear_equation",),
    ),
    _spec("COMPARE_VALUES", "algebra", requires=("values",), provides=("comparison",)),
    _spec("PROPAGATE_CONSTRAINT", "constraint", requires=("constraints",), provides=("constraints",)),
    _spec("CHECK_CONSISTENCY", "constraint", requires=("constraints",), provides=("consistent",)),
    _spec(
        "ENUMERATE_CASES",
        "constraint",
        provides=("assignments", "relations", "query"),
        goals=("prove_existential_relation",),
        kinds=("directed_relation", "entity_property", "exists_relation_by_property"),
    ),
    _spec("FILTER_CASES", "constraint", requires=("assignments",), provides=("assignments",)),
    _spec("FOLLOW_EDGE", "graph", requires=("graph", "node"), provides=("nodes",)),
    _spec("TRANSITIVE_CLOSURE", "graph", requires=("relations",), provides=("closure",)),
    _spec(
        "TOPOLOGICAL_ORDER",
        "graph",
        requires=("relations",),
        provides=("ordered", "verified"),
        goals=("linearize_order",),
        kinds=("precedes",),
    ),
    _spec("FIND_PATH", "graph", requires=("graph", "start", "goal"), provides=("path",)),
    _spec("MATCH_PATTERN", "graph", requires=("graph", "pattern"), provides=("matches",)),
    _spec("APPLY_RULE", "logic", requires=("facts", "rules"), provides=("facts",)),
    _spec("NEGATE", "logic", requires=("proposition",), provides=("proposition",)),
    _spec("CONJUNCTION", "logic", requires=("propositions",), provides=("proposition",)),
    _spec("DISJUNCTION", "logic", requires=("propositions",), provides=("proposition",)),
    _spec(
        "TEST_ENTAILMENT",
        "logic",
        requires=("assignments", "relations", "query"),
        provides=("case_results", "entailed"),
        goals=("prove_existential_relation",),
        kinds=("directed_relation", "exists_relation_by_property"),
    ),
    _spec("FIND_COUNTEREXAMPLE", "logic", requires=("case_results",), provides=("counterexample",)),
    _spec("BRANCH", "search", requires=("candidates",), provides=("branches",)),
    _spec("BACKTRACK", "search", requires=("branches",), provides=("candidate",)),
    _spec("RANK_CANDIDATES", "search", requires=("candidates",), provides=("ranked_candidates",)),
    _spec(
        "STOP_IF_VERIFIED",
        "search",
        requires=("verified",),
        provides=("answer",),
        verifier="requires_prior_deterministic_check",
    ),
    _spec(
        "CHECK_CONSTRAINTS",
        "verification",
        requires=("linear_solution",),
        provides=("verified",),
        goals=("solve_linear_target",),
        kinds=("linear_equation",),
    ),
    _spec(
        "SUBSTITUTE_AND_VERIFY",
        "verification",
        requires=("linear_solution",),
        provides=("verified",),
        goals=("solve_linear_target",),
        kinds=("linear_equation",),
    ),
    _spec(
        "VERIFY_ALL_CASES",
        "verification",
        requires=("case_results", "entailed"),
        provides=("verified",),
        goals=("prove_existential_relation",),
        kinds=("exists_relation_by_property",),
    ),
    _spec(
        "VERIFY_SOLUTION",
        "verification",
        requires=("linear_solution", "target_value"),
        provides=("verified",),
        goals=("solve_linear_target",),
        kinds=("linear_equation", "linear_target"),
    ),
    _spec("VERIFY_PROVENANCE", "verification", requires=("evidence",), provides=("verified",)),
)


PRIMITIVE_BY_NAME = {spec.name: spec for spec in PRIMITIVE_SPECS}
