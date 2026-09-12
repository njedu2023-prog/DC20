"""Keep oversized HTTP fixtures out of verbose pytest node names.

Read syntax only: never evaluate the 4 MB expressions or launch nested pytest.
The original oversized inputs and rejection assertions remain in their tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "work/profit_1000_upgrade"
SHORT_ID = "oversized-4mb"
CASES = (
    ("candidate_scope", "test_raw_bounded_strict_json_required"),
    ("detail_probe", "test_invalid_original_response_never_retains_detail_or_claims_source"),
    ("diagnostic", "test_bounded_strict_json_failures_are_fixed_enums"),
    ("envelope_probe", "test_oversized_or_non_original_response_has_no_false_whole_body_digest"),
    ("pagination_probe", "test_invalid_original_response_has_no_parsed_pagination_or_source_claim"),
    ("single_stock_probe", "test_malformed_duplicate_nonfinite_or_oversized_response_not_parsed"),
    ("single_stock_probe", "test_official_transport_bounded_body_without_retry"),
    ("stocks_scope", "test_bounded_original_http_bytes_only"),
)


def _shape(node):
    return ast.dump(node, include_attributes=False)


def _expected_payload(module):
    expression = {
        "diagnostic": 'b"x" * (diag.MAX_BYTES + 1)',
        "stocks_scope": 'b" " * 4_000_001',
    }.get(module, 'b"x" * 4_000_001')
    return _shape(ast.parse(expression, mode="eval").body)


def _oversized_case(tree, module, function):
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
    assert len(functions) == 1, "the registered rejection test must remain present exactly once"
    decorators = [node for node in functions[0].decorator_list
                  if isinstance(node, ast.Call) and ast.unparse(node.func) == "pytest.mark.parametrize"]
    assert len(decorators) == 1
    assert len(decorators[0].args) >= 2 and isinstance(decorators[0].args[1], ast.List)
    expected = _expected_payload(module)
    matches = [item for item in decorators[0].args[1].elts
               if any(_shape(node) == expected for node in ast.walk(item))]
    assert len(matches) == 1, "retain exactly one original oversized input; do not remove or shrink it"
    return matches[0]


def _assert_short_id(case, module):
    assert isinstance(case, ast.Call) and ast.unparse(case.func) == "pytest.param", "oversized input needs an explicit short ID"
    assert _shape(case.args[0]) == _expected_payload(module), "the oversized payload must remain unchanged"
    if module == "diagnostic":
        assert len(case.args) == 2
        assert isinstance(case.args[1], ast.Constant) and case.args[1].value == "RESPONSE_EXCEEDS_4MB"
    else:
        assert len(case.args) == 1
    assert len(case.keywords) == 1 and case.keywords[0].arg == "id", "do not skip or mark away the rejection case"
    value = case.keywords[0].value
    assert isinstance(value, ast.Constant) and value.value == SHORT_ID, "ID must be the fixed short label, not raw payload text"


@pytest.mark.parametrize("module,function", CASES, ids=[f"{module}-{index + 1}" for index, (module, _) in enumerate(CASES)])
def test_oversized_http_fixture_has_short_stable_node_id(module, function):
    tree = ast.parse((RESEARCH / f"test_auction_{module}.py").read_text(encoding="utf-8"))
    _assert_short_id(_oversized_case(tree, module, function), module)


@pytest.mark.parametrize("regression", ["unwrapped", "missing-id", "payload-id", "skip-mark"])
def test_static_guard_rejects_log_id_regressions_without_evaluating_payload(regression):
    expressions = {
        "unwrapped": 'b"x" * 4_000_001',
        "missing-id": 'pytest.param(b"x" * 4_000_001)',
        "payload-id": 'pytest.param(b"x" * 4_000_001, id=b"x" * 4_000_001)',
        "skip-mark": 'pytest.param(b"x" * 4_000_001, id="oversized-4mb", marks=pytest.mark.skip)',
    }
    case = ast.parse(expressions[regression], mode="eval").body
    with pytest.raises(AssertionError):
        _assert_short_id(case, "candidate_scope")
