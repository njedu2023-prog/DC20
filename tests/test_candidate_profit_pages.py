"""Exercise the real small Pages materializer with an empty, honest ledger."""
import hashlib
import json

import pytest

from scripts import candidate_profit_pages as m
from top10decision.decision import candidate_profit_publication as p
from top10decision.decision import candidate_formal_shadow_summary as s


@pytest.fixture(scope="module")
def payloads():
    config = p.load_activation()
    calendar = (p.ROOT / "data/market/trade_cal_sse.csv").read_bytes()
    summary = s.build_summary(config, [], [], as_of_date="20260911", calendar_raw=calendar,
        expected_calendar_sha256=s.statistics.labels.settlement.CALENDAR_SHA256)
    index = p.build_public_index([], activation=config, source_main_sha="a" * 40,
        generated_at_utc="2026-09-14T07:00:00+00:00")
    return {m.CONFIG: p.encoded(config), m.PREFIX + "index.json": p.encoded(index),
            m.PREFIX + "summary.json": p.encoded(summary)}


@pytest.fixture
def case(tmp_path, payloads):
    root, site = tmp_path / "repo", tmp_path / "site"
    root.mkdir(); site.mkdir()
    for relative, raw in payloads.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (site / "revision.json").write_text('{"source_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}')
    return root, site


def test_same_exact_config_index_and_summary_bound_to_revision(case, payloads):
    root, site = case
    result = m.materialize(root, site)
    assert result["enabled"] is True and result["days"] == 0
    revision = json.loads((site / "revision.json").read_bytes())
    assert revision["candidate_profit_activation"] == p.activation_identity(p.load_activation())
    for role in ("index", "summary"):
        relative = revision[f"candidate_profit_{role}_url"]
        assert (site / relative).read_bytes() == payloads[relative]
        assert revision[f"candidate_profit_{role}_sha256"] == hashlib.sha256(payloads[relative]).hexdigest()
    assert json.loads((site / (m.PREFIX + "summary.json")).read_bytes())["groups"]["candidate_top1"]["filled_proxy_win_rate"] is None


def test_invalid_summary_cannot_be_deployed(case):
    root, site = case
    path = root / (m.PREFIX + "summary.json")
    value = json.loads(path.read_bytes()); value["profitability_improvement_proven"] = True
    path.write_bytes(p.encoded(value))
    with pytest.raises(ValueError): m.materialize(root, site)


def test_wrong_model_config_cannot_be_deployed(case):
    root, site = case
    path = root / m.CONFIG
    value = json.loads(path.read_bytes()); value["model_canonical_sha256"] = "e" * 64
    path.write_bytes(p.encoded(value))
    with pytest.raises(ValueError): m.materialize(root, site)


def test_config_symlink_rejected(case):
    root, site = case
    source = root / m.CONFIG
    copy = source.with_name("original.json"); source.rename(copy); source.symlink_to(copy)
    with pytest.raises(ValueError, match="regular candidate"): m.materialize(root, site)


def test_disabled_config_with_old_enabled_index_is_not_silently_deployed(case):
    root, site = case
    source = root / m.CONFIG
    config = json.loads(source.read_bytes()); config["enabled"] = False
    source.write_bytes(p.encoded(config))
    with pytest.raises(ValueError, match="ACTIVATION_CHANGED"): m.materialize(root, site)


def test_public_verifier_requires_exact_activation_and_homepage(case):
    root, site = case
    m.materialize(root, site)
    for name in ("index.html", "decision.html"):
        (site / name).write_text("same verified homepage")
    result = m.verify_public(site, lambda relative: (site / relative).read_bytes())
    assert result["enabled"] is True and result["verified_files"] == 6


@pytest.mark.parametrize("changed", ["revision.json", "index.html", m.CONFIG,
    m.PREFIX + "index.json", m.PREFIX + "summary.json"])
def test_public_verifier_rejects_stale_bytes(case, changed):
    root, site = case
    m.materialize(root, site)
    for name in ("index.html", "decision.html"):
        (site / name).write_text("same verified homepage")
    def fetch(relative):
        return b"stale bytes" if relative == changed else (site / relative).read_bytes()
    with pytest.raises(ValueError, match="converged"):
        m.verify_public(site, fetch)
