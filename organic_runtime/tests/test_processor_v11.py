from __future__ import annotations

from organic_processor import (
    ActionProposal,
    CognitiveContext,
    Evidence,
    OrganicProcessor,
    ProcessOutcome,
    ProcessTrace,
    StructuralTask,
    ThoughtLimits,
    ThoughtPolicyController,
    ThoughtState,
    V7SeedPolicy,
    VerificationReceipt,
)
from organic_processor.primitive_matrix import SEED_PRIMITIVES_40
from organic_processor.seed_policy import EXPECTED_SEED_SHA256, processor_asset_dir


def _task(*, required_operations: tuple[str, ...] = ()) -> StructuralTask:
    return StructuralTask(
        family="symbolic_linear_constraints",
        goal="solve_linear_target",
        constraints=(
            {"kind": "linear_equation", "coefficients": {"x": 1}, "constant": 2},
            {"kind": "linear_target", "coefficients": {"x": 1}, "constant": 0},
        ),
        required_operations=required_operations,
    )


def test_v7_seed_integrity_and_context_ranking() -> None:
    seed = V7SeedPolicy.load()
    assert seed.checksum == EXPECTED_SEED_SHA256
    assert seed.experience_count == 930_838
    assert seed.primitive_names == set(SEED_PRIMITIVES_40)

    context = CognitiveContext(
        goal="solve linear target",
        feature_keys=(
            "family:linear_constraints",
            "domain:linear_direct",
            "variables:3",
            "equations:3",
        ),
    )
    linear = [
        "DEFINE_VARIABLE",
        "CREATE_EQUATION",
        "SUBSTITUTE",
        "ISOLATE_VARIABLE",
        "EVALUATE_EXPRESSION",
        "CHECK_CONSTRAINTS",
        "VERIFY_SOLUTION",
        "STOP_IF_VERIFIED",
    ]
    logic = [
        "SET_GOAL",
        "ENUMERATE_CASES",
        "FILTER_CASES",
        "TEST_ENTAILMENT",
        "FIND_COUNTEREXAMPLE",
        "VERIFY_ALL_CASES",
        "STOP_IF_VERIFIED",
    ]
    assert seed.score_path(linear, context) > seed.score_path(logic, context)


def test_v11_keeps_seed_separate_from_runtime_overlay(tmp_path) -> None:
    processor = OrganicProcessor(tmp_path / "processor.json")
    status = processor.status()
    assert status["version"] == "0.11.0-rc2"
    assert status["v7_seed"]["loaded"] is True
    assert status["v7_seed"]["immutable_base"] is True
    assert status["seed_primitive_count"] == 40
    assert status["declared_primitive_count"] == 35
    assert status["executable_primitive_count"] == 15
    assert status["waiting_primitive_count"] == 25
    assert status["runtime_learning_overlay"]["recoverable_without_seed_mutation"] is True
    assert processor.snapshot()["experience_count"] == 0


def test_missing_seed_primitive_fails_closed(tmp_path) -> None:
    processor = OrganicProcessor(tmp_path / "processor.json")
    outcome = processor.discover(_task(required_operations=("SHORTEST_PATH",)))
    assert outcome.status == "CAPABILITY_GAP"
    assert outcome.capability_gap is not None
    assert "WAITING_FOR_PRIMITIVE" in outcome.capability_gap
    assert outcome.missing_primitives == ["SHORTEST_PATH"]


def test_verifier_is_only_result_authority(tmp_path) -> None:
    processor = OrganicProcessor(tmp_path / "processor.json")
    task = _task()
    wrong = ProcessTrace(
        pathway="wrong",
        operators=["VERIFY_SOLUTION"],
        signature=task.signature(),
        answer=-999,
        valid=True,
    )
    right = ProcessTrace(
        pathway="right",
        operators=["VERIFY_SOLUTION"],
        signature=task.signature(),
        answer=2,
        valid=True,
    )

    def verifier(_task_value: StructuralTask, trace: ProcessTrace) -> VerificationReceipt:
        return VerificationReceipt(
            accepted=trace.answer == 2,
            verifier="test_exact_verifier",
            reason="exact_match",
        )

    outcome = processor.verify_candidates(task, [wrong, right], verifier)
    assert outcome.status == "VERIFIED"
    assert outcome.answer == 2
    assert len(outcome.attempts) == 2


def test_presenter_packet_withholds_unverified_candidate(tmp_path) -> None:
    processor = OrganicProcessor(tmp_path / "processor.json")
    task = _task()
    trace = ProcessTrace(
        pathway="unverified",
        operators=["VERIFY_SOLUTION"],
        signature=task.signature(),
        answer=123,
        valid=True,
    )
    outcome = ProcessOutcome(status="UNRESOLVED", answer=trace.answer, trace=trace)
    packet = processor.presenter_packet("What is x?", outcome, processor.cognitive_context(task))
    assert packet.verified is False
    assert packet.answer is None
    assert "presenter_must_not_invent_an_answer" in packet.warnings


def test_v9_thought_policy_selects_only_authorized_action() -> None:
    assets = processor_asset_dir()
    try:
        thought = ThoughtPolicyController.load(
            assets / "thought_policy_v9.pt",
            assets / "vocab_v9.json",
            assets / "policy_config_v9.json",
        )
    except RuntimeError as exc:
        if "PyTorch is not installed" in str(exc):
            import pytest

            pytest.skip(str(exc))
        raise

    state = ThoughtState(goal="compute", current_query="CASE970001")
    state.evidence.append(
        Evidence(
            "CASE970001_PROC",
            "Case dossier CASE970001. Bounded processor request expression: (5 + 2) * 6. "
            "Use the deterministic processor and verify before answering.",
            30,
            1.0,
            True,
        )
    )
    options = [
        ActionProposal("use_processor", evidence_id="CASE970001_PROC"),
        ActionProposal("retrieve", "CASE970001"),
        ActionProposal("abstain"),
    ]
    decision = thought.choose(state, options, ThoughtLimits(max_thought_tokens=900))
    assert thought.parameter_count > 20_000
    assert decision.selected.action == "use_processor"
    assert decision.selected.action in decision.authorized_actions
    assert decision.token_cost > 0
