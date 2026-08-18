from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        print(f"[config] Invalid {name}={value!r}; using {default}")
        return default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"[config] Invalid {name}={value!r}; using {default}")
        return default


def _hermes_project_root() -> Path | None:
    value = os.getenv("ORGANIC_PROJECT_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "HERMES_PIN.txt").exists() and (cwd / "overlay" / "plugins" / "organic-ai").exists():
        return cwd
    return None


@dataclass(frozen=True)
class RuntimeSettings:
    semantic_mode: str = "heuristic"
    backend: str = "mvp"
    model: str = "inherit"
    ollama_base_url: str | None = None
    trace_dir: Path = Path("runtime_data/traces")
    mvp_data_dir: Path = Path("runtime_data/mvp")
    web_provider: str = "auto"
    local_corpus_dir: Path | None = None
    allow_private_web: bool = False
    idle_growth_enabled: bool = True
    idle_delay_seconds: int = 2
    bootstrap_growth_enabled: bool = True
    bootstrap_growth_query: str = "organic ai cognition memory evidence growth"
    processor_max_cycles: int = 16
    processor_seed_enabled: bool = True
    processor_seed_path: Path | None = None
    thought_enabled: bool = False
    thought_shadow: bool = True
    thought_policy: str = "v9"
    thought_max_tokens: int = 2048
    thought_max_cycles: int = 20
    thought_max_context_tokens: int = 32_000
    thought_max_retrieval_calls: int = 14
    thought_max_model_tokens_per_decision: int = 192
    fast_complexity_max: float = 0.25
    fast_uncertainty_max: float = 0.35
    known_confidence_min: float = 0.80
    escalate_uncertainty: float = 0.55

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        hermes_root = _hermes_project_root()
        default_trace_dir = (
            hermes_root / "runtime" / "organic_home" / "traces"
            if hermes_root
            else Path("runtime_data/traces")
        )
        default_mvp_dir = (
            hermes_root / "runtime" / "organic_home" / "mvp"
            if hermes_root
            else Path("runtime_data/mvp")
        )
        return cls(
            semantic_mode=os.getenv("ORGANIC_SEMANTIC_MODE", "heuristic").strip().lower(),
            backend=os.getenv("ORGANIC_BACKEND", "mvp").strip().lower(),
            model=os.getenv("ORGANIC_MODEL", "inherit").strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL"),
            trace_dir=Path(os.getenv("ORGANIC_TRACE_DIR", str(default_trace_dir))),
            mvp_data_dir=Path(os.getenv("ORGANIC_MVP_DATA_DIR", str(default_mvp_dir))),
            web_provider=os.getenv("ORGANIC_WEB_PROVIDER", "auto").strip().lower(),
            local_corpus_dir=Path(os.getenv("ORGANIC_LOCAL_CORPUS_DIR")).expanduser()
            if os.getenv("ORGANIC_LOCAL_CORPUS_DIR")
            else None,
            allow_private_web=os.getenv("ORGANIC_ALLOW_PRIVATE_WEB", "0").strip().lower()
            in {"1", "true", "yes", "on"},
            idle_growth_enabled=os.getenv("ORGANIC_IDLE_GROWTH_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            idle_delay_seconds=_int_env("ORGANIC_IDLE_DELAY_SECONDS", 2),
            bootstrap_growth_enabled=os.getenv("ORGANIC_BOOTSTRAP_GROWTH_ENABLED", "1")
            .strip()
            .lower()
            not in {"0", "false", "no", "off"},
            bootstrap_growth_query=os.getenv(
                "ORGANIC_BOOTSTRAP_GROWTH_QUERY",
                "organic ai cognition memory evidence growth",
            ).strip(),
            processor_max_cycles=max(
                4,
                min(_int_env("ORGANIC_PROCESSOR_MAX_CYCLES", 16), 128),
            ),
            processor_seed_enabled=os.getenv("ORGANIC_PROCESSOR_SEED_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            processor_seed_path=(
                Path(os.environ["ORGANIC_PROCESSOR_SEED_PATH"]).expanduser()
                if os.getenv("ORGANIC_PROCESSOR_SEED_PATH")
                else None
            ),
            thought_enabled=os.getenv("ORGANIC_THOUGHT_ENABLED", "0").strip().lower()
            in {"1", "true", "yes", "on"},
            thought_shadow=os.getenv("ORGANIC_THOUGHT_SHADOW", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            thought_policy=os.getenv("ORGANIC_THOUGHT_POLICY", "v9").strip().lower(),
            thought_max_tokens=max(64, _int_env("ORGANIC_THOUGHT_MAX_TOKENS", 2048)),
            thought_max_cycles=max(1, _int_env("ORGANIC_THOUGHT_MAX_CYCLES", 20)),
            thought_max_context_tokens=max(
                1024, _int_env("ORGANIC_THOUGHT_MAX_CONTEXT_TOKENS", 32_000)
            ),
            thought_max_retrieval_calls=max(0, _int_env("ORGANIC_THOUGHT_MAX_RETRIEVAL_CALLS", 14)),
            thought_max_model_tokens_per_decision=max(
                16, _int_env("ORGANIC_THOUGHT_MAX_MODEL_TOKENS", 192)
            ),
            fast_complexity_max=_float_env("ORGANIC_GATE_FAST_COMPLEXITY_MAX", 0.25),
            fast_uncertainty_max=_float_env("ORGANIC_GATE_FAST_UNCERTAINTY_MAX", 0.35),
            known_confidence_min=_float_env("ORGANIC_GATE_KNOWN_CONFIDENCE_MIN", 0.80),
            escalate_uncertainty=_float_env("ORGANIC_GATE_ESCALATE_UNCERTAINTY", 0.55),
        )
