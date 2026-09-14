"""All HTTP and clocks synthetic; no real provider calls or future outcomes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import pytest

from work.profit_1000_upgrade import candidate_natural_outcome_collect as c
from work.profit_1000_upgrade.test_candidate_natural_outcomes import make_case

TOKEN = "SYNTHETIC_CREDENTIAL_NEVER_PERSIST_8247"


class Clock:
    def __init__(self): self.elapsed = 0.
    def tick(self): return self.elapsed
    def sleep(self, amount): self.elapsed += amount


def response(contract, *, price=None, pre=None, empty=False, invalid=False):
    api, day, code = c.request_identity(contract)
    price = price if price is not None else (10 if day == "20260915" else 9.8)
    pre = pre if pre is not None else 10
    fields = contract["fields"]
    if api == "stk_auction":
        rows = [{"ts_code":code,"trade_date":day,"price":price,"vol":2_000_000,"amount":price*2_000_000,"pre_close":pre}]
    elif api == "stk_mins":
        rows = [{"ts_code":code,"trade_time":stamp,"open":price,"high":price,"low":price,"close":price,"vol":100,"amount":98000}
            for stamp in c.labels.minute_truth._minute.expected_bar_ends(day)]
    else:
        rows = [{"ts_code":code,"trade_date":day,"open":price,"high":price,"low":price,"close":price,"pre_close":pre,
            "vol":24000,"amount":price*2_400_000,"pct_chg":(price/pre-1)*100,"up_limit":round(pre*1.1,2),"down_limit":round(pre*.9,2)}]
    if empty: rows=[]
    data={"fields":fields,"items":[[row[key] for key in fields] for row in rows],"has_more":False,"count":0}
    if invalid: data["has_more"]=True
    return (" \n"+json.dumps({"code":0,"detail":"...","msg":"","data":data},ensure_ascii=False)+"\n").encode()


def setup(tmp_path, monkeypatch, size=2):
    case=make_case(tmp_path,monkeypatch,size=size)
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    case["collect_index"]=0
    return case


def collect(case, *, previous=None, prior_outcome=None, asof=None, transport=None, **options):
    case["collect_index"]+=1
    root=case["output"].parent / ("collection_"+str(case["collect_index"])) / "candidate_natural_outcome_sources"
    clock=Clock(); seen=[]
    def call(contract):
        seen.append(deepcopy(contract))
        return response(contract) if transport is None else transport(contract)
    params={"expected_snapshot_sha256":case["snapshot_sha"],"calendar_path":case["bundle"]["calendar"]["origin_path"],
        "expected_calendar_sha256":case["bundle"]["calendar"]["sha256"],"as_of_date":asof or case["t"],
        "transport":call,"clock":lambda:datetime(2026,9,17,8,tzinfo=timezone.utc),"monotonic":clock.tick,"sleep":clock.sleep}
    if previous:
        path=Path(previous["collection_root"])/"receipt.json"
        params.update(previous_collection_path=path,expected_previous_collection_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if prior_outcome:
        params.update(previous_outcomes_path=prior_outcome["ledger_path"],expected_previous_outcomes_sha256=prior_outcome["ledger_file_sha256"])
    params.update(options)
    result=c.collect_natural_outcome_sources(case["snapshot"],root,**params)
    return result,seen


def observe(case, collected, *, previous=None):
    return c.outcomes.evaluate_natural_outcomes(case["snapshot"],case["output"],
        expected_snapshot_sha256=case["snapshot_sha"],as_of_date=collected["as_of_date"],source_bundle=collected["source_bundle"],
        expected_existing_ledger_sha256=None if previous is None else previous["ledger_file_sha256"],
        clock=lambda:datetime(2026,9,17,8,tzinfo=timezone.utc))


@pytest.mark.parametrize("size",[0,1,2,10,13])
def test_only_full_frozen_four_slot_union_first_T_even_for_late_asof(tmp_path,monkeypatch,size):
    case=setup(tmp_path,monkeypatch,size)
    frozen=case["snapshot"].read_bytes()
    result,calls=collect(case,asof="20260917")
    assert result["full_frozen_candidate_count"]==min(size,10)
    assert result["slot_union_codes"]==case["codes"]
    assert len(calls)==3*len(case["codes"])<=12
    assert all(c.request_identity(r)[1]==case["t"] for r in calls)
    assert all(c.request_identity(r)[0]!="stk_mins" for r in calls)
    assert case["snapshot"].read_bytes()==frozen
    assert result["natural_forward_admission_issued"] is result["source_authority_issued"] is False
    assert result["callable_injected_for_test"] and all(not r["network_request_performed"] for r in result["requests"])
    c.outcomes._bundle(result["source_bundle"],case["codes"],t=case["t"],t1=case["t1"],asof="20260917",
        dates=[case["day"],case["t"],case["t1"],"20260917"])
    for row in result["request_history"]:
        archive=Path(result["collection_root"])/row["raw_http_sidecar"]["path"]
        assert archive.read_bytes()==response(row["request"])
    assert all(TOKEN.encode() not in p.read_bytes() for p in Path(result["collection_root"]).rglob("*") if p.is_file())


def test_T_then_T1_native_terminal_then_no_later_requests_or_old_byte_changes(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    first,_=collect(case); outcome_t=observe(case,first)
    original={p:p.read_bytes() for p in Path(first["collection_root"]).rglob("*") if p.is_file()}
    second,calls=collect(case,previous=first,prior_outcome=outcome_t,asof=case["t1"])
    assert len(calls)==3*len(case["codes"])
    assert all(c.request_identity(r)[1]==case["t1"] for r in calls)
    assert any(c.request_identity(r)[0]=="stk_mins" for r in calls)
    final=observe(case,second,previous=outcome_t)
    report=json.loads(Path(final["ledger_path"]).read_bytes())["versions"][-1]
    assert all(r["rows"][0]["slot_net_return"]==pytest.approx(-.0245) for r in report["native_singleton_reports"].values())
    third,calls=collect(case,previous=second,prior_outcome=final,asof="20260917")
    assert not calls and third["api_calls"]==0
    assert all(s["reason"]=="TERMINAL_ORIGINAL_EVIDENCE_REUSED" for s in third["stock_plan"])
    assert original=={p:p.read_bytes() for p in original}


def test_bound_valid_sources_require_outcome_validation_before_next_date(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    first,_=collect(case)
    second,calls=collect(case,previous=first,asof="20260917")
    assert not calls
    assert all(s["reason"]=="REQUIRES_OUTCOME_VALIDATION" for s in second["stock_plan"])


@pytest.mark.parametrize("kind",["empty","invalid","transport_error"])
def test_old_invalid_empty_and_failed_attempts_are_not_retried_or_overwritten(tmp_path,monkeypatch,kind):
    case=setup(tmp_path,monkeypatch)
    def call(contract):
        if contract["api_name"]=="daily":
            if kind=="transport_error": raise RuntimeError("provider error "+TOKEN)
            return response(contract,empty=kind=="empty",invalid=kind=="invalid")
        return response(contract)
    first,_=collect(case,transport=call)
    before={p:p.read_bytes() for p in Path(first["collection_root"]).rglob("*") if p.is_file()}
    second,calls=collect(case,previous=first,asof=case["t1"])
    assert not calls and second["api_calls"]==0
    assert any(r["status"]=="PRIOR_INVALID_OR_EMPTY_ATTEMPT_PRESERVED_PENDING" for r in second["requests"])
    assert before=={p:p.read_bytes() for p in before}
    out=observe(case,second)
    rows=json.loads(Path(out["ledger_path"]).read_bytes())["versions"][-1]["native_singleton_reports"]
    assert all(v["rows"][0]["slot_net_return"] is None for v in rows.values())


def test_missing_T1_evidence_targets_that_session_not_all_later_dates(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    first,_=collect(case,asof=case["t1"])
    pending=observe(case,first)
    next_sources,calls=collect(case,previous=first,prior_outcome=pending,asof="20260917")
    assert calls and all(c.request_identity(r)[1]==case["t1"] for r in calls)
    assert all(s["target_trade_date"]==case["t1"] for s in next_sources["stock_plan"])


def test_full_limit_hold_advances_only_one_calendar_session(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    first,_=collect(case); pending=observe(case,first)
    held,_=collect(case,previous=first,prior_outcome=pending,asof=case["t1"],transport=lambda r:response(r,price=11))
    held_outcome=observe(case,held,previous=pending)
    next_sources,calls=collect(case,previous=held,prior_outcome=held_outcome,asof="20260917",
        transport=lambda r:response(r,price=11.5,pre=11))
    assert calls and all(c.request_identity(r)[1]=="20260917" for r in calls)
    assert all(s["reason"]=="NATIVE_UNRESOLVED_NEXT_SESSION_ONLY" for s in next_sources["stock_plan"])


@pytest.mark.parametrize("kind",["plain","escaped","sensitive_key","exception"])
def test_credentials_and_provider_exception_text_never_persist_or_print(tmp_path,monkeypatch,capsys,kind):
    case=setup(tmp_path,monkeypatch)
    def call(contract):
        if kind=="exception": raise RuntimeError("secret "+TOKEN)
        value=json.loads(response(contract))
        if kind=="sensitive_key": value["secret"]=TOKEN
        else: value["msg"]=TOKEN
        raw=json.dumps(value).encode()
        if kind=="escaped": raw=raw.replace(TOKEN.encode(),("".join("\\u%04x"%ord(ch) for ch in TOKEN)).encode())
        return raw
    result,_=collect(case,transport=call)
    assert all(r["raw_http_sidecar"] is None and not r["source_files"] for r in result["requests"])
    assert not capsys.readouterr().out
    assert all(TOKEN.encode() not in p.read_bytes() for p in Path(result["collection_root"]).rglob("*") if p.is_file())


def test_csv_retains_original_decimal_precision_and_field_order_not_upstream_csv():
    contract=c.request_contract("stk_limit","20260915","600000.SH")
    raw=b'{"code":0,"data":{"fields":["down_limit","up_limit","pre_close","ts_code","trade_date"],"items":[[9.00000000000000000001,11.10000000000000000001,null,"600000.SH","20260915"]],"count":0,"has_more":false}}'
    c.safe_response(raw,TOKEN)
    projected=c.csv_projection(raw,contract)
    assert projected==b'down_limit,up_limit,pre_close,ts_code,trade_date\n9.00000000000000000001,11.10000000000000000001,,600000.SH,20260915\n'


@pytest.mark.parametrize("kind",["missing_token","call_budget","byte_budget","time_budget"])
def test_budget_or_credential_gaps_keep_every_slot_and_unrequested_tail(tmp_path,monkeypatch,kind):
    case=setup(tmp_path,monkeypatch,size=13)
    if kind=="missing_token": monkeypatch.delenv("TUSHARE_TOKEN")
    elif kind=="call_budget": monkeypatch.setattr(c,"MAX_CALLS",1)
    elif kind=="byte_budget": monkeypatch.setattr(c,"MAX_TOTAL_BYTES",c.MAX_BYTES)
    else: monkeypatch.setattr(c,"MAX_SECONDS",c.HEADROOM)
    result,calls=collect(case)
    assert len(calls)==(1 if kind=="call_budget" else 0)
    assert len(result["stock_plan"])==len(case["codes"])
    assert set(result["source_bundle"]["by_code"])==set(case["codes"])
    assert len(result["requests"])==3*len(case["codes"])


def test_not_due_D_has_no_calls_even_with_available_token(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    result,calls=collect(case,asof=case["day"])
    assert not calls and all(s["reason"]=="NOT_DUE" for s in result["stock_plan"])


def test_clock_injection_cannot_fall_through_to_real_network(tmp_path):
    with pytest.raises(ValueError,match="TEST_TRANSPORT"):
        c.collect_natural_outcome_sources(tmp_path,tmp_path,expected_snapshot_sha256="0"*64,
            calendar_path=tmp_path,expected_calendar_sha256="0"*64,as_of_date="20260916",clock=lambda:None)


@pytest.mark.parametrize("kind",["wrong_api","wrong_start","extra_params","wrong_fields"])
def test_transport_rejects_unregistered_request_before_network(monkeypatch,kind):
    request=c.request_contract("stk_mins","20260916","600000.SH")
    if kind=="wrong_api": request["api_name"]="daily_basic"
    elif kind=="wrong_start": request["params"]["start_date"]="2026-09-16 09:30:00"
    elif kind=="extra_params": request["params"]["limit"]=1000
    else: request["fields"]=["password"]
    monkeypatch.setattr(c.multiprocessing,"get_context",lambda *a:pytest.fail("NETWORK_PROCESS"))
    with pytest.raises(ValueError): c.official_call(request)


def test_parent_deadline_terminates_stalled_stub_worker(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    monkeypatch.setattr(c,"TIMEOUT",.05)
    def stalled(connection,contract): time.sleep(3)
    monkeypatch.setattr(c,"_worker",stalled)
    started=time.monotonic()
    with pytest.raises(ValueError,match="DEADLINE"):
        c.official_call(c.request_contract("daily","20260915","600000.SH"))
    assert time.monotonic()-started<2


def test_environment_change_during_stub_response_cannot_save_new_secret(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    def changed(contract):
        monkeypatch.setenv("TUSHARE_TOKEN","OTHER_PRIVATE_CREDENTIAL")
        return json.dumps({"code":0,"msg":"OTHER_PRIVATE_CREDENTIAL","data":None}).encode()
    with pytest.raises(ValueError): collect(case,transport=changed)
    assert all(b"OTHER_PRIVATE_CREDENTIAL" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_prior_future_file_is_rejected_before_opening_any_old_price_body(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch); first,_=collect(case)
    path=Path(first["collection_root"])/"receipt.json"
    value=json.loads(path.read_bytes())
    value["output_file_bindings"].append({"path":"http/600000_SH/20260917/daily.response.json","sha256":"0"*64,"bytes":1})
    value.pop("receipt_sha256");value["receipt_sha256"]=c.natural.scorer.canonical_sha(value)
    path.write_bytes(c.natural.storage.encoded(value))
    original=c.natural._read
    def read(where,*a,**k):
        if Path(first["collection_root"]) in Path(where).parents and Path(where)!=path: pytest.fail("PRIOR_BODY_BEFORE_FULL_SCOPE_CHECK")
        return original(where,*a,**k)
    monkeypatch.setattr(c.natural,"_read",read)
    with pytest.raises(ValueError,match="PRIOR_FILE_SCOPE"):
        collect(case,previous=first,asof=case["t1"])


@pytest.mark.parametrize("kind",["raw_sha","source_sha","missing_pair","orphan","cohort","clock_mode"])
def test_prior_request_file_and_scope_crossbindings_fail_before_price_read(tmp_path,monkeypatch,kind):
    case=setup(tmp_path,monkeypatch); first,_=collect(case)
    path=Path(first["collection_root"])/"receipt.json"
    value=json.loads(path.read_bytes())
    row=value["request_history"][0]
    if kind=="raw_sha": row["http_response_sha256"]="0"*64
    elif kind=="source_sha": row["source_files"][0]["sha256"]="0"*64
    elif kind=="missing_pair": value["request_history"][2]["source_files"].pop()
    elif kind=="orphan": row["raw_http_sidecar"]=None;row["source_files"]=[]
    elif kind=="cohort": value["slot_union_codes"]=["600999.SH"]
    else: value["clock_mode"]="HOST_SYSTEM_UTC"
    value.pop("receipt_sha256");value["receipt_sha256"]=c.natural.scorer.canonical_sha(value)
    path.write_bytes(c.natural.storage.encoded(value))
    original=c.natural._read
    def read(where,*a,**k):
        if Path(first["collection_root"]) in Path(where).parents and Path(where)!=path: pytest.fail("OLD_PRICE_READ_BEFORE_CROSSBINDINGS")
        return original(where,*a,**k)
    monkeypatch.setattr(c.natural,"_read",read)
    with pytest.raises(ValueError): collect(case,previous=first,asof=case["t1"])


def test_request_mutation_by_transport_fails_without_persisting_changed_contract(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    def changed(contract):
        raw=response(contract)
        contract["params"]["ts_code"]="600999.SH"
        return raw
    with pytest.raises(ValueError,match="REQUEST_CHANGED"):
        collect(case,transport=changed)
    assert not list(tmp_path.rglob("*.response.json"))


@pytest.mark.parametrize("kind",["before_source_clock","final_elapsed","final_wall_clock","snapshot_mutation"])
def test_entire_operation_end_guards_cannot_return_success(tmp_path,monkeypatch,kind):
    case=setup(tmp_path,monkeypatch)
    clock=Clock(); wall=[datetime(2026,9,17,8,tzinfo=timezone.utc)]
    writer=c.write_new
    def write(root,relative,body,token):
        result=writer(root,relative,body,token)
        if relative=="source_bundle.json" and kind=="before_source_clock": clock.elapsed=301
        if relative=="receipt.json":
            if kind=="final_elapsed": clock.elapsed=301
            elif kind=="final_wall_clock": wall[0]=datetime(2026,9,17,7,tzinfo=timezone.utc)
            elif kind=="snapshot_mutation": case["snapshot"].write_bytes(b"changed source")
        return result
    monkeypatch.setattr(c,"write_new",write)
    with pytest.raises(ValueError):
        collect(case,clock=lambda:wall[0],monotonic=clock.tick,sleep=clock.sleep)


@pytest.mark.parametrize("kind",["empty","oversize","duplicate_key","nonfinite","wrong_stock","wrong_day","unqualified_detail","provider_error"])
def test_unqualified_original_response_is_pending_never_zero_or_fallback(tmp_path,monkeypatch,capsys,kind):
    case=setup(tmp_path,monkeypatch)
    def call(contract):
        if kind=="empty": return b""
        if kind=="oversize": return b" "*(c.MAX_BYTES+1)
        if kind=="duplicate_key": return b'{"code":0,"code":1,"data":null}'
        if kind=="nonfinite": return b'{"code":0,"data":NaN}'
        value=json.loads(response(contract))
        if kind=="provider_error": value["code"]=-1;value["msg"]="PRIVATE_PROVIDER_FAILURE_DETAIL"
        elif kind=="unqualified_detail": value["detail"]="PRIVATE_PROVIDER_FAILURE_DETAIL"
        else:
            field="ts_code" if kind=="wrong_stock" else "trade_date"
            replacement="600999.SH" if kind=="wrong_stock" else "20260917"
            value["data"]["items"][0][value["data"]["fields"].index(field)]=replacement
        return json.dumps(value).encode()
    result,_=collect(case,transport=call)
    assert all(not r["source_files"] for r in result["requests"])
    out=observe(case,result)
    report=json.loads(Path(out["ledger_path"]).read_bytes())["versions"][-1]
    assert all(s["slot_net_return"] is None for k in ("candidate_slots","promotion_slots") for s in report[k])
    assert "PRIVATE_PROVIDER_FAILURE_DETAIL" not in capsys.readouterr().out


@pytest.mark.parametrize("kind",["valid","oversize","empty","error"])
def test_official_worker_uses_only_fixed_https_no_proxy_redirect_or_retry(monkeypatch,capsys,kind):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    contract=c.request_contract("daily","20260915","600000.SH")
    raw=response(contract); packet=[]; closed=[]; seen=[]
    class Reply:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,limit):
            assert limit==c.MAX_BYTES+1
            return b"" if kind=="empty" else b" "*(c.MAX_BYTES+1) if kind=="oversize" else raw
    class Opener:
        def open(self,req,timeout):
            seen.append(req)
            assert req.full_url=="https://api.tushare.pro" and req.get_method()=="POST" and timeout==20
            assert json.loads(req.data)=={**contract,"fields":",".join(contract["fields"]),"token":TOKEN}
            if kind=="error": raise RuntimeError(TOKEN+" PRIVATE_PROVIDER_ERROR")
            return Reply()
    def opener(*handlers):
        assert len(handlers)==2 and isinstance(handlers[0],c.request.ProxyHandler) and handlers[0].proxies=={}
        assert isinstance(handlers[1],c.NoRedirect)
        assert handlers[1].redirect_request(None,None,None,None,None,"https://unapproved.example") is None
        return Opener()
    class Pipe:
        def sendall(self,body): packet.append(body)
        def close(self): closed.append(True)
    monkeypatch.setattr(c.request,"build_opener",opener)
    c._worker(Pipe(),contract)
    assert packet==([b"S"+raw] if kind=="valid" else [b"EREJECTED"])
    assert len(seen)==1 and closed==[True] and not capsys.readouterr().out


def test_saved_timestamp_does_not_claim_completion_of_its_own_later_write(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch); result,_=collect(case)
    assert "source_processing_completed_at_utc" in result and "elapsed_source_processing_seconds" in result
    assert "local_operation_completed_at_utc" not in result and "elapsed_collection_seconds" not in result


def test_copied_json_must_not_contain_current_token_in_escaped_form(tmp_path,monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    escaped="".join("\\u%04x"%ord(ch) for ch in TOKEN)
    with pytest.raises(ValueError,match="SENSITIVE_OUTPUT"):
        c.write_new(tmp_path,"copy.json",('{"msg":"'+escaped+'"}').encode(),TOKEN)
    assert not (tmp_path/"copy.json").exists()


def test_cross_run_byte_restore_to_identical_absolute_root_continues_then_terminal_zero_calls(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    first,_=collect(case); first_observation=observe(case,first)
    def restore_new_inodes(result,suffix):
        root=Path(result["collection_root"])
        original={p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
        original_inode=(root/"receipt.json").stat().st_ino
        archived=root.with_name("archive_"+suffix)
        root.rename(archived)
        shutil.copytree(archived,root)
        assert (root/"receipt.json").stat().st_ino!=original_inode
        assert original=={p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
    restore_new_inodes(first,"T")
    second,calls=collect(case,previous=first,prior_outcome=first_observation,asof=case["t1"])
    assert len(calls)==3*len(case["codes"]) and all(c.request_identity(r)[1]==case["t1"] for r in calls)
    terminal=observe(case,second,previous=first_observation)
    restore_new_inodes(second,"T1")
    third,calls=collect(case,previous=second,prior_outcome=terminal,asof="20260917")
    assert calls==[] and third["api_calls"]==0
    assert all(s["reason"]=="TERMINAL_ORIGINAL_EVIDENCE_REUSED" for s in third["stock_plan"])


def test_real_mode_rejects_test_frozen_selection_before_network(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(c,"official_call",lambda *a:pytest.fail("TEST_SELECTION_NETWORK_CALL"))
    monkeypatch.setattr(c.natural,"_now",lambda clock:datetime(2026,9,17,8,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match="TEST_FROZEN_SELECTION"):
        c.collect_natural_outcome_sources(case["snapshot"],case["output"].parent/"candidate_natural_outcome_sources",
            expected_snapshot_sha256=case["snapshot_sha"],calendar_path=case["bundle"]["calendar"]["origin_path"],
            expected_calendar_sha256=case["bundle"]["calendar"]["sha256"],as_of_date=case["t"])


def test_prior_test_history_cannot_be_relabelled_as_actual_transport(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch); first,_=collect(case)
    path=Path(first["collection_root"])/"receipt.json"
    value=json.loads(path.read_bytes()); value["request_history"][0]["network_request_performed"]=True
    value.pop("receipt_sha256");value["receipt_sha256"]=c.natural.scorer.canonical_sha(value)
    path.write_bytes(c.natural.storage.encoded(value))
    with pytest.raises(ValueError,match="PRIOR_REQUEST_HISTORY"):
        collect(case,previous=first,asof=case["t1"])


def test_parent_deadline_also_bounds_partially_delivered_worker_response(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    monkeypatch.setattr(c,"TIMEOUT",.05)
    def partial(connection,contract):
        connection.sendall(b"Spartial")
        time.sleep(3)
    monkeypatch.setattr(c,"_worker",partial)
    started=time.monotonic()
    with pytest.raises(ValueError,match="DEADLINE"):
        c.official_call(c.request_contract("daily","20260915","600000.SH"))
    assert time.monotonic()-started<2


@pytest.mark.parametrize("kind",["valid","max_valid","oversize","bad_prefix"])
def test_bounded_socket_delivery_keeps_exact_original_worker_bytes(monkeypatch,kind):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    payload=b"x"*(c.MAX_BYTES if kind=="max_valid" else c.MAX_BYTES+1 if kind=="oversize" else 100000)
    packet=(b"E" if kind=="bad_prefix" else b"S")+payload
    def deliver(connection,contract):
        try: connection.sendall(packet)
        finally: connection.close()
    monkeypatch.setattr(c,"_worker",deliver)
    if kind in ("oversize","bad_prefix"):
        with pytest.raises(ValueError,match="REJECTED"):
            c.official_call(c.request_contract("daily","20260915","600000.SH"))
    else:
        assert c.official_call(c.request_contract("daily","20260915","600000.SH"))==payload


def test_parent_disconnect_cannot_print_chained_provider_exception(monkeypatch,capsys):
    monkeypatch.setenv("TUSHARE_TOKEN",TOKEN)
    class Opener:
        def open(self,*a,**k): raise RuntimeError(TOKEN+" PRIVATE_PROVIDER_ERROR")
    class BrokenSocket:
        def sendall(self,*a): raise BrokenPipeError("parent closed")
        def close(self): raise OSError("already closed")
    monkeypatch.setattr(c.request,"build_opener",lambda *a:Opener())
    c._worker(BrokenSocket(),c.request_contract("daily","20260915","600000.SH"))
    captured=capsys.readouterr()
    assert not captured.out and not captured.err
