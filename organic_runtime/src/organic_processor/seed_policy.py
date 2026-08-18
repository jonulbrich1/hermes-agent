from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from .contracts import CognitiveContext
from .primitive_matrix import SEED_PRIMITIVES_40


class SeedFormatError(RuntimeError):
    pass


EXPECTED_SEED_SHA256 = "f35cd1d9fc1ef44721234318b3aea6ae73ba577a7ac15f7f5acd60e179cb8e81"
EXPECTED_EXPERIENCES = 930_838


def processor_asset_dir() -> Path:
    configured = os.getenv("ORGANIC_PROCESSOR_ASSET_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    packaged = Path(__file__).resolve().parent / "assets"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[2] / "assets" / "processor"


def default_seed_path() -> Path:
    return processor_asset_dir() / "processor_seed_state_v7.json"


def path_edges(operators: Sequence[str]) -> list[str]:
    previous = "START"
    result: list[str] = []
    for operator in operators:
        name = str(operator).strip().upper()
        if not name:
            continue
        result.append(f"{previous}->{name}")
        previous = name
    return result


class V7SeedPolicy:
    """Immutable base policy used only to rank bounded primitive paths."""

    def __init__(self, state: dict, *, source_path: Path | None = None, checksum: str = "") -> None:
        if int(state.get("schema_version", 0)) != 1:
            raise SeedFormatError("unsupported_seed_schema")
        if int(state.get("experience_count", 0)) != EXPECTED_EXPERIENCES:
            raise SeedFormatError("unexpected_seed_experience_count")
        if state.get("learned_pathways") not in ({}, None) or state.get("composites") not in (
            {},
            None,
        ):
            raise SeedFormatError("seed_must_not_contain_full_pathways")

        self.source_path = source_path
        self.checksum = checksum
        self.edge_weights = {
            str(key): float(value) for key, value in state.get("edge_weights", {}).items()
        }
        self.context_edges = {
            str(key): {str(edge): float(value) for edge, value in bucket.items()}
            for key, bucket in state.get("context_edge_weights", {}).items()
        }
        self.context_ops = {
            str(key): {str(operator): float(value) for operator, value in bucket.items()}
            for key, bucket in state.get("context_operator_weights", {}).items()
        }
        if not self.edge_weights:
            raise SeedFormatError("seed_has_no_edge_weights")
        if self.primitive_names != set(SEED_PRIMITIVES_40):
            raise SeedFormatError("unexpected_seed_primitive_vocabulary")

    @classmethod
    def load(cls, path: str | Path | None = None) -> V7SeedPolicy:
        source = Path(path) if path else default_seed_path()
        payload = source.read_bytes()
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != EXPECTED_SEED_SHA256:
            raise SeedFormatError("seed_checksum_mismatch")
        return cls(
            json.loads(payload.decode("utf-8")), source_path=source.resolve(), checksum=checksum
        )

    @property
    def primitive_names(self) -> set[str]:
        names: set[str] = set()
        for edge in self.edge_weights:
            left, _, right = edge.partition("->")
            if left and left != "START":
                names.add(left)
            if right:
                names.add(right)
        return names

    @property
    def experience_count(self) -> int:
        return EXPECTED_EXPERIENCES

    def score_path(
        self,
        operators: Sequence[str],
        context: CognitiveContext | None = None,
    ) -> float:
        edges = path_edges(operators)
        score = 0.12 * sum(self.edge_weights.get(edge, 0.0) for edge in edges)
        if context is None:
            return float(score)

        feature_scores = []
        for feature in context.feature_keys:
            bucket = self.context_edges.get(feature)
            if bucket:
                feature_scores.append(sum(bucket.get(edge, 0.0) for edge in edges))
        if feature_scores:
            score += sum(feature_scores) / len(feature_scores)

        unique_ops = {str(operator).upper() for operator in operators}
        op_scores = []
        for feature in context.operator_feature_keys or context.feature_keys:
            bucket = self.context_ops.get(feature)
            if bucket:
                op_scores.append(sum(bucket.get(operator, 0.0) for operator in unique_ops))
        if op_scores:
            score += 2.0 * (sum(op_scores) / len(op_scores))
        if not math.isfinite(score):
            raise SeedFormatError("non_finite_path_score")
        return float(score)

    def rank_paths(
        self,
        candidates: Iterable[tuple[str, Sequence[str]]],
        context: CognitiveContext | None = None,
    ) -> list[tuple[str, list[str], float]]:
        ranked = [
            (
                str(name),
                [str(operator).upper() for operator in operators],
                self.score_path(operators, context),
            )
            for name, operators in candidates
        ]
        ranked.sort(key=lambda row: (-row[2], row[0], tuple(row[1])))
        return ranked

    def status(self) -> dict[str, object]:
        return {
            "loaded": True,
            "version": "V7",
            "source_path": str(self.source_path) if self.source_path else None,
            "sha256": self.checksum,
            "experience_count": self.experience_count,
            "primitive_count": len(self.primitive_names),
            "context_feature_count": len(self.context_edges),
            "operator_feature_count": len(self.context_ops),
            "immutable_base": True,
        }
