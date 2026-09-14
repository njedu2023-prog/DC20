"""Synthetic orchestration checks; network and private-proof boundary are fake.

The original daily/native collector/journal path runs unchanged with TEST_ONLY
fixtures. Test proof and statistics adapters explicitly do not issue real
publication/source/outcome authority. No Tushare or GitHub request is made.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from work.profit_1000_upgrade import candidate_natural_settlement_workflow as m
from work.profit_1000_upgrade import test_candidate_natural_daily as daily_tests
from work.profit_1000_upgrade.test_candidate_natural_statistics import SyntheticPublicationProof


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def setup(tmp_path, monkeypatch, *, size=2):
    (tmp_path / "native").mkdir()
    source_case = daily_tests.setup(tmp_path / "native", monkeypatch, size=size)
    source = source_case["case"]
    modules = m.dependencies()
    issuer, gh = modules["evidence_publication"], modules["evidence_publication"].gh
    repo = tmp_path.resolve() / "checkout"; repo.mkdir()
    artifacts = tmp_path.resolve() / "artifacts"; artifacts.mkdir()
    context = {"repository":m.REPOSITORY,"branch":"main","run_id":10,"run_attempt":1,
        "code_head_sha":"a"*40,"workflow_path":m.WORKFLOW_PATH,"event_name":"schedule",
        "trigger_observer_run_id":None}
    tree = {}
    def add(relative, raw):
        write(repo / relative, raw)
        tree[relative] = {"type":"blob","mode":"100644","sha":gh.git_blob(raw),"size":len(raw)}
    add(str(modules["statistics"].labels.settlement.CALENDAR_PATH), Path(source["bundle"]["calendar"]["origin_path"]).read_bytes())
    snapshot_path = m.SNAPSHOT_PREFIX + "day_" + source["day"] + ".json"
    add(snapshot_path, source["snapshot"].read_bytes())
    proof = SyntheticPublicationProof(source["day"], source["snapshot_sha"])
    context_path = m.EVIDENCE_PREFIX + source["day"] + "/context.json"
    add(context_path, m.encoded({"signal_date":source["day"],"snapshot_file_sha256":source["snapshot_sha"],
        "observer_run_id":proof.observer_run_id,"manifest_sha256":proof.evidence_manifest_sha256}))
    registration = {"id":100,"state":"active","name":m.WORKFLOW_NAME,"path":m.WORKFLOW_PATH}
    run = {"id":10,"workflow_id":100,"name":m.WORKFLOW_NAME,"path":m.WORKFLOW_PATH,
        "head_sha":"a"*40,"head_branch":"main","run_attempt":1,"event":"schedule",
        "repository":{"full_name":m.REPOSITORY},"head_repository":{"full_name":m.REPOSITORY},
        "status":"in_progress","conclusion":None}
    class FakeReads:
        actual = False
        def __init__(self, client): assert client is fake_client
        def json(self, path, **kwargs):
            if path.startswith("/actions/workflows/"): return deepcopy(registration)
            if path == "/actions/runs/10": return deepcopy(run)
            if path == "/git/ref/heads/main":
                return {"ref":"refs/heads/main","object":{"type":"commit","sha":"a"*40}}
            pytest.fail("UNEXPECTED_REMOTE_READ:"+path)
        def tree(self, commit):
            assert commit == "a"*40
            return {},dict(tree)
    fake_client = object()
    publications = []; daily_results = []; statistics_inputs = []
    def runner(path, **kwargs):
        result = modules["daily"].run_daily(path, **kwargs, test_hooks=source_case["hooks"])
        daily_results.append(result)
        return result
    def publisher(files, *, pre_cas_guard):
        pre_cas_guard()
        manifest_path = next(path for path in files if path.endswith("/manifest.json"))
        manifest = json.loads(files[manifest_path])
        assert manifest["test_only"] is True
        for relative, raw in files.items():
            assert relative not in tree
            add(relative,raw)
        publications.append(deepcopy(files))
        pre_cas_guard()
        return {"status":"JOURNAL_STORAGE_ACKNOWLEDGED","signal_date":manifest["signal_date"],
            "as_of_date":manifest["as_of_date"],"commit_sha":"b"*40}
    def statistics(items, **kwargs):
        # Original statistics requires a real private proof and host snapshot;
        # those boundaries are tested independently, never relaxed in source.
        statistics_inputs.append(list(items))
        for item in items:
            assert type(item["publication_proof"]) is SyntheticPublicationProof
            frozen = json.loads(item["snapshot_raw"])
            assert len(frozen["candidate_slots"]) == len(frozen["promotion_slots"]) == 2
            if item["ledger_raw"] is not None:
                assert m.sha(item["ledger_raw"]) == item["expected_ledger_sha256"]
        return {"status":"SYNTHETIC_STATS_BOUNDARY_ONLY","signal_dates":[i["signal_date"] for i in items]}
    hooks = {"clock":lambda:datetime(2026,9,16,8,tzinfo=timezone.utc),"read_factory":lambda:fake_client,
        "proof_factory":lambda row:proof,"daily_runner":runner,"publisher":publisher,"context":context,
        "repo_root":repo,"state_root":source_case["hooks"]["state_parent"]}
    monkeypatch.setattr(m,"code_guard",lambda mods: ({},("SYNTHETIC_CODE_GUARD",)))
    monkeypatch.setattr(issuer.publication,"_Reads",FakeReads)
    monkeypatch.setattr(issuer,"VerifiedResearchPublication",SyntheticPublicationProof)
    monkeypatch.setattr(modules["statistics"],"summarize_natural_statistics",statistics)
    monkeypatch.setattr(socket,"socket",lambda *a,**k:pytest.fail("REAL_NETWORK_FORBIDDEN"))
    monkeypatch.setattr(modules["outcome_collect"].natural.scorer,"predict_forward",lambda *a,**k:pytest.fail("RESCORE_FORBIDDEN"))
    return {"native":source_case,"source":source,"modules":modules,"repo":repo,"artifacts":artifacts,
        "hooks":hooks,"tree":tree,"proof":proof,"publications":publications,"daily_results":daily_results,
        "statistics_inputs":statistics_inputs,"registration":registration,"run":run,"add":add}


@pytest.fixture
def case(tmp_path,monkeypatch): return setup(tmp_path,monkeypatch)


def run(case, *, dry_run=False):
    return m.run_settlement(dry_run=dry_run,work_parent=case["artifacts"],test_hooks=case["hooks"])


def test_real_native_loss_four_slots_is_saved_as_synthetic_not_zero_or_real_authority(case):
    snapshot = case["source"]["snapshot"].read_bytes()
    result = run(case)
    assert result["status"] == "SYNTHETIC_ORCHESTRATION_ONLY"
    assert result["recorded_successful_quote_calls"] == 12
    assert len(case["publications"]) == len(case["daily_results"]) == 1
    ledger = daily_tests.latest(case["daily_results"][0])
    for group in ("candidate_slots","promotion_slots"):
        assert len(ledger[group]) == 2
        assert all(slot["slot_net_return"] == pytest.approx(-.0245) for slot in ledger[group])
    assert snapshot == case["source"]["snapshot"].read_bytes()
    assert result["production_activation_allowed"] is result["formal_ledger_written"] is False
    assert result["whole_history_cumulative_statistics_claimed"] is False


@pytest.mark.parametrize("size",[0,1,2,10])
def test_D_not_due_still_journals_four_slots_without_quotes(tmp_path,monkeypatch,size):
    case=setup(tmp_path,monkeypatch,size=size)
    case["hooks"]["clock"]=lambda:datetime(2026,9,14,8,tzinfo=timezone.utc)
    result=run(case)
    assert result["recorded_successful_quote_calls"] == 0
    assert case["native"]["requests"] == [] and len(case["publications"]) == 1
    ledger=daily_tests.latest(case["daily_results"][0])
    for group in ("candidate_slots","promotion_slots"):
        assert len(ledger[group]) == 2
        assert all(slot["slot_net_return"] is None for slot in ledger[group])


def test_dry_run_only_proves_existing_bytes_never_restores_quotes_or_writes(case):
    result=run(case,dry_run=True)
    assert result["processed"][0]["status"] == "READ_ONLY_PREFLIGHT"
    assert not case["native"]["requests"] and not case["publications"]
    assert not Path(case["hooks"]["state_root"]).exists()


@pytest.mark.parametrize("remove_snapshot",[False,True])
def test_no_context_or_no_snapshot_needs_no_tushare_token_and_no_quotes(case,monkeypatch,remove_snapshot):
    monkeypatch.delenv("TUSHARE_TOKEN",raising=False)
    day=case["source"]["day"]
    del case["tree"][m.EVIDENCE_PREFIX+day+"/context.json"]
    if remove_snapshot: del case["tree"][m.SNAPSHOT_PREFIX+"day_"+day+".json"]
    result=run(case)
    assert result["selected_work_days"]==[] and result["processed"]==[]
    assert result["recorded_successful_quote_calls"]==0
    assert not case["daily_results"] and not case["publications"]


def test_same_asof_and_terminal_later_runs_are_zero_call_read_only(case):
    first=run(case)
    calls=len(case["native"]["requests"])
    second=run(case)
    assert second["selected_work_days"] == []
    assert second["processed"][0]["status"] == "EXISTING_RESEARCH_LEDGER_REVALIDATED"
    case["hooks"]["clock"]=lambda:datetime(2026,9,17,8,tzinfo=timezone.utc)
    third=run(case)
    assert third["selected_work_days"] == [] and len(case["publications"]) == 1
    assert len(case["native"]["requests"]) == calls
    assert first["processed"][0]["journal_manifest_sha256"]


def test_T_then_recovered_original_state_T1_no_original_overwrite(case):
    case["hooks"]["clock"]=lambda:datetime(2026,9,15,8,tzinfo=timezone.utc)
    run(case)
    old=case["publications"][0]
    state=Path(case["hooks"]["state_root"])/case["source"]["day"]/"20260915"
    state.rename(state.with_name("saved-test-original"))
    case["hooks"]["clock"]=lambda:datetime(2026,9,16,8,tzinfo=timezone.utc)
    result=run(case)
    assert result["recorded_successful_quote_calls"] == 6 and len(case["publications"]) == 2
    assert all((case["repo"]/p).read_bytes()==body for p,body in old.items())
    first_manifest=json.loads(next(b for p,b in old.items() if p.endswith("manifest.json")))
    second_manifest=json.loads(next(b for p,b in case["publications"][1].items() if p.endswith("manifest.json")))
    assert second_manifest["previous_manifest_sha256"] == m.sha(m.encoded(first_manifest))


def test_failed_empty_source_keeps_pending_and_same_asof_does_not_retry(case):
    case["native"]["hooks"]["transport"]=lambda contract:daily_tests.old.response(contract,empty=True)
    run(case)
    ledger=daily_tests.latest(case["daily_results"][0])
    assert all(slot["slot_net_return"] is None for group in ("candidate_slots","promotion_slots") for slot in ledger[group])
    count=len(case["publications"])
    again=run(case)
    assert again["recorded_successful_quote_calls"] == 0 and len(case["publications"]) == count


@pytest.mark.parametrize("now",[datetime(2026,9,14,1,tzinfo=timezone.utc)])
def test_preclose_or_closed_calendar_day_does_not_collect_previous_session(case,now):
    case["hooks"]["clock"]=lambda:now
    result=run(case)
    assert result["status"] == "NO_CLOSED_TRADING_SESSION_TODAY"
    assert result["as_of_date"] is None and result["git_writes"] == 0
    assert not case["daily_results"] and not case["publications"]


def test_calendar_closed_day_does_not_collect_previous_session():
    stats=SimpleNamespace(_calendar=lambda *args:["20260911","20260914"],
        labels=SimpleNamespace(settlement=SimpleNamespace(CALENDAR_SHA256="a"*64)))
    assert m.completed_asof(b"calendar",statistics=stats,
        now=datetime(2026,9,13,12,tzinfo=timezone.utc))==(None,["20260911","20260914"])


@pytest.mark.parametrize("field,value",[("run_attempt",True),("run_attempt",2),("head_branch","feature"),
    ("status","completed"),("conclusion","success"),("name","wrong"),("workflow_id",True)])
def test_exact_current_workflow_registration_fails_before_quotes(case,field,value):
    case["run"][field]=value
    with pytest.raises(ValueError,match="REGISTERED_CURRENT"):
        run(case)
    assert not case["native"]["requests"] and not case["publications"]


@pytest.mark.parametrize("field,value",[("api_calls",97),("api_calls",True),("publishable",True),
    ("test_only",False),("daily_module_sha256","0"*64),("dependencies",{})])
def test_invalid_daily_result_never_reaches_git(case,field,value):
    original=case["hooks"]["daily_runner"]
    def invalid(*args,**kwargs):
        result=original(*args,**kwargs);result[field]=value;return result
    case["hooks"]["daily_runner"]=invalid
    result=run(case)
    assert result["status"] == "PARTIAL_FAILURE_RESEARCH_ONLY" and not case["publications"]
    assert result["processed"][0]["api_calls"] is None
    assert result["unrecorded_failed_day_quote_calls_possible"] is True


def test_unissued_plain_proof_never_calls_market_or_writer(case):
    case["hooks"]["proof_factory"]=lambda row:{"verified":True}
    result=run(case)
    assert result["status"] == "PARTIAL_FAILURE_RESEARCH_ONLY"
    assert not case["native"]["requests"] and not case["publications"]


def test_changed_checkout_during_collection_blocks_journal(case):
    original=case["hooks"]["daily_runner"]
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        path=case["repo"]/m.SNAPSHOT_PREFIX/("day_"+case["source"]["day"]+".json")
        path.write_bytes(path.read_bytes()+b" ")
        return result
    case["hooks"]["daily_runner"]=changed
    with pytest.raises(ValueError,match="CHECKOUT|LOCAL_FILE_SIZE_LIMIT"):
        run(case)
    assert not case["publications"]


def test_four_day_budget_missing_context_future_and_priority_are_explicit():
    rows=[]
    for i in range(8):
        rows.append({"signal_date":str(20260914+i),"context":{},"latest_journal_asof":None,
            "planning_terminal_only":False})
    rows[0]["context"]=None
    rows[1]["latest_journal_asof"]="20260921"
    rows[2]["planning_terminal_only"]=True
    selected,covered=m.choose_work(rows,"20260921")
    assert len(selected)==len(covered)==m.MAX_DAYS==4
    assert [r["signal_date"] for r in selected]==[str(20260914+i) for i in range(3,7)]


def test_rotation_does_not_let_four_permanent_proof_failures_starve_later_days():
    rows=[{"signal_date":str(20260914+i),"context":{},"latest_journal_asof":None,
        "planning_terminal_only":False} for i in range(11)]
    selected=set()
    for rotation in range(11):
        batch,_=m.choose_work(rows,"20260930",rotation=rotation)
        assert len(batch)==4
        selected.update(row["signal_date"] for row in batch)
    assert selected=={row["signal_date"] for row in rows}
    first=datetime(2026,9,14,11,50,tzinfo=timezone.utc)
    second=datetime(2026,9,14,12,50,tzinfo=timezone.utc)
    assert m.fair_rotation(second)==m.fair_rotation(first)+1


def test_future_tree_bodies_not_indexed_or_read():
    dates=["20260914","20260915","20260916"]
    tree={m.SNAPSHOT_PREFIX+"day_20260916.json":{"type":"blob","mode":"100644"}}
    rows,future=m.discover(tree,asof="20260915",dates=dates)
    assert rows==[] and future==list(tree)


@pytest.mark.parametrize("name",list(m.PINS))
def test_exact_reviewed_dependency_pins_are_not_zero_or_drifted(name):
    file=m.ROOT/"work/profit_1000_upgrade"/(name+".py")
    assert m.PINS[name]!="0"*64 and m.sha(file.read_bytes())==m.PINS[name]


def test_default_cli_is_read_only_and_has_no_asof_source_token_or_policy_override(monkeypatch,tmp_path):
    seen=[]
    def fake(**kwargs):
        seen.append(kwargs)
        return {"status":"READ_ONLY_PREFLIGHT_COMPLETED","as_of_date":"20260914",
            "work_root":str(tmp_path),"dry_run":True,"selected_work_days":[]}
    monkeypatch.setattr(m,"run_settlement",fake)
    assert m.main(["--work-parent",str(tmp_path)])==0 and seen[0]["dry_run"] is True
    for option in ("--as-of-date","--token","--source","--force","--policy"):
        with pytest.raises(SystemExit): m.main(["--work-parent",str(tmp_path),option,"bad"])


def action_context(tmp_path,monkeypatch,*,event_name="schedule"):
    issuer=m.dependencies()["evidence_publication"]
    path=tmp_path.resolve()/"event.json"
    source={"id":123,"workflow_id":m.OBSERVER_ID,"path":issuer.WORKFLOW_PATH,
        "name":issuer.WORKFLOW_NAME,"head_branch":"main","run_attempt":1,"status":"completed",
        "conclusion":"success","event":"workflow_run","repository":{"full_name":m.REPOSITORY},
        "head_repository":{"full_name":m.REPOSITORY}}
    event={"workflow_run":source} if event_name=="workflow_run" else {}
    write(path,m.encoded(event))
    monkeypatch.setattr(m.sys,"platform","linux")
    for name,value in {"GITHUB_ACTIONS":"true","GITHUB_REPOSITORY":m.REPOSITORY,
        "GITHUB_REF":"refs/heads/main","GITHUB_RUN_ATTEMPT":"1","GITHUB_RUN_ID":"10",
        "GITHUB_SHA":"a"*40,"GITHUB_WORKFLOW_REF":m.REPOSITORY+"/"+m.WORKFLOW_PATH+"@refs/heads/main",
        "GITHUB_EVENT_NAME":event_name,"GITHUB_EVENT_PATH":str(path)}.items(): monkeypatch.setenv(name,value)
    return issuer,path,event


@pytest.mark.parametrize("event",["schedule","workflow_dispatch","workflow_run"])
def test_registered_environment_event_context_returns_pair(tmp_path,monkeypatch,event):
    issuer,path,payload=action_context(tmp_path,monkeypatch,event_name=event)
    context,state=m.execution_context(issuer)
    assert context["run_id"]==10 and context["event_name"]==event
    assert state[0]==path and state[1]==m.encoded(payload)


@pytest.mark.parametrize("name,value",[("GITHUB_ACTIONS","false"),("GITHUB_REF","refs/heads/test"),
    ("GITHUB_RUN_ATTEMPT","2"),("GITHUB_REPOSITORY","other/repo"),("GITHUB_EVENT_NAME","push"),
    ("GITHUB_WORKFLOW_REF","other"),("GITHUB_SHA","main")])
def test_environment_cannot_grant_other_workflow_or_branch(tmp_path,monkeypatch,name,value):
    issuer,_,_=action_context(tmp_path,monkeypatch)
    monkeypatch.setenv(name,value)
    with pytest.raises(ValueError): m.execution_context(issuer)


@pytest.mark.parametrize("field,value",[("id",True),("workflow_id",True),("run_attempt",2),
    ("head_branch","feature"),("status","in_progress"),("conclusion","failure"),("event","push")])
def test_observer_event_must_be_fixed_successful_first_nonpush_main(tmp_path,monkeypatch,field,value):
    issuer,path,event=action_context(tmp_path,monkeypatch,event_name="workflow_run")
    event["workflow_run"][field]=value;write(path,m.encoded(event))
    with pytest.raises(ValueError,match="EXACT_SUCCESSFUL_NON_PUSH"):
        m.execution_context(issuer)
