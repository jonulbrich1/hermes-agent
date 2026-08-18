from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ActionProposal, ThoughtDecision, ThoughtLimits, ThoughtState

_WORD = re.compile(r"[A-Za-z_]+|\d+|[^\sA-Za-z\d_]")
_CASE = re.compile(r"CASE\d+", re.IGNORECASE)
_NODE = re.compile(r"NODE[A-Z0-9_]+", re.IGNORECASE)
_CODE = re.compile(r"CODE\d+", re.IGNORECASE)
_NUM = re.compile(r"\b\d+\b")

V9_VALIDATED_ACTIONS = frozenset(
    {
        "retrieve",
        "follow_reference",
        "use_processor",
        "hypothesize",
        "verify",
        "answer",
        "abstain",
    }
)


class ThoughtPolicyUnavailable(RuntimeError):
    pass


def normalize_text(text: str) -> str:
    text = _CASE.sub(" CASE_ID ", text)
    text = _NODE.sub(" NODE_ID ", text)
    text = _CODE.sub(" CODE_VALUE ", text)
    text = _NUM.sub(" NUM ", text)
    return " ".join(text.lower().split())


class TinyTokenizer:
    def __init__(self, vocab: dict[str, int]) -> None:
        self.vocab = vocab

    @staticmethod
    def pieces(text: str) -> list[str]:
        return [piece.lower() for piece in _WORD.findall(normalize_text(text))]

    def encode(self, text: str, max_tokens: int) -> list[int]:
        return [self.vocab.get(token, 1) for token in self.pieces(text)][:max_tokens] or [1]


def _evidence_index(state: ThoughtState, evidence_id: str | None) -> int | None:
    if evidence_id is None:
        return None
    for index, evidence in enumerate(state.evidence):
        if evidence.doc_id == evidence_id:
            return index
    return None


def render_state_option(state: ThoughtState, option: ActionProposal) -> str:
    empty = int(state.metadata.get("empty_retrievals", 0))
    empty_state = "none" if empty == 0 else ("once" if empty == 1 else "multiple")
    no_progress = int(state.metadata.get("no_progress_retrievals", 0))
    progress = (
        "fresh"
        if no_progress == 0
        else ("stalled_once" if no_progress == 1 else "stalled_repeated")
    )
    verification = (
        "none"
        if state.last_verification is None
        else ("passed" if state.last_verification else "failed")
    )
    pressure = (
        "low" if state.retrieval_calls < 6 else ("medium" if state.retrieval_calls < 12 else "high")
    )
    stage = "early" if state.cycle < 7 else ("middle" if state.cycle < 15 else "late")
    parts = [
        "STATE",
        f"query_type {'case' if state.current_query.upper().startswith('CASE') else 'node'}",
        f"cycle_stage {stage}",
        f"retrieval_pressure {pressure}",
        f"has_candidate {'yes' if state.candidate is not None else 'no'}",
        f"verification_state {verification}",
        f"empty_retrieval_state {empty_state}",
        f"retrieval_progress {progress}",
    ]
    for index, evidence in enumerate(state.evidence[:6]):
        parts.append(f"EVIDENCE_{index} {evidence.text}")
    evidence_index = _evidence_index(state, option.evidence_id)
    parts.append(f"OPTION {option.action}")
    if option.argument:
        parts.append(f"argument {option.argument}")
    if evidence_index is not None:
        evidence = state.evidence[evidence_index]
        parts.append(f"option_evidence EVIDENCE_{evidence_index} {evidence.text}")
    return " | ".join(parts)


def _build_model(torch: Any, vocab_size: int, emb_dim: int, hidden: int) -> Any:
    class NeuralActionRanker(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embedding = torch.nn.Embedding(vocab_size, emb_dim, padding_idx=0)
            self.net = torch.nn.Sequential(
                torch.nn.Linear(emb_dim, hidden),
                torch.nn.GELU(),
                torch.nn.LayerNorm(hidden),
                torch.nn.Linear(hidden, hidden // 2),
                torch.nn.GELU(),
                torch.nn.Linear(hidden // 2, 1),
            )

        def forward(self, token_ids: Any) -> Any:
            mask = token_ids.ne(0).unsqueeze(-1)
            embedded = self.embedding(token_ids) * mask
            denominator = mask.sum(dim=1).clamp(min=1)
            return self.net(embedded.sum(dim=1) / denominator).squeeze(-1)

    return NeuralActionRanker()


@dataclass
class ThoughtPolicyController:
    model: Any
    tokenizer: TinyTokenizer
    torch: Any
    max_tokens: int = 192

    @classmethod
    def load(
        cls,
        model_path: str | Path,
        vocab_path: str | Path,
        config_path: str | Path,
    ) -> ThoughtPolicyController:
        try:
            import torch
        except ImportError as exc:
            raise ThoughtPolicyUnavailable(
                "PyTorch is not installed. Install organic-ai-runtime[thought] to enable V9."
            ) from exc

        metadata = json.loads(Path(config_path).read_text(encoding="utf-8"))
        vocab = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
        model = _build_model(
            torch,
            len(vocab),
            int(metadata["emb_dim"]),
            int(metadata["hidden"]),
        )
        model.load_state_dict(torch.load(Path(model_path), map_location="cpu", weights_only=True))
        model.eval()
        return cls(model, TinyTokenizer(vocab), torch, int(metadata.get("max_tokens", 192)))

    @property
    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.model.parameters())

    def choose(
        self,
        state: ThoughtState,
        options: list[ActionProposal],
        limits: ThoughtLimits,
        *,
        mode: str = "shadow",
        allow_untrained_actions: bool = False,
    ) -> ThoughtDecision:
        if not options:
            raise ValueError("no_authorized_actions")
        if not allow_untrained_actions:
            unsupported = sorted({option.action for option in options} - V9_VALIDATED_ACTIONS)
            if unsupported:
                raise ValueError("untrained_thought_actions:" + ",".join(unsupported))

        texts = [render_state_option(state, option) for option in options]
        encoded = [self.tokenizer.encode(text, self.max_tokens) for text in texts]
        width = max(len(item) for item in encoded)
        batch = self.torch.zeros((len(encoded), width), dtype=self.torch.long)
        for index, token_ids in enumerate(encoded):
            batch[index, : len(token_ids)] = self.torch.tensor(token_ids, dtype=self.torch.long)
        with self.torch.no_grad():
            scores = self.model(batch).cpu().tolist()
        best = max(range(len(options)), key=lambda index: scores[index])
        lengths = [
            min(limits.max_model_tokens_per_decision, len(self.tokenizer.pieces(text)))
            for text in texts
        ]
        token_cost = (max(lengths) if lengths else 1) + max(0, len(texts) - 1) + 1
        if token_cost > limits.max_thought_tokens:
            raise ValueError("thought_token_budget_exceeded")
        return ThoughtDecision(
            selected=options[best],
            token_cost=token_cost,
            scores=tuple(float(score) for score in scores),
            authorized_actions=tuple(option.action for option in options),
            mode=mode,
        )
