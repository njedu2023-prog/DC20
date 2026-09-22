"""Unchanged native scorer to public day to ledger; synthetic proof only."""
from datetime import datetime, timezone
import json
import socket

from tests.test_candidate_eligible_public_day import make_case, TestProof
from top10decision.decision import candidate_formal_shadow_summary as ledger


def test_one_short_history_stock_does_not_erase_public_top2_or_ledger(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, size=10, missing=True)
    day = ledger.publication.project_verified_day(**case)
    frozen = json.loads(case['snapshot_raw'])
    assert [r['promotion_rank'] for r in day['original_rows']] == list(range(1, 11))
    assert day['candidate_count'] == 9
    excluded = {r['ts_code'] for r in day['eligibility'] if not r['eligible']}
    selected = day['candidate_slots']
    assert len(excluded) == 1 and len(selected) == 2
    assert not excluded.intersection(r['ts_code'] for r in selected)

    # The issuer is replaced explicitly: this must never count as natural
    # publication evidence. The summary still validates the real v2 snapshot.
    monkeypatch.setattr(ledger.statistics, '_publication_type', lambda: TestProof)
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: (_ for _ in ()).throw(AssertionError('NO_NETWORK')))
    calendar = ledger._validation_calendar()
    item = dict(signal_date=day['signal_date'], snapshot_raw=case['snapshot_raw'],
        expected_snapshot_sha256=ledger.sha(case['snapshot_raw']),
        ledger_raw=None, expected_ledger_sha256=None, ledger_as_of_date=None,
        source_collection_receipt_sha256=None, publication_proof=case['publication_proof'])
    result = ledger.build_summary(case['activation'], [day], [item],
        as_of_date=day['signal_date'], calendar_raw=calendar,
        expected_calendar_sha256=ledger.sha(calendar),
        clock=lambda: datetime(2026, 9, 14, 13, tzinfo=timezone.utc))
    assert result['test_only'] is True
    assert result['published_signal_dates'] == [day['signal_date']]
    assert result['missing_signal_dates'] == []
    assert result['expected_slot_count'] == 2
    for rank, group_name in enumerate(ledger.GROUPS, 1):
        row = result['groups'][group_name]['daily_sequence'][0]
        assert row['candidate_rank'] == row['slot'] == rank
        assert row['ts_code'] == selected[rank-1]['ts_code']
        assert row['status'] == 'PENDING_T_NOT_DUE'
        assert row['slot_net_return'] is None
    assert json.loads(case['snapshot_raw']) == frozen
