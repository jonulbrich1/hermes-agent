from __future__ import annotations

import ast
import heapq
import itertools
import math
import operator
from collections.abc import Callable
from fractions import Fraction
from typing import Any

from .types import StructuralTask


class OperatorError(RuntimeError):
    pass


def _fraction(value: Any) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise OperatorError("Linear coefficients must be finite numbers")
    result = Fraction(str(value))
    if (
        abs(result) > 10**12
        or abs(result.numerator) > 10**18
        or result.denominator > 10**18
    ):
        raise OperatorError("Linear coefficient exceeds the processor budget")
    return result


def _plain_number(value: Fraction) -> int | float:
    return int(value) if value.denominator == 1 else float(value)


def define_linear_system(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    equations = [item for item in task.constraints if item.get("kind") == "linear_equation"]
    targets = [item for item in task.constraints if item.get("kind") == "linear_target"]
    if not equations or len(targets) != 1 or len(equations) > 16:
        raise OperatorError("A bounded linear task needs equations and exactly one target")
    variables = sorted(
        {
            str(name).strip()
            for item in (*equations, targets[0])
            for name in (item.get("coefficients") or {})
            if str(name).strip()
        }
    )
    if not variables or len(variables) > 16 or len(equations) != len(variables):
        raise OperatorError("Linear system must be square and contain 1-16 variables")
    rows = []
    for equation in equations:
        coefficients = equation.get("coefficients")
        if not isinstance(coefficients, dict) or not coefficients:
            raise OperatorError("Each linear equation needs coefficients")
        rows.append(
            [
                *[_plain_number(_fraction(coefficients.get(name, 0))) for name in variables],
                _plain_number(_fraction(equation.get("constant", 0))),
            ]
        )
    return {
        **state,
        "linear_variables": variables,
        "linear_rows": rows,
        "linear_target": dict(targets[0]),
    }


def solve_linear_system(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    variables = list(state.get("linear_variables") or [])
    matrix = [[_fraction(value) for value in row] for row in state.get("linear_rows") or []]
    size = len(variables)
    if len(matrix) != size:
        raise OperatorError("Linear system is incomplete")
    for column in range(size):
        pivot = next((row for row in range(column, size) if matrix[row][column] != 0), None)
        if pivot is None:
            raise OperatorError("Linear system has no unique solution")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            matrix[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(matrix[row], matrix[column])
            ]
    solution = {name: _plain_number(matrix[index][-1]) for index, name in enumerate(variables)}
    return {**state, "linear_solution": solution}


def evaluate_linear_target(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    target = state.get("linear_target") or {}
    solution = state.get("linear_solution") or {}
    coefficients = target.get("coefficients") or {}
    value = _fraction(target.get("constant", 0))
    for name, coefficient in coefficients.items():
        if name not in solution:
            raise OperatorError(f"Target references unknown variable {name!r}")
        value += _fraction(coefficient) * _fraction(solution[name])
    return {**state, "linear_target_value": _plain_number(value)}


def check_linear_constraints(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    solution = state.get("linear_solution") or {}
    valid = True
    for equation in task.constraints:
        if equation.get("kind") != "linear_equation":
            continue
        total = sum(
            (
                _fraction(coefficient) * _fraction(solution.get(name, 0))
                for name, coefficient in (equation.get("coefficients") or {}).items()
            ),
            Fraction(0),
        )
        valid = valid and total == _fraction(equation.get("constant", 0))
    if not valid:
        raise OperatorError("Solved values do not satisfy every original equation")
    return {**state, "linear_constraints_valid": True}


def emit_linear_result(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    if not state.get("linear_constraints_valid"):
        raise OperatorError("Linear solution has not passed constraint checks")
    return {
        **state,
        "answer": {
            "values": dict(state.get("linear_solution") or {}),
            "target": state.get("linear_target_value"),
        },
    }


def define_variable(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    variables = sorted(
        {
            str(name).strip()
            for item in task.constraints
            for name in (item.get("coefficients") or {})
            if str(name).strip()
        }
    )
    if not variables or len(variables) > 16:
        raise OperatorError("Expected 1-16 bounded symbolic variables")
    return {**state, "variables": variables, "linear_variables": variables}


def create_equation(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    variables = list(state.get("variables") or [])
    if not variables:
        raise OperatorError("Variables must be defined before equations")
    equations = [item for item in task.constraints if item.get("kind") == "linear_equation"]
    targets = [item for item in task.constraints if item.get("kind") == "linear_target"]
    if len(equations) != len(variables) or len(targets) != 1:
        raise OperatorError("A square equation system and exactly one target are required")
    rows = [
        [
            *[
                _plain_number(_fraction((equation.get("coefficients") or {}).get(name, 0)))
                for name in variables
            ],
            _plain_number(_fraction(equation.get("constant", 0))),
        ]
        for equation in equations
    ]
    return {**state, "linear_rows": rows, "linear_target": dict(targets[0])}


def substitute(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    matrix = [[_fraction(value) for value in row] for row in state.get("linear_rows") or []]
    size = len(matrix)
    if not size or any(len(row) != size + 1 for row in matrix):
        raise OperatorError("Substitution requires a square augmented matrix")
    for column in range(size):
        pivot = next((row for row in range(column, size) if matrix[row][column]), None)
        if pivot is None:
            raise OperatorError("Linear system has no unique substitution pivot")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(column + 1, size):
            factor = matrix[row][column]
            matrix[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(matrix[row], matrix[column])
            ]
    return {
        **state,
        "linear_echelon": [[_plain_number(value) for value in row] for row in matrix],
    }


def isolate_variable(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    variables = list(state.get("variables") or [])
    matrix = [[_fraction(value) for value in row] for row in state.get("linear_echelon") or []]
    size = len(variables)
    if len(matrix) != size:
        raise OperatorError("Isolation requires an echelon row for every variable")
    solution = [Fraction(0) for _ in range(size)]
    for row in range(size - 1, -1, -1):
        rhs = matrix[row][-1] - sum(
            (matrix[row][column] * solution[column] for column in range(row + 1, size)),
            Fraction(0),
        )
        if matrix[row][row] == 0:
            raise OperatorError("Linear system has no unique isolated solution")
        solution[row] = rhs / matrix[row][row]
    return {
        **state,
        "linear_solution": {
            name: _plain_number(solution[index]) for index, name in enumerate(variables)
        },
    }


def evaluate_expression(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    evaluated = evaluate_linear_target(state, task)
    return {**evaluated, "target_value": evaluated["linear_target_value"]}


def verify_solution(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    checked = check_linear_constraints(state, task)
    if "target_value" not in checked:
        raise OperatorError("The requested target has not been evaluated")
    return {**checked, "verified": True}


def create_relation(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    collected = collect_partial_order(state, task)
    return {**collected, "relations": list(collected["edges"])}


def topological_order(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    ordered = topological_linearize(state, task)
    return {**ordered, "verified": bool(ordered.get("order_valid"))}


def _enumerate_truth_lie_cases(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    destinations = next(
        (item for item in task.constraints if item.get("kind") == "binary_destination"),
        None,
    )
    unknown = next(
        (item for item in task.constraints if item.get("kind") == "unknown_responder_home"),
        None,
    )
    rules = {
        str(item.get("home") or ""): item.get("truthful")
        for item in task.constraints
        if item.get("kind") == "responder_rule"
    }
    if not destinations or not unknown:
        raise OperatorError("Truth/lie navigation premises are incomplete")
    target = str(destinations.get("target") or "").strip()
    alternative = str(destinations.get("alternative") or "").strip()
    options = [str(item).strip() for item in (unknown.get("options") or [])]
    if (
        not target
        or not alternative
        or target == alternative
        or set(options) != {target, alternative}
        or rules.get(target) is not True
        or rules.get(alternative) is not False
    ):
        raise OperatorError("Truth/lie navigation premises are inconsistent")
    return {
        **state,
        "assignments": [
            {"responder_home": home, "truthful": bool(rules[home])} for home in options
        ],
        "target_destination": target,
        "alternative_destination": alternative,
        "question_candidates": [
            {
                "question_id": "ask_home_road",
                "question": "Which road leads to the city where you live?",
                "follow": "take_indicated_road",
            }
        ],
    }


def _evaluate_truth_lie_questions(state: dict[str, Any]) -> dict[str, Any]:
    target = str(state.get("target_destination") or "")
    alternative = str(state.get("alternative_destination") or "")
    assignments = state.get("assignments") or []
    candidates = state.get("question_candidates") or []
    if not target or not alternative or len(assignments) != 2 or not candidates:
        raise OperatorError("Truth/lie cases were not enumerated")
    for candidate in candidates:
        if candidate.get("question_id") != "ask_home_road":
            continue
        case_results = []
        for assignment in assignments:
            home = str(assignment.get("responder_home") or "")
            truthful = bool(assignment.get("truthful"))
            indicated = home if truthful else (alternative if home == target else target)
            case_results.append(
                {
                    "responder_home": home,
                    "truthful": truthful,
                    "indicated_destination": indicated,
                    "satisfied": indicated == target,
                }
            )
        if case_results and all(item["satisfied"] for item in case_results):
            return {
                **state,
                "question": dict(candidate),
                "case_results": case_results,
                "entailed": True,
                "entailment_valid": True,
            }
    return {**state, "case_results": [], "entailed": False, "entailment_valid": True}


def enumerate_cases(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    if task.goal == "identify_truth_road":
        return _enumerate_truth_lie_cases(state, task)
    return enumerate_unknown_assignments(state, task)


def test_entailment(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    if task.goal == "identify_truth_road":
        return _evaluate_truth_lie_questions(state)
    return evaluate_existential_relation(state, task)


def verify_all_cases(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    cases = state.get("case_results")
    if not isinstance(cases, list) or not cases or "entailed" not in state:
        raise OperatorError("Case verification requires evaluated bounded cases")
    if task.goal == "identify_truth_road" and not state.get("entailed"):
        raise OperatorError("No question identifies the target road in every responder case")
    return {**state, "verified": True, "entailment_valid": True}


def stop_if_verified(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    if not state.get("verified"):
        raise OperatorError("Cannot stop before deterministic verification")
    if "linear_solution" in state and "target_value" in state:
        answer: Any = {
            "values": dict(state["linear_solution"]),
            "target": state["target_value"],
        }
    elif isinstance(state.get("ordered"), list):
        answer = list(state["ordered"])
    elif isinstance(state.get("question"), dict):
        answer = dict(state["question"])
    elif "entailed" in state:
        answer = bool(state["entailed"])
    else:
        raise OperatorError("Verified state has no bounded result to emit")
    return {**state, "answer": answer}


def _counts(task: StructuralTask) -> tuple[list[int], list[int], bool]:
    before: list[int] = []
    after: list[int] = []
    middle = False
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "exists_count_before":
            before.append(int(constraint["count"]))
        elif kind == "exists_count_after":
            after.append(int(constraint["count"]))
        elif kind == "exists_middle":
            middle = True
    return before, after, middle


def sum_constraint_mentions(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    return {**state, "scalar": sum(before) + sum(after) + int(middle)}


def max_constraint_only(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    values = before + after + ([1] if middle else [])
    return {**state, "scalar": max(values or [0])}


def infer_minimum_line_bound(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    before, after, middle = _counts(task)
    lower = max([1, *[value + 1 for value in before], *[value + 1 for value in after]])
    if middle:
        lower = max(lower, 3)
    return {**state, "lower_bound": lower}


def construct_linear_model(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    return {**state, "model": list(range(int(state.get("lower_bound", 1))))}


def _model_satisfies(task: StructuralTask, count: int) -> bool:
    if count <= 0:
        return False
    positions = range(count)
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "exists_count_before":
            if not any(position == int(constraint["count"]) for position in positions):
                return False
        elif kind == "exists_count_after":
            if not any(count - 1 - position == int(constraint["count"]) for position in positions):
                return False
        elif kind == "exists_middle":
            if count < 3 or count % 2 == 0:
                return False
        else:
            return False
    return True


def repair_until_valid(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    count = max(1, len(list(state.get("model") or [])))
    for _ in range(64):
        if _model_satisfies(task, count):
            return {**state, "model": list(range(count)), "model_valid": True}
        count += 1
    raise OperatorError("No valid bounded linear model was found")


def emit_scalar(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    return {**state, "answer": state.get("scalar")}


def emit_model_cardinality(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    model = state.get("model")
    if not isinstance(model, list):
        raise OperatorError("No structural model exists to count")
    return {**state, "answer": len(model)}


def evaluate_bounded_expression(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    expressions = [
        item.get("expression") for item in task.constraints if item.get("kind") == "expression"
    ]
    if len(expressions) != 1 or not isinstance(expressions[0], str):
        raise OperatorError("Exactly one arithmetic expression is required")
    expression = expressions[0]
    if len(expression) > 160:
        raise OperatorError("Expression exceeds the processor budget")
    tree = ast.parse(expression, mode="eval")
    binary: dict[type[ast.operator], Callable[[float, float], float]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            value = binary[type(node.op)](evaluate(node.left), evaluate(node.right))
            if not math.isfinite(value) or abs(value) > 1e15:
                raise OperatorError("Arithmetic result exceeds the processor budget")
            return value
        raise OperatorError("Unsupported arithmetic syntax")

    value = evaluate(tree)
    answer: int | float = int(value) if value.is_integer() else value
    return {**state, "answer": answer, "expression_valid": True}


def select_grounded_candidates(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    selected: list[tuple[float, float, int]] = []
    limits: list[int] = []
    for constraint in task.constraints:
        if constraint.get("kind") != "grounded_candidate":
            raise OperatorError("Unsupported grounded-evidence constraint")
        retrieval = float(constraint.get("retrieval_score", 0.0))
        confidence = float(constraint.get("confidence", 0.0))
        threshold = float(constraint.get("selection_threshold", 0.45))
        limits.append(max(1, min(8, int(constraint.get("selection_limit", 4)))))
        if retrieval >= threshold and confidence >= 0.45:
            selected.append((retrieval, confidence, int(constraint["index"])))
    selected.sort(key=lambda item: (-item[0], -item[1], item[2]))
    limit = min(limits or [4])
    return {
        **state,
        "answer": [index for _retrieval, _confidence, index in selected[:limit]],
        "selection_valid": True,
    }


def collect_partial_order(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for constraint in task.constraints:
        if constraint.get("kind") != "precedes":
            raise OperatorError("Unsupported partial-order constraint")
        before = str(constraint.get("before") or "").strip()
        after = str(constraint.get("after") or "").strip()
        if not before or not after or before == after:
            raise OperatorError("Invalid partial-order edge")
        nodes.update((before, after))
        edges.add((before, after))
    if not edges or len(nodes) > 128 or len(edges) > 512:
        raise OperatorError("Partial-order task exceeds the processor budget")
    return {**state, "nodes": sorted(nodes), "edges": sorted(edges)}


def topological_linearize(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    nodes = [str(item) for item in state.get("nodes") or []]
    edges = [(str(left), str(right)) for left, right in state.get("edges") or []]
    incoming = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for before, after in edges:
        incoming[after] += 1
        outgoing[before].append(after)
    ready = [node for node, degree in incoming.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        ordered.append(node)
        for successor in sorted(outgoing[node]):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, successor)
    if len(ordered) != len(nodes):
        raise OperatorError("Partial-order constraints contain a cycle")
    return {**state, "ordered": ordered, "order_valid": True}


def emit_order(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    ordered = state.get("ordered")
    if not isinstance(ordered, list):
        raise OperatorError("No partial-order model exists")
    return {**state, "answer": list(ordered)}


def enumerate_unknown_assignments(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    facts: dict[tuple[str, str], bool | None] = {}
    relations: list[dict[str, str]] = []
    query: dict[str, Any] | None = None
    for constraint in task.constraints:
        kind = constraint.get("kind")
        if kind == "entity_property":
            entity = str(constraint.get("entity") or "").strip()
            property_name = str(constraint.get("property") or "").strip()
            value = constraint.get("value")
            if not entity or not property_name or value not in {True, False, None}:
                raise OperatorError("Invalid entity-property premise")
            facts[(entity, property_name)] = value
        elif kind == "directed_relation":
            relation = {
                "subject": str(constraint.get("subject") or "").strip(),
                "predicate": str(constraint.get("predicate") or "").strip(),
                "object": str(constraint.get("object") or "").strip(),
            }
            if not all(relation.values()):
                raise OperatorError("Invalid directed-relation premise")
            relations.append(relation)
        elif kind == "exists_relation_by_property":
            query = dict(constraint)
        else:
            raise OperatorError("Unsupported Boolean case-analysis constraint")
    unknowns = sorted(key for key, value in facts.items() if value is None)
    if len(unknowns) > 10:
        raise OperatorError("Unknown assignment space exceeds the processor budget")
    assignments: list[dict[str, bool]] = []
    for values in itertools.product((False, True), repeat=len(unknowns)):
        assignment = {
            f"{entity}|{property_name}": bool(value)
            for (entity, property_name), value in facts.items()
            if value is not None
        }
        assignment.update(
            {
                f"{entity}|{property_name}": bool(value)
                for (entity, property_name), value in zip(unknowns, values)
            }
        )
        assignments.append(assignment)
    if query is None or not relations or not assignments:
        raise OperatorError("Boolean case-analysis task is incomplete")
    return {
        **state,
        "assignments": assignments,
        "unknowns": [f"{entity}|{property_name}" for entity, property_name in unknowns],
        "relations": relations,
        "query": query,
    }


def evaluate_existential_relation(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    query = state.get("query") or {}
    predicate = str(query.get("predicate") or "")
    subject_property = str(query.get("subject_property") or "")
    object_property = str(query.get("object_property") or "")
    subject_value = bool(query.get("subject_value"))
    object_value = bool(query.get("object_value"))
    case_results: list[dict[str, Any]] = []
    for assignment in state.get("assignments") or []:
        witnesses: list[dict[str, str]] = []
        for relation in state.get("relations") or []:
            if relation.get("predicate") != predicate:
                continue
            subject = str(relation.get("subject") or "")
            obj = str(relation.get("object") or "")
            if (
                assignment.get(f"{subject}|{subject_property}") is subject_value
                and assignment.get(f"{obj}|{object_property}") is object_value
            ):
                witnesses.append({"subject": subject, "object": obj})
        case_results.append(
            {
                "assignment": dict(assignment),
                "satisfied": bool(witnesses),
                "witnesses": witnesses,
            }
        )
    return {
        **state,
        "case_results": case_results,
        "entailed": bool(case_results) and all(item["satisfied"] for item in case_results),
        "entailment_valid": True,
    }


def emit_entailment(state: dict[str, Any], task: StructuralTask) -> dict[str, Any]:
    del task
    if "entailed" not in state:
        raise OperatorError("No Boolean entailment model exists")
    return {**state, "answer": bool(state["entailed"])}


OPERATORS = {
    "SUM_CONSTRAINT_MENTIONS": sum_constraint_mentions,
    "MAX_CONSTRAINT_ONLY": max_constraint_only,
    "INFER_MINIMUM_LINE_BOUND": infer_minimum_line_bound,
    "CONSTRUCT_LINEAR_MODEL": construct_linear_model,
    "REPAIR_UNTIL_VALID": repair_until_valid,
    "EMIT_SCALAR": emit_scalar,
    "EMIT_MODEL_CARDINALITY": emit_model_cardinality,
    "EVALUATE_BOUNDED_EXPRESSION": evaluate_bounded_expression,
    "SELECT_GROUNDED_CANDIDATES": select_grounded_candidates,
    "COLLECT_PARTIAL_ORDER": collect_partial_order,
    "TOPOLOGICAL_LINEARIZE": topological_linearize,
    "EMIT_ORDER": emit_order,
    "ENUMERATE_UNKNOWN_ASSIGNMENTS": enumerate_unknown_assignments,
    "EVALUATE_EXISTENTIAL_RELATION": evaluate_existential_relation,
    "EMIT_ENTAILMENT": emit_entailment,
    "DEFINE_LINEAR_SYSTEM": define_linear_system,
    "SOLVE_LINEAR_SYSTEM": solve_linear_system,
    "EVALUATE_LINEAR_TARGET": evaluate_linear_target,
    "CHECK_LINEAR_CONSTRAINTS": check_linear_constraints,
    "EMIT_LINEAR_RESULT": emit_linear_result,
    "DEFINE_VARIABLE": define_variable,
    "CREATE_EQUATION": create_equation,
    "SUBSTITUTE": substitute,
    "ISOLATE_VARIABLE": isolate_variable,
    "EVALUATE_EXPRESSION": evaluate_expression,
    "VERIFY_SOLUTION": verify_solution,
    "SUBSTITUTE_AND_VERIFY": verify_solution,
    "CHECK_CONSTRAINTS": verify_solution,
    "CREATE_RELATION": create_relation,
    "TOPOLOGICAL_ORDER": topological_order,
    "ENUMERATE_CASES": enumerate_cases,
    "TEST_ENTAILMENT": test_entailment,
    "VERIFY_ALL_CASES": verify_all_cases,
    "STOP_IF_VERIFIED": stop_if_verified,
}
