"""Reject stale events before reserving the writer; retain API/source guards."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]

def test_stale_upstream_is_isolated_and_cannot_compute():
    raw=(ROOT/'.github/workflows/run_primary_d_daily.yml').read_text()
    workflow=yaml.safe_load(raw)
    assert 'github.event.workflow_run.head_sha != github.sha ||' in workflow['concurrency']['group']
    assert "format('dc20-p0-ignored-{0}', github.run_id)" in workflow['concurrency']['group']
    assert workflow['concurrency']['cancel-in-progress'] is False
    gate=workflow['jobs']['compute']['if']
    assert "github.event_name != 'workflow_run' ||" in gate
    assert 'github.event.workflow_run.head_sha == github.sha &&' in gate
    assert "'head_sha': base_head" in raw
    assert 'if identity is None or supplied != expected:' in raw
    assert 'P0 workflow_run upstream API identity drifted' in raw
    assert 'P0 CAS publication missed the T 09:15 safety cutoff' in raw
