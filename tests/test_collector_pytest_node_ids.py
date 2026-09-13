"""Check collector node IDs using syntax only: no payload evaluation or pytest subprocess."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


RESEARCH = Path(__file__).resolve().parents[1] / "work/profit_1000_upgrade"
CASES = (
    ("collect_v3", "test_only_original_bounded_http_bytes_allowed", "raw", 5, 'b"x" * 4_000_001'),
    ("daily_gap_collect", "test_duplicate_nonfinite_and_oversize_json", "body", 6, 'b"x" * (c.MAX_BYTES + 1)'),
    ("minute_gap_collect", "test_transport_bound_and_bytes", "raw", 4, 'b"x" * 1_000_001'),
    ("minute_gap_collect", "test_malformed_response_fails_closed_without_sources", "raw", 7, 'b"x" * 1_000_001'),
    ("stocks_scope_collect", "test_transport_response_limit_is_checked_without_retry", "raw", 4, 'b"x" * 4_000_001'),
)


def _shape(node):
    return ast.dump(node, include_attributes=False)


def _assert_guarded_test(tree, function, parameter, count, payload):
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
    assert len(functions) == 1, "retain the registered rejection test exactly once"
    decorators = functions[0].decorator_list
    assert len(decorators) == 1, "do not add marks that skip the rejection test"
    decorator = decorators[0]
    assert isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "pytest.mark.parametrize"
    assert len(decorator.args) == 2 and not decorator.keywords
    assert isinstance(decorator.args[0], ast.Constant) and decorator.args[0].value == parameter
    values = decorator.args[1]
    assert isinstance(values, ast.List) and len(values.elts) == count, "retain the original case count"
    expected = _shape(ast.parse(payload, mode="eval").body)
    matches = [item for item in values.elts if any(_shape(node) == expected for node in ast.walk(item))]
    assert len(matches) == 1, "retain exactly one original oversized payload without shrinking it"
    case = matches[0]
    assert isinstance(case, ast.Call) and ast.unparse(case.func) == "pytest.param", "oversized input needs a short explicit ID"
    assert len(case.args) == 1 and _shape(case.args[0]) == expected, "payload expression must remain unchanged"
    assert len(case.keywords) == 1 and case.keywords[0].arg == "id", "do not skip or mark away this case"
    short_id = case.keywords[0].value
    assert isinstance(short_id, ast.Constant) and short_id.value == "oversized-bytes"


@pytest.mark.parametrize("module,function,parameter,count,payload", CASES,
                         ids=[f"{module}-{index + 1}" for index, (module, *_) in enumerate(CASES)])
def test_collector_oversized_payload_keeps_short_id_and_original_case(module, function, parameter, count, payload):
    tree = ast.parse((RESEARCH / f"test_{module}.py").read_text(encoding="utf-8"))
    _assert_guarded_test(tree, function, parameter, count, payload)


@pytest.mark.parametrize("regression", [
    "unwrapped", "missing-id", "payload-id", "skip-mark", "xfail-mark",
    "shrunk-payload", "changed-byte", "missing-case", "skip-test",
])
def test_static_guard_rejects_id_payload_count_and_skip_regressions(regression):
    expressions = {
        "unwrapped": 'b"x" * 4_000_001',
        "missing-id": 'pytest.param(b"x" * 4_000_001)',
        "payload-id": 'pytest.param(b"x" * 4_000_001, id=b"x" * 4_000_001)',
        "skip-mark": 'pytest.param(b"x" * 4_000_001, id="oversized-bytes", marks=pytest.mark.skip)',
        "xfail-mark": 'pytest.param(b"x" * 4_000_001, id="oversized-bytes", marks=pytest.mark.xfail)',
        "shrunk-payload": 'pytest.param(b"x" * 1_000_001, id="oversized-bytes")',
        "changed-byte": 'pytest.param(b"y" * 4_000_001, id="oversized-bytes")',
    }
    expression = expressions.get(regression, 'pytest.param(b"x" * 4_000_001, id="oversized-bytes")')
    values = expression if regression == "missing-case" else f"b'', {expression}"
    prefix = "@pytest.mark.skip\n" if regression == "skip-test" else ""
    tree = ast.parse(prefix + f"@pytest.mark.parametrize('raw', [{values}])\ndef rejection(raw):\n    pass\n")
    with pytest.raises(AssertionError):
        _assert_guarded_test(tree, "rejection", "raw", 2, 'b"x" * 4_000_001')
