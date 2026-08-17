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
    "description": "Ask the bounded Organic Processing Core to reason over a problem and grounded context. The Semantic Interface should use this instead of performing factual/logical reasoning itself.",
    "parameters": {
        "type": "object",
        "properties": {
            "problem": {"type": "string"},
            "context": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Grounded or explicitly labeled candidate evidence supplied to Cognition / Active Weave.",
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
                "enum": ["WEB", "PRIMARY", "USER", "FOREIGN_GRAPH", "DOCUMENT", "OTHER"],
            },
        },
        "required": ["claim", "quote", "source_url", "provenance_family", "source_kind"],
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
            "max_candidates": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
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
