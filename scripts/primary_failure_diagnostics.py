"""Read-only failure explanation, deliberately outside every formal ranking index.

An identity audit is not a prediction. This module never scores, drops or reranks
stocks and never grants source, model, publication or natural-time admission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA = "dc20_primary_failure_diagnostic_v1"
RELATIVE = Path("outputs/decision/primary_diagnostics")
REASONS = {
    "RECOMPUTED_STAGE_MISMATCH": "上游连板数与本地重算不一致，可能影响整个候选池特征",
    "RUNTIME_AUGMENT_DROPPED_HARD_IDENTITY": "候选在特征构建中缺失，尚不能确认其他输入不受影响",
    "DUPLICATE_CURRENT_BASE_IDENTITY": "当前计算输入存在重复股票身份",
    "MISSING_EXACT_D_DAILY_ROW": "缺少当日行情记录",
    "MISSING_EXACT_D_STK_LIMIT_ROW": "缺少当日涨跌停价格记录",
    "INVALID_EXACT_D_DAILY_PRICES": "当日行情价格无效",
    "INVALID_EXACT_D_LIMIT_PRICES": "当日涨跌停价格无效",
    "INVALID_EXACT_D_CLOSE": "当日收盘价无效",
    "INVALID_EXACT_D_UP_LIMIT": "当日涨停价格无效",
    "EXACT_D_CLOSE_NOT_AT_UP_LIMIT": "当日收盘未达到核验涨停价格，与候选资格不一致",
    "INVALID_EXACT_D_MECHANISM_LIMIT": "当日涨跌停机制无法核验",
    "MECHANISM_LIMIT_ABOVE_PRIMARY_CAP": "股票涨跌停机制不符合当前候选范围",
    "UNEXPLAINED_CURRENT_BASE_EXCLUSION": "候选未进入计算输入，原因仍待核验",
}


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"20\d{6}", value):
        raise ValueError("diagnostic date must be YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def build_diagnostic(signal_date, *, audit_rows=None, stock_names=None,
                     source_bindings=None, run_id="", run_attempt="", generated_at_utc=None):
    """Project only typed identity checks; no raw errors, paths or feature values."""
    day = _day(signal_date)
    for value in (run_id, run_attempt):
        if not isinstance(value, str) or (value and not re.fullmatch(r"[1-9]\d*", value)):
            raise ValueError("invalid diagnostic run identity")
    names = stock_names or {}
    rows, seen = [], set()
    for row in audit_rows or []:
        if not isinstance(row, dict) or set(row) != {"ts_code", "declared_stage", "computed_stage", "status", "reason"}:
            raise ValueError("invalid identity audit surface")
        code, reason = row["ts_code"], row["reason"]
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or code in seen:
            raise ValueError("invalid or duplicated diagnostic stock")
        seen.add(code)
        if reason not in {"", *REASONS}:
            raise ValueError("unsupported identity diagnostic reason")
        if row["status"] != ("DROPPED" if reason else "INCLUDED"):
            raise ValueError("identity diagnostic status mismatch")
        declared, computed = row["declared_stage"], row["computed_stage"]
        if type(declared) is not int or declared not in (2, 3):
            raise ValueError("invalid declared hard stage")
        if computed is not None and (type(computed) is not int or not 0 <= computed <= 100):
            raise ValueError("invalid computed stage")
        if not reason and computed != declared:
            raise ValueError("passed identity audit has inconsistent stages")
        name = names.get(code, "")
        if not isinstance(name, str) or len(name) > 120:
            raise ValueError("invalid diagnostic stock name")
        rows.append({**row, "name": name,
                     "identity_stage_check": "BLOCKED" if reason else "PASSED_NOT_SCORED",
                     "reason_text": REASONS.get(reason, "身份与板数映射通过；不代表特征、评分或交易资格已通过")})
    rows.sort(key=lambda row: row["ts_code"])
    if audit_rows is not None and not any(row["reason"] for row in rows):
        raise ValueError("failure diagnostic needs at least one failed identity")
    bindings = dict(source_bindings or {})
    allowed = {"code_revision": 40, "candidate_sha256": 64, "market_metadata_sha256": 64,
               "calendar_sha256": 64}
    if set(bindings) - set(allowed):
        raise ValueError("unknown diagnostic binding")
    for key, value in bindings.items():
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{"+str(allowed[key])+"}", value):
            raise ValueError("invalid diagnostic binding")
    stamp = generated_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("diagnostic timestamp needs a timezone")
    body = {
        "schema_version": SCHEMA, "signal_date": day,
        "status": "BLOCKED_WITH_IDENTITY_DIAGNOSTICS" if audit_rows is not None else "BLOCKED_BEFORE_IDENTITY_AUDIT",
        "scope": "PRE_SCORE_DIAGNOSTIC_ONLY_NOT_FORMAL_RANKING",
        "formal_output_ready": False, "formal_ledger_admission": False,
        "calculation_resumed": False, "candidate_pool_modified": False,
        "generated_at_utc": stamp, "timestamp_scope": "DIAGNOSTIC_CREATION_NOT_PREDICTION_OR_FREEZE",
        "run_id": run_id, "run_attempt": run_attempt, "source_bindings": bindings,
        "original_candidate_count": len(rows) if audit_rows is not None else None,
        "identity_check_passed_count": sum(not row["reason"] for row in rows) if audit_rows is not None else None,
        "blocked_stock_count": sum(bool(row["reason"]) for row in rows) if audit_rows is not None else None,
        "rows": rows,
        "required_action": "核验候选与全池输入后，按原始证据和时间窗口接续；本文件不授予计算或发布资格",
    }
    return {**body, "diagnostic_sha256": hashlib.sha256(_raw(body)).hexdigest()}


def validate_diagnostic(value):
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise ValueError("invalid diagnostic schema")
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise ValueError("invalid diagnostic rows")
    identity_rows = None
    if value.get("status") == "BLOCKED_WITH_IDENTITY_DIAGNOSTICS":
        identity_rows = [{key: row.get(key) for key in ("ts_code", "declared_stage", "computed_stage", "status", "reason")} for row in rows]
    expected = build_diagnostic(value.get("signal_date"), audit_rows=identity_rows,
        stock_names={row.get("ts_code"): row.get("name") for row in rows},
        source_bindings=value.get("source_bindings"), run_id=value.get("run_id"),
        run_attempt=value.get("run_attempt"), generated_at_utc=value.get("generated_at_utc"))
    if expected != value:
        raise ValueError("diagnostic integrity or fixed boundaries changed")
    return value


def render_html(value):
    value = validate_diagnostic(value)
    escape = lambda x: html.escape(str(x), quote=True)
    day = value["signal_date"]
    title = f"D {day[:4]}-{day[4:6]}-{day[6:]} · 名单生成缺口"
    rows = "".join("<tr>" + "".join(f"<td>{escape(item)}</td>" for item in (
        row["ts_code"], row["name"], row["declared_stage"],
        row["computed_stage"] if row["computed_stage"] is not None else "待核验",
        "待核验" if row["reason"] else "身份/板数映射通过", row["reason_text"])) + "</tr>" for row in value["rows"])
    if not rows:
        rows = '<tr><td colspan="6">流程在逐股检查前停止，尚不能划定单股影响范围。具体失败步骤见对应运行日志。</td></tr>'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>body{{font:16px/1.6 system-ui,sans-serif;margin:0;background:#f3f7f5;color:#203c32}}main{{max-width:1100px;margin:32px auto;padding:24px;background:white;border:1px solid #dbe5df;border-radius:12px}}h1{{font-size:26px}}.notice{{padding:16px;background:#fff4dc;border-left:4px solid #a46e1e}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;margin-top:24px}}th,td{{text-align:left;border-bottom:1px solid #dbe5df;padding:12px}}th{{background:#eef5f1}}.muted{{color:#637a70;font-size:14px}}</style>
<main><h1>{escape(title)}</h1><p class="notice"><strong>故障诊断 · 尚未形成正式排名。</strong>保留原候选池，未删除股票重算。下表只说明身份与板数映射检查；通过此项不代表评分、特征或交易资格已通过。</p>
<p>原候选数：{escape(value['original_candidate_count'] if value['original_candidate_count'] is not None else '尚未核验')} · 身份映射通过：{escape(value['identity_check_passed_count'] if value['identity_check_passed_count'] is not None else '—')} · 待核验：{escape(value['blocked_stock_count'] if value['blocked_stock_count'] is not None else '—')}</p>
<div class="scroll"><table><thead><tr><th>代码</th><th>公司</th><th>上游板数</th><th>重算板数</th><th>检查状态</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>{escape(value['required_action'])}</p><p class="muted">诊断生成于 {escape(value['generated_at_utc'])} · 运行 {escape(value['run_id'] or '本地诊断')} / 尝试 {escape(value['run_attempt'] or '—')}。此时间不是预测或冻结时间；本页无正式名次，不进入业绩账本。</p></main></html>'''


def write_diagnostic(root, value):
    value = validate_diagnostic(value)
    root = Path(root).resolve()
    folder = root / RELATIVE
    for part in (folder, *folder.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("diagnostic directory cannot be a symlink")
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for suffix, raw in (("json", json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()+b"\n"),
                        ("html", render_html(value).encode())):
        path = folder / f"day_{value['signal_date']}.{suffix}"
        if path.is_symlink():
            raise ValueError("diagnostic output cannot be a symlink")
        fd, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=folder)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        outputs[suffix] = path
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-attempt", required=True)
    args = parser.parse_args()
    day = _day(args.signal_date)
    folder = Path(args.root)/RELATIVE
    value = validate_diagnostic(json.loads((folder/f"day_{day}.json").read_text()))
    if (value["run_id"], value["run_attempt"]) != (args.expected_run_id, args.expected_run_attempt):
        raise ValueError("refuse stale diagnostic from a different attempt")
    if (folder/f"day_{day}.html").read_bytes() != render_html(value).encode():
        raise ValueError("diagnostic HTML does not match the checked JSON")
    shown = lambda key: value[key] if value[key] is not None else "尚未核验"
    summary = (f"### D {day} 名单生成缺口\n\n正式计算未完成；没有删除问题股后重算。"
               f"原候选数：{shown('original_candidate_count')}；身份/板数映射通过：{shown('identity_check_passed_count')}；"
               f"待核验：{shown('blocked_stock_count')}。这不是正式排名。\n\n"
               "逐股 JSON 与可视化 HTML 见本次运行的 `primary-d-diagnostic` 附件。\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
