from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .config import AuctionV3Config


CSS = """
:root{color-scheme:light;--bg:#f4f6f8;--surface:#fff;--ink:#17202a;--muted:#66727f;--line:#dfe4e8;--green:#16794b;--red:#b42318;--amber:#9a6700;--blue:#165d9c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0}
.topbar{position:sticky;top:0;z-index:5;background:#17202a;color:#fff;border-bottom:1px solid #000;padding:14px 24px}.topbar h1{margin:0;font-size:20px}.topbar p{margin:4px 0 0;color:#c9d1d9;font-size:13px}
main{max-width:1500px;margin:0 auto;padding:22px}.status{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px}.badge{display:inline-flex;align-items:center;border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 10px;font-size:13px}.badge.good{color:var(--green);border-color:#9bd2b9}.badge.warn{color:var(--amber);border-color:#e3c276}.badge.bad{color:var(--red);border-color:#e7a29d}
.metrics{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:10px;margin:0 0 20px}.metric{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:13px;min-height:82px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;margin-top:8px;font-size:21px;overflow-wrap:anywhere}
section{background:var(--surface);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:0 0 20px;padding:18px 0}section h2{font-size:18px;margin:0 18px 14px}.buy-list-section h2{font-size:12px}.note{margin:0 18px 14px;color:var(--muted);line-height:1.65;font-size:13px}.empty-state{margin:0 18px;padding:18px;border:1px solid #e7a29d;background:#fff8f7;border-radius:6px;font-size:12px}.empty-state strong{display:block;color:var(--red);font-size:12px}.empty-state p{margin:8px 0 0;color:var(--muted);font-size:12px;line-height:1.65}.table-wrap{overflow:auto;border-top:1px solid var(--line)}table{border-collapse:collapse;width:100%;min-width:1050px;font-size:13px}th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th{position:sticky;top:0;background:#f7f8fa;color:#4b5563;font-weight:650;z-index:2}th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){text-align:left}tr:hover td{background:#fafbfd}.buy{color:var(--green);font-weight:700}.reject{color:var(--red)}.pending{color:var(--amber)}
.chart{margin:0 18px;border:1px solid var(--line);background:#fff;padding:8px;min-height:230px}.chart svg{width:100%;height:210px;display:block}.chart .axis{stroke:#dfe4e8;stroke-width:1}.chart .line{fill:none;stroke:var(--blue);stroke-width:2}.footer{color:var(--muted);font-size:12px;padding:2px 0 30px;line-height:1.7}
@media(max-width:900px){main{padding:12px}.topbar{padding:12px}.metrics{grid-template-columns:repeat(2,minmax(120px,1fr))}.metric strong{font-size:18px}}
"""


def _esc(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    return html.escape(str(value))


def _num(value: Any) -> float:
    try:
        result = float(value)
    except Exception:
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _pct(value: Any, digits: int = 2) -> str:
    number = _num(value)
    return "-" if not math.isfinite(number) else f"{number * 100:.{digits}f}%"


def _price(value: Any) -> str:
    number = _num(value)
    return "-" if not math.isfinite(number) else f"{number:.2f}"


def _float(value: Any, digits: int = 3) -> str:
    number = _num(value)
    return "-" if not math.isfinite(number) else f"{number:.{digits}f}"


def _metric(label: str, value: str) -> str:
    return f'<div class="metric"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></div>'


def _page(title: str, subtitle: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{CSS}</style></head>
<body><header class="topbar"><h1>{_esc(title)}</h1><p>{_esc(subtitle)}</p></header><main>{body}
<div class="footer">本页仅供人工决策参考，不连接券商、不执行订单、不构成收益保证。正式状态只由严格样本外门槛决定；影子状态不得用于买入。</div>
</main></body></html>"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _current_table(frame: pd.DataFrame) -> str:
    headers = ["操作", "观察排名", "交易排名", "代码", "股票", "行业板块", "晋级", "连板路径", "路径变化", "路径依据", "同梯队", "原排名", "前收", "竞价上限价", "T+1退出", "晋级涨停概率", "一层预计净收益", "二筛条件净收益", "二筛成交概率", "二筛大跌概率", "价格动作"]
    rows: list[str] = []
    for _, row in frame.iterrows():
        action = str(row.get("action", ""))
        css = "buy" if "BUY" in action else "reject" if action == "REJECT" else "pending"
        values = [
            f'<span class="{css}">{_esc(action)}</span>',
            _float(row.get("observation_rank"), 0),
            _float(row.get("trade_rank"), 0),
            _esc(row.get("ts_code")),
            _esc(row.get("name")),
            _esc(row.get("industry")),
            _esc(row.get("stage")),
            _esc(row.get("path_label")),
            _pct(row.get("path_strength_delta")),
            _esc(row.get("path_explanation")),
            _float(row.get("stage_pool_size"), 0),
            _float(row.get("source_rank"), 0),
            _price(row.get("d_close")),
            _price(row.get("recommended_max_price")),
            _esc(row.get("latest_exit_time") or "09:30"),
            _pct(row.get("predicted_continuation_limit_up_probability")),
            _pct(row.get("predicted_net_return")),
            _pct(row.get("trade_predicted_conditional_net_return")),
            _pct(row.get("trade_predicted_fill_probability")),
            _pct(row.get("trade_predicted_big_loss_probability")),
            _esc(row.get("price_action")),
        ]
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    return '<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{_esc(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def current_report(prediction: pd.DataFrame, backtest: dict[str, Any]) -> str:
    signal_date = str(prediction.get("signal_date", pd.Series([""])).iloc[0]) if not prediction.empty else ""
    promoted = bool(int(_num(prediction.get("model_promoted", pd.Series([0])).iloc[0]) or 0)) if not prediction.empty else False
    actions = prediction.get("action", pd.Series(index=prediction.index, dtype=str)).astype(str)
    formal_buys = prediction[actions.eq("BUY")]
    status_class = "good" if promoted else "warn"
    status_text = "正式模型" if promoted else "影子验证"
    sentiment_row = prediction.iloc[0] if not prediction.empty else pd.Series(dtype=object)
    selector = backtest.get("trade_selector", {}) or {}
    selector_buyable = (
        (selector.get("formal_policy_oos", {}) or {}).get(
            "market_buyable_only",
            {},
        )
        or {}
    )
    body = f'<div class="status"><span class="badge {status_class}">{status_text}</span><span class="badge">信号日 {signal_date}</span><span class="badge">模型 {_esc(prediction.get("model_version", pd.Series(["-"])).iloc[0] if not prediction.empty else "-")}</span></div>'
    body += '<div class="metrics">'
    body += _metric("候选数", str(len(prediction)))
    body += _metric("正式买入数", str(len(formal_buys)))
    body += _metric("二筛样本外交易日", str(selector.get("oos_dates", 0)))
    body += _metric("二筛现实可买净收益", _pct(selector_buyable.get("mean_trade_net_return")))
    body += _metric("二筛现实可买胜率", _pct(selector_buyable.get("win_rate")))
    body += _metric("二筛现实可买大跌率", _pct(selector_buyable.get("realized_big_loss_rate")))
    body += _metric("2进3/3进4命中率", _pct(backtest.get("stage_focus_continuation_hit_rate")))
    body += "</div>"
    body += '<section><h2>D日市场情绪量化</h2><p class="note">仅使用D日及更早的10%涨跌幅机制A股数据；综合分用于解释，原始因子是否进入连板模型由留出期消融决定。</p><div class="metrics">'
    sentiment_score = _num(sentiment_row.get("market_sentiment_score"))
    body += _metric(
        "情绪状态",
        f"{_esc(sentiment_row.get('market_sentiment_regime_label'))} · {sentiment_score * 100:.1f}分"
        if math.isfinite(sentiment_score)
        else "-",
    )
    body += _metric("较前一交易日", _pct(sentiment_row.get("market_sentiment_delta")))
    body += _metric("涨停 / 跌停", f"{_float(sentiment_row.get('market_limit_up_count'), 0)} / {_float(sentiment_row.get('market_limit_down_count'), 0)}")
    body += _metric("炸板率", _pct(sentiment_row.get("market_failed_limit_up_rate")))
    body += _metric("昨日涨停平均涨跌", _pct(sentiment_row.get("market_prev_limit_up_mean_return")))
    body += _metric("2进3真实晋级", _pct(sentiment_row.get("market_2_to_3_promotion_rate")))
    body += _metric("3进4真实晋级", _pct(sentiment_row.get("market_3_to_4_promotion_rate")))
    body += _metric("成交额/5日均值", _float(sentiment_row.get("market_amount_ratio_5d"), 2))
    body += "</div></section>"
    failures = backtest.get("promotion_failures", []) or []
    fail_text = "、".join(str(x) for x in failures) if failures else "全部通过"
    body += '<section class="buy-list-section"><h2>正式买入名单</h2>'
    if formal_buys.empty:
        body += '<div class="empty-state"><strong>今日无正式买入标的</strong><p>只有操作为 BUY 的股票才属于买入名单。REJECT 表示放弃，SHADOW_ONLY 仅用于研究验证。</p></div>'
    else:
        body += '<p class="note">以下仅列操作为 BUY 的人工参考标的。只使用限价单；竞价价格不得超过冻结上限，超过或未成交均放弃。</p>'
        body += _current_table(formal_buys)
    body += "</section>"
    body += '<section><h2>候选复核记录（非买入名单）</h2><p class="note">下表只来自D日已涨停候选池，2进3、3进4在全部风险门禁通过后优先。模型未晋级时，SHADOW_ONLY只用于验证。当前未通过项目：' + _esc(fail_text) + "。</p>"
    body += _current_table(prediction.head(20)) + "</section>"
    return _page("Decision 竞价人工指导 V12", "观察Top10与独立交易二筛；T日人工限价竞价，T+1日9:30退出", body)


def _verification_table(frame: pd.DataFrame) -> str:
    headers = ["信号日", "代码", "股票", "预测操作", "建议上限", "实际买价", "实际卖价", "预计净收益", "实际净收益", "真值来源", "状态", "预测结果"]
    rows: list[str] = []
    for _, row in frame.iterrows():
        status = str(row.get("verification_status", ""))
        trade_success = _num(row.get("trade_success"))
        verdict = "待验证"
        css = "pending"
        if status == "VERIFIED":
            verdict = "成功" if trade_success == 1 else "失败"
            css = "buy" if trade_success == 1 else "reject"
        elif status in ("NO_FILL", "COUNTERFACTUAL_READY"):
            verdict = "未成交"
        values = [
            _esc(row.get("signal_date")),
            _esc(row.get("ts_code")),
            _esc(row.get("name")),
            _esc(row.get("action")),
            _price(row.get("recommended_max_price")),
            _price(row.get("actual_buy_price")),
            _price(row.get("actual_exit_price")),
            _pct(row.get("predicted_net_return")),
            _pct(row.get("actual_net_return")),
            _esc(row.get("truth_source")),
            _esc(status),
            f'<span class="{css}">{_esc(verdict)}</span>',
        ]
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    return '<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{_esc(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"


def verification_report(ledger: pd.DataFrame, cumulative: dict[str, Any]) -> str:
    body = '<div class="status"><span class="badge">逐笔冻结预测与真价对照</span><span class="badge">人工成交回填与公开行情模拟分开累计</span></div>'
    body += '<div class="metrics">'
    body += _metric("冻结预测", str(cumulative.get("frozen_predictions", 0)))
    body += _metric("已验证交易", str(cumulative.get("verified_trades", 0)))
    body += _metric("成交率", _pct(cumulative.get("fill_rate")))
    body += _metric("价格指导准确率", _pct(cumulative.get("price_guidance_accuracy")))
    body += _metric("实际胜率", _pct(cumulative.get("win_rate")))
    body += _metric("大跌规避率", _pct(cumulative.get("big_loss_avoidance_rate")))
    body += "</div>"
    body += '<section><h2>每笔人工参考的累计验证</h2><p class="note">公开行情只生成模拟真值；人工实际买卖价格可通过手工反馈文件回填。未成交不计作零收益，一字跌停顺延到首次可交易日。</p>'
    latest = ledger.sort_values(["signal_date", "source_rank"], ascending=[False, True]).head(300) if not ledger.empty else ledger
    body += _verification_table(latest) + "</section>"
    return _page("Decision V12 逐笔验证", "冻结竞价建议价、观察Top10与交易二筛、真实竞价与T+1日9:30退出真值分账追溯", body)


def _equity_chart(points: Iterable[dict[str, Any]]) -> str:
    data = [p for p in points if _num(p.get("nav")) > 0]
    if len(data) < 2:
        return '<div class="chart"><p class="note">样本外净值数据尚不足。</p></div>'
    values = np.asarray([_num(p["nav"]) for p in data], dtype=float)
    vmin, vmax = float(values.min()), float(values.max())
    spread = max(vmax - vmin, 0.01)
    width, height, pad = 1000.0, 200.0, 18.0
    coords = []
    for i, value in enumerate(values):
        x = pad + i * (width - 2 * pad) / (len(values) - 1)
        y = height - pad - (value - vmin) * (height - 2 * pad) / spread
        coords.append(f"{x:.1f},{y:.1f}")
    return f'<div class="chart"><svg viewBox="0 0 1000 200" role="img" aria-label="样本外累计净值"><line class="axis" x1="18" y1="182" x2="982" y2="182"/><polyline class="line" points="{" ".join(coords)}"/></svg></div>'


def dashboard(backtest: dict[str, Any], cumulative: dict[str, Any]) -> str:
    promoted = backtest.get("promoted") is True
    failures = backtest.get("promotion_failures", []) or []
    body = f'<div class="status"><span class="badge {"good" if promoted else "warn"}">{"已通过晋级" if promoted else "未通过晋级"}</span><span class="badge">严格按日期滚动样本外</span><span class="badge">2倍成本压力测试</span></div>'
    body += '<div class="metrics">'
    body += _metric("历史独立日", str(backtest.get("history_dates", 0)))
    body += _metric("样本外交易日", str(backtest.get("oos_dates", 0)))
    body += _metric("累计收益", _pct(backtest.get("cumulative_return")))
    body += _metric("平均日收益", _pct(backtest.get("mean_daily_return"), 3))
    body += _metric("2倍成本日收益", _pct(backtest.get("stress_2x_cost_mean_daily_return"), 3))
    body += _metric("Bootstrap盈利概率", _pct(backtest.get("bootstrap_probability_mean_positive")))
    body += _metric("样本外大跌率", _pct(backtest.get("realized_big_loss_rate")))
    body += _metric("2进3/3进4命中率", _pct(backtest.get("stage_focus_continuation_hit_rate")))
    body += "</div>"
    top10 = backtest.get("top10_oos", {}) or {}
    top10_all = top10.get("all_candidates", {}) or {}
    top10_buyable = top10.get("market_buyable_only", {}) or {}
    body += '<section><h2>Top10样本外回测</h2>'
    body += '<p class="note">每日仅取2进3与3进4排序前10名；不足10只按实际数量统计，不使用其他股票补位。全部开盘计价用于排序诊断，现实可买子集用于接近可执行结果。</p>'
    top10_rows = []
    for label, item in (
        ("Top10全部开盘计价", top10_all),
        ("Top10现实可买", top10_buyable),
    ):
        top10_rows.append(
            "<tr>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(item.get('filled_trades', 0))}</td>"
            f"<td>{_esc(_pct(item.get('mean_trade_net_return')))}</td>"
            f"<td>{_esc(_pct(item.get('win_rate')))}</td>"
            f"<td>{_esc(_pct(item.get('continuation_hit_rate')))}</td>"
            f"<td>{_esc(_pct(item.get('tail_10pct_mean_return')))}</td>"
            f"<td>{_esc(_float(item.get('profit_factor'), 2))}</td>"
            "</tr>"
        )
    body += '<div class="table-wrap"><table><thead><tr><th>Top10口径</th><th>样本</th><th>平均净收益</th><th>胜率</th><th>晋级率</th><th>尾部10%均值</th><th>盈亏比</th></tr></thead><tbody>' + "".join(top10_rows) + "</tbody></table></div></section>"
    selector = backtest.get("trade_selector", {}) or {}
    selector_formal = selector.get("formal_policy_oos", {}) or {}
    body += '<section><h2>第二层交易排序严格样本外</h2><p class="note">只在每日观察Top10内二次排序；E_ret仅用真实可成交样本训练，P_fill独立建模。最多2只、允许0只，零交易或长期无交易不能晋级。</p>'
    selector_rows = []
    for label, item in (
        ("二筛全部开盘计价", selector_formal.get("all_candidates", {}) or {}),
        ("二筛现实可买", selector_formal.get("market_buyable_only", {}) or {}),
    ):
        selector_rows.append(
            "<tr>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(item.get('filled_trades', 0))}</td>"
            f"<td>{_esc(_pct(item.get('mean_trade_net_return')))}</td>"
            f"<td>{_esc(_pct(item.get('win_rate')))}</td>"
            f"<td>{_esc(_pct(item.get('tail_10pct_mean_return')))}</td>"
            f"<td>{_esc(_float(item.get('profit_factor'), 2))}</td>"
            "</tr>"
        )
    body += '<div class="table-wrap"><table><thead><tr><th>交易二筛口径</th><th>样本</th><th>平均净收益</th><th>胜率</th><th>尾部10%均值</th><th>盈亏比</th></tr></thead><tbody>' + "".join(selector_rows) + "</tbody></table></div></section>"
    all_focus = backtest.get("stage_focus_all", {}) or {}
    paths = backtest.get("path_shadow_policies", {}) or {}
    body += '<section><h2>全体2进3与3进4样本外回测</h2><div class="metrics">'
    body += _metric("全部样本", str(all_focus.get("filled_trades", 0)))
    body += _metric("全部平均净收益", _pct(all_focus.get("mean_trade_net_return")))
    body += _metric("全部胜率", _pct(all_focus.get("win_rate")))
    body += _metric("全部累计收益", _pct(all_focus.get("cumulative_return")))
    body += _metric("全部最大回撤", _pct(all_focus.get("max_drawdown")))
    body += _metric("全部尾部10%均值", _pct(all_focus.get("tail_10pct_mean_return")))
    body += "</div>"
    rows = []
    for code, label in (
        ("ACCELERATION_CONSENSUS", "加速一致"),
        ("WEAK_TO_STRONG", "弱转强"),
    ):
        item = paths.get(code, {}) or {}
        rows.append(
            "<tr>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(item.get('filled_trades', 0))}</td>"
            f"<td>{_esc(_pct(item.get('mean_trade_net_return')))}</td>"
            f"<td>{_esc(_pct(item.get('win_rate')))}</td>"
            f"<td>{_esc(_pct(item.get('cumulative_return')))}</td>"
            f"<td>{_esc(_pct(item.get('max_drawdown')))}</td>"
            "</tr>"
        )
    body += '<div class="table-wrap"><table><thead><tr><th>连板路径</th><th>样本</th><th>平均净收益</th><th>胜率</th><th>等权累计收益</th><th>最大回撤</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></section>"
    body += '<section><h2>样本外累计净值</h2>' + _equity_chart(backtest.get("daily_equity", []) or []) + "</section>"
    body += '<section><h2>模型晋级审计</h2><p class="note">当前未通过项目：' + _esc("、".join(str(x) for x in failures) if failures else "无") + "。任何一个硬门槛失败，系统都保持影子状态。</p>"
    checks = backtest.get("promotion_checks", {}) or {}
    rows = "".join(f'<tr><td>{_esc(name)}</td><td><span class="{"buy" if passed else "reject"}">{"通过" if passed else "未通过"}</span></td></tr>' for name, passed in checks.items())
    body += '<div class="table-wrap"><table><thead><tr><th>检查项</th><th>结果</th></tr></thead><tbody>' + rows + "</tbody></table></div></section>"
    body += '<section><h2>上线后累计真价验证</h2><div class="metrics">'
    body += _metric("已验证交易", str(cumulative.get("verified_trades", 0)))
    body += _metric("平均实际净收益", _pct(cumulative.get("mean_actual_net_return")))
    body += _metric("实际胜率", _pct(cumulative.get("win_rate")))
    body += _metric("预测MAE", _pct(cumulative.get("forecast_mae")))
    body += _metric("正确放弃率", _pct(cumulative.get("correct_rejection_rate")))
    body += _metric("机会遗漏率", _pct(cumulative.get("missed_opportunity_rate")))
    body += "</div></section>"
    return _page("Decision V12 回测与累计验证", "严格A股交易日内嵌套滚动样本外验证；观察Top10与独立交易二筛均按T+1日9:30退出分账统计", body)


def write_reports(
    config: AuctionV3Config,
    *,
    prediction: pd.DataFrame,
    ledger: pd.DataFrame,
    backtest_trades: pd.DataFrame,
    backtest_metrics: dict[str, Any],
    cumulative_metrics: dict[str, Any],
) -> dict[str, str]:
    del backtest_trades
    signal_date = str(prediction.get("signal_date", pd.Series([""])).iloc[0]) if not prediction.empty else "unknown"
    current = config.report_root / "auction_v3_latest.html"
    current_dated = config.report_root / f"auction_v3_{signal_date}.html"
    verification = config.report_root / "auction_v3_verify_latest.html"
    validation = config.report_root / "auction_v3_validation_dashboard.html"
    current_html = current_report(prediction, backtest_metrics)
    _write(current, current_html)
    _write(current_dated, current_html)
    _write(verification, verification_report(ledger, cumulative_metrics))
    _write(validation, dashboard(backtest_metrics, cumulative_metrics))
    return {"current": str(current), "verification": str(verification), "dashboard": str(validation)}
