"""Model-facing tool schemas for the Organic AI Hermes plugin."""

ORGANIC_GET_STATE = {
    "description": "Read actual Organic AI runtime state. Use for questions about current health, activity, learning, tasks, memory, or growth status.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

ORGANIC_MEMORY_SEARCH = {
    "description": "Search trusted Organic Living Memory. This searches Organic knowledge, not the interface model's pretrained knowledge.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

ORGANIC_REASON = {
    "description": (
        "Send a nontrivial problem to the bounded Organic Processor. For closed-world "
        "problems, translate the user's premises into a domain-neutral structural task "
        "without solving it. Describe the goal, typed constraints, and required reasoning "
        "operations; Organic chooses executable primitives, verifies candidates, learns "
        "successful compositions, and records unknown structures as durable capability gaps."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "problem": {"type": "string"},
            "context": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Grounded or explicitly labeled candidate evidence supplied to Cognition / Active Weave.",
            },
            "structure": {
                "type": "object",
                "description": (
                    "Optional model-produced translation of user-supplied premises. "
                    "It is untrusted input; Organic validates it and owns execution."
                ),
                "properties": {
                    "family": {
                        "type": "string",
                        "description": (
                            "A concise structural family hint, not a domain noun or answer. "
                            "Examples: symbolic_linear_constraints, partial_order, "
                            "boolean_case_analysis. Unknown families are allowed."
                        ),
                    },
                    "goal": {
                        "type": "string",
                        "description": (
                            "Canonical structural objective such as solve_linear_target, "
                            "linearize_order, prove_existential_relation, compare_values, "
                            "or search_path."
                        ),
                    },
                    "required_operations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 24,
                        "description": (
                            "Domain-neutral operations needed, not executable pathway names. "
                            "Seed vocabulary — representation: DEFINE_VARIABLE, "
                            "CREATE_CONSTANT, CREATE_RELATION, CREATE_EQUATION, SET_GOAL; "
                            "algebra: SUBSTITUTE, ISOLATE_VARIABLE, EVALUATE_EXPRESSION, "
                            "SIMPLIFY, SOLVE_LINEAR_SYSTEM, COMPARE_VALUES; constraints: "
                            "PROPAGATE_CONSTRAINT, CHECK_CONSISTENCY, ENUMERATE_CASES, "
                            "FILTER_CASES; graphs: FOLLOW_EDGE, TRANSITIVE_CLOSURE, "
                            "TOPOLOGICAL_ORDER, FIND_PATH, MATCH_PATTERN; logic: APPLY_RULE, "
                            "NEGATE, CONJUNCTION, DISJUNCTION, TEST_ENTAILMENT, "
                            "FIND_COUNTEREXAMPLE; control: BRANCH, BACKTRACK, "
                            "RANK_CANDIDATES, STOP_IF_VERIFIED; verification: "
                            "CHECK_CONSTRAINTS, SUBSTITUTE_AND_VERIFY, VERIFY_ALL_CASES, "
                            "VERIFY_SOLUTION, VERIFY_PROVENANCE. Unknown operation names are "
                            "allowed and become explicit primitive frontier needs."
                        ),
                    },
                    "constraints": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "description": (
                            "Typed premises only; never include a proposed answer. Preserve "
                            "relations and unknowns explicitly. For linear equations, use "
                            "kind=linear_equation with numeric coefficients on the left and "
                            "constant on the right; use one kind=linear_target for the goal."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                },
                                "coefficients": {
                                    "type": "object",
                                    "additionalProperties": {"type": "number"},
                                },
                                "constant": {"type": "number", "default": 0},
                                "label": {"type": "string"},
                            },
                            "required": ["kind"],
                            "additionalProperties": True,
                        },
                    },
                },
                "required": ["family", "goal", "required_operations", "constraints"],
                "additionalProperties": False,
            },
        },
        "required": ["problem"],
        "additionalProperties": False,
    },
}

ORGANIC_RESEARCH = {
    "description": "Acquire external evidence through Hermes tooling for an Organic knowledge gap. Results remain evidence candidates and are not automatically trusted memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["query", "reason"],
        "additionalProperties": False,
    },
}

ORGANIC_SUBMIT_EVIDENCE = {
    "description": "Submit a source-grounded evidence span to Organic validation. This does not directly create trusted memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "quote": {"type": "string"},
            "source_url": {"type": "string"},
            "source_title": {"type": "string"},
            "provenance_family": {"type": "string"},
            "source_kind": {
                "type": "string",
                "enum": [
                    "WEB",
                    "PRIMARY",
                    "USER",
                    "FOREIGN_GRAPH",
                    "DOCUMENT",
                    "OTHER",
                ],
            },
        },
        "required": [
            "claim",
            "quote",
            "source_url",
            "provenance_family",
            "source_kind",
        ],
        "additionalProperties": False,
    },
}

ORGANIC_VALIDATE_CLAIM = {
    "description": "Evaluate whether a candidate claim has enough independent evidence to enter trusted Living Memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "claim_id": {"type": "string"},
        },
        "required": ["claim_id"],
        "additionalProperties": False,
    },
}

ORGANIC_GROWTH_FRONTIER = {
    "description": "Inspect safe, typed, structurally relevant Organic growth-frontier candidates. Raw lexical anchors are excluded.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        },
        "additionalProperties": False,
    },
}

ORGANIC_GROWTH_CYCLE = {
    "description": "Run one bounded Organic idle-growth planning cycle. The cycle selects only safe frontier nodes and returns a growth objective; it does not blindly ingest entire pages.",
    "parameters": {
        "type": "object",
        "properties": {
            "max_candidates": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
            },
        },
        "additionalProperties": False,
    },
}

ORGANIC_EXPORT_REVIEW = {
    "description": "Export an Organic AI review bundle under the project's reveiw folder.",
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "default": "manual"},
        },
        "additionalProperties": False,
    },
}
