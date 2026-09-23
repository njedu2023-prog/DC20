"""Bounded natural-research settlement orchestration, never formal trading.

The default CLI is a read-only plan. Real execution needs the fixed Linux main
workflow and same-observer private publication proof. No caller clock/asof,
model training, production ledger, frontend mutation or market subscription.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).absolute().parents[2]
WORKFLOW_PATH = ".github/workflows/research_candidate_natural_settlement.yml"
WORKFLOW_NAME = "DC20 · Settle natural candidate research slots"
REPOSITORY = "njedu2023-prog/DC20"
OBSERVER_ID = 357027830
MAX_DAYS, MAX_METADATA_BYTES, MAX_INDEX_DAYS = 4, 128*1024**2, 4096
SHANGHAI = timezone(timedelta(hours=8))
SCHEDULES = {"50 11 * * 1-5": (11, 50), "50 12 * * 1-5": (12, 50)}
MAX_SCHEDULE_DELAY = timedelta(hours=12)
SCHEMA = "dc20_candidate_natural_settlement_orchestration_v1"
SNAPSHOT_PREFIX = "work/profit_1000_upgrade/candidate_natural_forward/"
EVIDENCE_PREFIX = "work/profit_1000_upgrade/candidate_natural_evidence/"
JOURNAL_PREFIX = "work/profit_1000_upgrade/candidate_natural_journal/"
PINS = {
    "candidate_natural_evidence_publication": "9bf6448e57794b61e7b0965226a70220d8160750b2447a3cb557a4fd341c5d8c",
    "candidate_natural_outcome_collect": "210cdd60bb230b50394579c9fd3615a892ca5d0abd0c118fee5acf4e8d8f4fca",
    "candidate_natural_daily": "5d616c7a194d2278b4308db84743f7b229c6eecea5e60d62d5572f31443adb7d",
    "candidate_natural_journal": "2b8c18728ddbbebd42074f0d6c80ec43d8ec3bf24a5c79e022b0d50b48de9b1e",
    "candidate_natural_journal_git": "534b8f47d320d95b82964188afa58c093dd506106b93fc8383d76645b478be74",
    "candidate_natural_statistics": "657238cade20b67c65178b9e87e222b9007c6834e609c191e1cda16b4be80554",
}
FLAGS = {"research_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "natural_outcome_admission_issued": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "formal_ledger_written": False, "formal_model_replaced": False,
    "front_end_modified": False, "model_training_performed": False,
    "provider_timestamp_semantics_confirmed": False,
    "whole_history_cumulative_statistics_claimed": False}


def require(ok, reason):
    if not ok: raise ValueError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()
SELF_SHA = sha(Path(__file__).read_bytes())


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()


def dependencies():
    modules = {}
    for name, expected in PINS.items():
        file = ROOT/"work/profit_1000_upgrade"/(name+".py")
        require(expected != "0"*64 and file.is_file() and file.stat().st_nlink == 1
            and not any(p.is_symlink() for p in (file,*file.parents)) and sha(file.read_bytes()) == expected,
            "REVIEWED_SETTLEMENT_DEPENDENCY_REQUIRED")
        module = importlib.import_module("work.profit_1000_upgrade."+name)
        require(Path(module.__file__).absolute() == file, "SETTLEMENT_IMPORT_ORIGIN_CHANGED")
        modules[name.removeprefix("candidate_natural_")] = module
    return modules


def code_guard(modules):
    issuer, collector = modules["evidence_publication"], modules["outcome_collect"]
    local, prior = issuer.code_guard()
    gh = issuer.gh
    own = Path(__file__).absolute()
    raw, own_id = gh.read(own)
    require(sha(raw) == SELF_SHA, "SETTLEMENT_ORCHESTRATOR_CHANGED")
    local[str(own.relative_to(ROOT))] = raw
    states = []
    for name, expected in PINS.items():
        path = "work/profit_1000_upgrade/"+name+".py"
        body, identity = gh.read(ROOT/path)
        require(sha(body) == expected, "SETTLEMENT_DEPENDENCY_CHANGED")
        local[path] = body; states.append(identity)
    workflow, workflow_id = gh.read(ROOT/WORKFLOW_PATH)
    local[WORKFLOW_PATH] = workflow
    return local, (own_id,tuple(states),workflow_id,prior,collector.code_guard(),
        modules["daily"]._guard(),modules["statistics"]._guard())


def execution_context(issuer):
    gh = issuer.gh
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "FIRST_MAIN_LINUX_ACTION_REQUIRED")
    run_id = gh.exact_id(os.environ.get("GITHUB_RUN_ID"))
    head = gh.exact_sha(os.environ.get("GITHUB_SHA"),40)
    require(os.environ.get("GITHUB_WORKFLOW_REF") == REPOSITORY+"/"+WORKFLOW_PATH+"@refs/heads/main",
        "EXACT_SETTLEMENT_WORKFLOW_REF_REQUIRED")
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    require(event_name in {"schedule","workflow_dispatch","workflow_run"}, "SETTLEMENT_EVENT_NOT_ALLOWED")
    event_path = gh.path(os.environ.get("GITHUB_EVENT_PATH"))
    raw, identity = gh.read(event_path,2*1024**2)
    event = gh.parse_json(raw)
    if event_name == "schedule":
        require(type(event.get("schedule")) is str and event["schedule"] in SCHEDULES,
            "EXACT_AUTHORIZED_SETTLEMENT_SCHEDULE_REQUIRED")
    if event_name == "workflow_run":
        source = event.get("workflow_run",{})
        require(type(source.get("id")) is int and source["id"] > 0
            and source.get("workflow_id") == OBSERVER_ID and type(source["workflow_id"]) is int
            and source.get("path") == issuer.WORKFLOW_PATH and source.get("name") == issuer.WORKFLOW_NAME
            and source.get("head_branch") == "main" and type(source.get("run_attempt")) is int and source["run_attempt"] == 1
            and source.get("status") == "completed" and source.get("conclusion") == "success"
            and source.get("event") in {"workflow_run","workflow_dispatch"}
            and source.get("repository",{}).get("full_name") == REPOSITORY
            and source.get("head_repository",{}).get("full_name") == REPOSITORY,
            "EXACT_SUCCESSFUL_NON_PUSH_OBSERVER_EVENT_REQUIRED")
    return ({"repository":REPOSITORY,"branch":"main","run_id":int(run_id),"run_attempt":1,
        "code_head_sha":head,"workflow_path":WORKFLOW_PATH,"event_name":event_name,
        "schedule":event.get("schedule") if event_name == "schedule" else None,
        "trigger_observer_run_id":event.get("workflow_run",{}).get("id") if event_name == "workflow_run" else None},
        (event_path,raw,identity))


def scheduled_session(context, run, *, now):
    """Bind a bounded delayed schedule to the already-verified GitHub run.

    The cron and API creation/start timestamps locate the latest possible
    occurrence; they do not make a calendar holiday a trading session. Manual
    and observer events never gain previous-session access through this path.
    """
    if context["event_name"] != "schedule": return None
    require(type(now) is datetime and now.tzinfo is not None, "HOST_AWARE_CLOCK_REQUIRED")
    cron = context.get("schedule")
    require(type(cron) is str and cron in SCHEDULES, "EXACT_AUTHORIZED_SETTLEMENT_SCHEDULE_REQUIRED")
    timestamps = []
    for field in ("created_at", "run_started_at"):
        value = run.get(field)
        require(type(value) is str and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value),
            "REGISTERED_SCHEDULE_RUN_TIMESTAMPS_REQUIRED")
        timestamps.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
    created, started = timestamps
    require(created <= started <= now, "REGISTERED_SCHEDULE_RUN_TIMESTAMPS_INVALID")
    hour, minute = SCHEDULES[cron]
    scheduled = created.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled > created: scheduled -= timedelta(days=1)
    while scheduled.weekday() >= 5: scheduled -= timedelta(days=1)
    require(timedelta(0) <= now - scheduled <= MAX_SCHEDULE_DELAY,
        "SCHEDULE_DELAY_EXCEEDS_BOUNDED_CATCHUP_WINDOW")
    return scheduled


def completed_asof(calendar_raw, *, statistics, now, scheduled_for=None):
    require(type(now) is datetime and now.tzinfo is not None, "HOST_AWARE_CLOCK_REQUIRED")
    dates = statistics._calendar(calendar_raw,statistics.labels.settlement.CALENDAR_SHA256)
    today = now.astimezone(SHANGHAI).strftime("%Y%m%d")
    require(dates[0] <= today <= dates[-1], "HOST_DATE_OUTSIDE_REVIEWED_CALENDAR")
    if scheduled_for is not None:
        require(type(scheduled_for) is datetime and scheduled_for.tzinfo is not None
            and timedelta(0) <= now - scheduled_for <= MAX_SCHEDULE_DELAY,
            "BOUNDED_PAST_SCHEDULE_REQUIRED")
    day = today if scheduled_for is None else scheduled_for.astimezone(SHANGHAI).strftime("%Y%m%d")
    require(dates[0] <= day <= dates[-1], "SCHEDULE_DATE_OUTSIDE_REVIEWED_CALENDAR")
    # Only a registered, bounded schedule can retain its intended trading date
    # after midnight. Holidays never silently fall back to an older session.
    if day not in dates or statistics.labels._timestamp(statistics.labels._at(day,"15:00:00")) > now:
        return None,dates
    return day,dates


def discover(tree, *, asof, dates):
    """Index path identities only, never read a future or unknown body."""
    snapshots, contexts, journals, future = {},{}, {},[]
    for path,node in tree.items():
        if node["type"] == "tree": continue
        match = re.fullmatch(re.escape(SNAPSHOT_PREFIX)+r"day_(20[0-9]{6})\.json",path)
        role = "snapshot"
        if not match:
            match = re.fullmatch(re.escape(EVIDENCE_PREFIX)+r"(20[0-9]{6})/context\.json",path);role="context"
        if not match:
            match = re.fullmatch(re.escape(JOURNAL_PREFIX)+r"(20[0-9]{6})/(20[0-9]{6})/manifest\.json",path);role="journal"
        if match is None: continue
        day = match[1]
        require(day in dates and day >= "20260914", "UNREGISTERED_RESEARCH_DAY_PATH")
        if day > asof or (role=="journal" and match[2]>asof): future.append(path);continue
        require(node["type"]=="blob" and node["mode"]=="100644", "REGULAR_RESEARCH_INDEX_REQUIRED")
        if role=="snapshot": snapshots[day]=path
        elif role=="context": contexts[day]=path
        else:
            require(day <= match[2] and match[2] in dates, "JOURNAL_PATH_DATE_INVALID")
            journals.setdefault(day,{})[match[2]]=path
    require(len(snapshots)<=MAX_INDEX_DAYS and set(contexts)<=set(snapshots) and set(journals)<=set(snapshots),
        "BOUNDED_SNAPSHOT_ROOTED_INDEX_REQUIRED")
    return [{"signal_date":day,"snapshot_path":snapshots[day],"context_path":contexts.get(day),
        "journals":journals.get(day,{}),"latest_journal_asof":max(journals[day]) if day in journals else None}
        for day in sorted(snapshots)],future


class CheckoutReads:
    def __init__(self,root,tree,gh):
        self.root,self.tree,self.gh=root,tree,gh
        self.states,self.bytes={},0
    def read(self,relative,*,expected=None,limit=8*1024**2):
        self.gh.relative(relative)
        node = self.tree.get(relative)
        require(node is not None and node["type"]=="blob" and node["mode"]=="100644"
            and type(node.get("size")) is int and 0<node["size"]<=limit, "BOUNDED_CHECKOUT_GIT_BLOB_REQUIRED")
        path = self.gh.path(self.root/relative)
        raw,identity = self.gh.read(path,limit)
        require(len(raw)==node["size"] and self.gh.git_blob(raw)==node["sha"], "CHECKOUT_NOT_ORIGINAL_CURRENT_GIT_BYTES")
        if expected is not None: require(sha(raw)==self.gh.exact_sha(expected), "CHECKOUT_EXTERNAL_SHA_CHANGED")
        if relative not in self.states: self.bytes+=len(raw)
        require(self.bytes<=MAX_METADATA_BYTES, "BOUNDED_CHECKOUT_READ_BUDGET_EXCEEDED")
        bound=(raw,identity,node["sha"])
        require(relative not in self.states or self.states[relative]==bound,"CHECKOUT_SOURCE_CHANGED")
        self.states[relative]=bound
        return raw
    def guard(self):
        for relative,(raw,identity,blob_sha) in self.states.items():
            current,current_id=self.gh.read(self.root/relative,len(raw))
            require(current_id==identity and current==raw and self.gh.git_blob(current)==blob_sha,
                "BOUND_CHECKOUT_SOURCE_CHANGED")


def restore_empty_source_roots(restored, frozen, modules, *, test_state_root=None):
    """Recreate directory-only state omitted by Git; never invent source files."""
    journal = modules["journal"]
    bound = restored["endpoints"]["final_collection_receipt"]
    base = journal.root_path(test_state_root)/restored["signal_date"]/restored["as_of_date"]
    receipt_path = journal.binding_path(bound, journal.root_path(test_state_root),
        restored["signal_date"], restored["as_of_date"])
    raw = journal.read_bound(receipt_path, bound)
    receipt = json.loads(raw)
    collection = receipt_path.parent
    relative = collection.relative_to(base).as_posix()
    require(re.fullmatch(r"collection_[1-8]/candidate_natural_outcome_sources", relative) is not None
        and receipt.get("collection_root") == str(collection), "EXACT_RESTORED_COLLECTION_ROOT_REQUIRED")
    codes = sorted({s["ts_code"] for key in ("candidate_slots", "promotion_slots")
        for s in frozen[key] if s["ts_code"] is not None})
    by_code = receipt["source_bundle"]["by_code"]
    require(sorted(by_code) == codes, "EXACT_RESTORED_SLOT_SOURCE_ROOTS_REQUIRED")
    roots = []
    for code in codes:
        require(re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code) is not None, "EXACT_RESTORED_STOCK_REQUIRED")
        root = collection/"sources"/code.replace(".", "_")
        require(by_code[code]["source_root"] == str(root)
            and not any(p.is_symlink() for p in (root, *root.parents))
            and (not root.exists() or root.is_dir()), "RESTORED_SOURCE_ROOT_CHANGED")
        roots.append(root)
    # Validate every path before creating any directory. Downstream source and
    # ledger validation remains unchanged; empty directories contain no truth.
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    require(journal.read_bound(receipt_path, bound) == raw, "RESTORED_RECEIPT_CHANGED")


def daily_metadata_profile(daily, frozen, modules):
    """Exact reviewed writer/dependency pairs, never cross-version pin mixing."""
    current = (PINS["candidate_natural_daily"], modules["daily"].PINS)
    profiles = [current]
    if frozen.get("schema_version") == "dc20_fixed_candidate_natural_research_snapshot_20260913_v1":
        profiles.append(("88d75a131946be1884da459b8d357942b29f18fd4d83839ea918951291d7e321", {
            "work/profit_1000_upgrade/candidate_natural_outcome_collect.py": "1d9addaa1aecaf1082023c28ab26b23b8ee5ab79d5fd7b9fd24f55e1320d9ced",
            "scripts/diagnose_core_supervisor.py": "b21794ddd38510ce06c1cbe958fe0744ce28547d998fceb6f34a31b66a5d5a99"}))
    return any(daily.get("daily_module_sha256") == writer and daily.get("dependencies") == pins
        for writer, pins in profiles)


def metadata(row,reads,modules,*,asof,test_state_root=None):
    """Original Git-held metadata classifies work, never grants authority."""
    natural=modules["outcome_collect"].natural
    snapshot_raw=reads.read(row["snapshot_path"],limit=4_000_000)
    frozen=modules["outcome_collect"].outcomes._snapshot(snapshot_raw,sha(snapshot_raw))
    require(frozen["signal_date"]==row["signal_date"],"SNAPSHOT_PATH_IDENTITY_CHANGED")
    result={**row,"snapshot_raw":snapshot_raw,"snapshot_sha256":sha(snapshot_raw),"frozen":frozen,
        "context":None,"context_file_sha256":None,"manifest":None,"manifest_raw":None,"manifest_sha256":None,
        "planning_terminal_only":False,"planning_status":"NEW_DAY"}
    if row["context_path"] is not None:
        context_raw=reads.read(row["context_path"])
        context=natural._json(context_raw)
        require(context.get("signal_date")==row["signal_date"]
            and context.get("snapshot_file_sha256")==sha(snapshot_raw)
            and type(context.get("observer_run_id")) is int and context["observer_run_id"]>0,
            "OBSERVER_CONTEXT_SNAPSHOT_MISMATCH")
        result["context"]=context
        result["context_file_sha256"]=sha(context_raw)
    latest=row["latest_journal_asof"]
    if latest is None: return result
    path=row["journals"][latest]
    raw=reads.read(path)
    manifest=natural._json(raw);journal=modules["journal"]
    require(manifest.get("schema_version")==journal.SCHEMA and manifest.get("signal_date")==row["signal_date"]
        and manifest.get("as_of_date")==latest and manifest.get("snapshot_file_sha256")==sha(snapshot_raw)
        and manifest.get("test_only") is (test_state_root is not None)
        and manifest.get("state_root")==str(journal.root_path(test_state_root)),"JOURNAL_INDEX_BINDING_CHANGED")
    bindings=manifest.get("files")
    require(type(bindings) is list and 3<=len(bindings)<=journal.MAX_FILES,"BOUNDED_JOURNAL_METADATA_REQUIRED")
    mapping={}
    for binding in bindings:
        require(type(binding) is dict and set(binding)=={"origin_path","sha256","bytes","content_path"},"EXACT_JOURNAL_METADATA_BINDING")
        journal.binding_path({k:binding[k] for k in ("origin_path","sha256","bytes")},journal.root_path(test_state_root),row["signal_date"],latest)
        require(binding["content_path"]=="blobs/"+binding["sha256"]+".bin" and binding["origin_path"] not in mapping,
            "JOURNAL_METADATA_CONTENT_IDENTITY_CHANGED")
        mapping[binding["origin_path"]]=binding
    require(set(manifest.get("endpoints",{}))=={"daily_manifest","final_outcomes","final_collection_receipt"},"EXACT_JOURNAL_ENDPOINTS_REQUIRED")
    for role,binding in manifest["endpoints"].items():
        require(binding=={k:mapping[binding["origin_path"]][k] for k in ("origin_path","sha256","bytes")},"JOURNAL_ENDPOINT_CHANGED")
    endpoint=manifest["endpoints"]["daily_manifest"]
    daily_path=str(Path(path).parent)+"/"+mapping[endpoint["origin_path"]]["content_path"]
    daily=natural._json(reads.read(daily_path,expected=endpoint["sha256"]))
    require(daily.get("schema_version")==modules["daily"].SCHEMA and daily.get("signal_date")==row["signal_date"]
        and daily.get("as_of_date")==latest and daily.get("snapshot_file_sha256")==sha(snapshot_raw)
        and daily_metadata_profile(daily, frozen, modules)
        and daily.get("publishable") is (test_state_root is None) and daily.get("test_only") is (test_state_root is not None),
        "ORIGINAL_DAILY_METADATA_REQUIRED")
    require(type(daily.get("steps")) is list and bool(daily["steps"]),"ORIGINAL_DAILY_FINAL_STATUSES_REQUIRED")
    statuses=daily["steps"][-1]["statuses"]
    slots=[*frozen["candidate_slots"],*frozen["promotion_slots"]]
    codes={s["ts_code"] for s in slots if s["ts_code"] is not None}
    require(len(slots)==4 and type(statuses) is dict and set(statuses)==codes
        and all(type(value) is str for value in statuses.values()),"EXACT_FOUR_SLOT_STATUS_IDENTITIES_REQUIRED")
    terminal=all(s["ts_code"] is None or statuses[s["ts_code"]] in modules["outcome_collect"].outcomes.TERMINAL for s in slots)
    result.update(manifest=manifest,manifest_raw=raw,manifest_sha256=sha(raw),planning_terminal_only=terminal,
        planning_status="SAME_ASOF_READ_ONLY" if latest==asof else "TERMINAL_READ_ONLY" if terminal else "PRIOR_PENDING")
    return result


def choose_work(rows,asof,*,rotation=0):
    require(type(rotation) is int and rotation>=0,"EXACT_FAIR_ROTATION_REQUIRED")
    available=[r for r in rows if r["context"] is not None]
    work=[r for r in available if r["latest_journal_asof"]!=asof and not r["planning_terminal_only"]]
    work.sort(key=lambda r:(r["latest_journal_asof"] is not None,r["latest_journal_asof"] or "",r["signal_date"]))
    if work:
        offset=(rotation*MAX_DAYS)%len(work)
        work=work[offset:]+work[:offset]
    selected=work[:MAX_DAYS]
    readonly=sorted((r for r in available if r not in work),key=lambda r:r["signal_date"],reverse=True)
    covered=selected+readonly[:MAX_DAYS-len(selected)]
    return selected,covered


def fair_rotation(now):
    local=now.astimezone(timezone(timedelta(hours=8)))
    # The second authorized evening window advances a full batch. Observer
    # triggers in the same window intentionally share its selection. The next
    # calendar date continues rotation even if four old proofs stay invalid.
    return local.date().toordinal()*2+int((local.hour,local.minute)>=(20,50))


def publication_proof(row,issuer,read_factory):
    """ACK extraction is only a locator; a fresh complete issuer verifies it."""
    context=row["context"];run_id=str(context["observer_run_id"])
    reads=issuer.publication._Reads(read_factory())
    registration=reads.json("/actions/workflows/research_candidate_natural_observer.yml")
    require(type(registration.get("id")) is int and registration["id"]==OBSERVER_ID,"EXACT_OBSERVER_REGISTRATION_REQUIRED")
    run=reads.json("/actions/runs/"+run_id);issuer._run(run,registration,run_id)
    artifact=issuer._artifact(reads.json("/actions/runs/"+run_id+"/artifacts?per_page=100"),run)
    archive=issuer.read_observer_archive(reads.archive(artifact))
    ack=issuer.gh.parse_json(archive["publication.json"])
    commit=issuer.gh.exact_sha(ack.get("commit_sha"),40)
    proof=issuer.verify_published_evidence(evidence_commit=commit,observer_run_id=run_id,github_client=read_factory())
    require(type(proof) is issuer.VerifiedResearchPublication,"REAL_PRIVATE_PUBLICATION_PROOF_REQUIRED")
    proof.assert_unchanged()
    require(proof.signal_date==row["signal_date"] and proof.snapshot_file_sha256==row["snapshot_sha256"]
        and proof.observer_run_id==context["observer_run_id"] and proof.evidence_manifest_sha256==context["manifest_sha256"],
        "INDEPENDENT_PROOF_DOES_NOT_BIND_CHECKOUT")
    wanted=next((b for b in proof.report["evidence_file_bindings"] if b["path"]==row["context_path"]),None)
    require(wanted is not None and wanted["sha256"]==row["context_file_sha256"],"ORIGINAL_CONTEXT_BYTES_NOT_IN_PROOF")
    return proof


def load_journal(row,reads,modules,*,test_state_root=None):
    if row["manifest"] is None: return None,{}
    raw=row["manifest_raw"];manifest=row["manifest"]
    prefix=str(Path(row["journals"][row["latest_journal_asof"]]).parent)+"/"
    bodies={}
    for binding in manifest["files"]:
        relative=binding["content_path"]
        if relative not in bodies: bodies[relative]=reads.read(prefix+relative,expected=binding["sha256"])
    verified=modules["journal"].validate_journal(raw,bodies,expected_manifest_sha256=row["manifest_sha256"],test_state_root=test_state_root)
    require(verified==manifest,"ORIGINAL_JOURNAL_CHANGED")
    return verified,bodies


def journal_endpoint(manifest,bodies,role):
    binding=manifest["endpoints"][role]
    return bodies["blobs/"+binding["sha256"]+".bin"],binding


def stat_input(row,proof,manifest,bodies):
    if manifest is None: raw=binding=receipt=None
    else:
        raw,binding=journal_endpoint(manifest,bodies,"final_outcomes")
        receipt=manifest["endpoints"]["final_collection_receipt"]["sha256"]
    return {"signal_date":row["signal_date"],"snapshot_raw":row["snapshot_raw"],"expected_snapshot_sha256":row["snapshot_sha256"],
        "ledger_raw":raw,"expected_ledger_sha256":None if binding is None else binding["sha256"],
        "ledger_as_of_date":None if manifest is None else manifest["as_of_date"],"publication_proof":proof,
        "source_collection_receipt_sha256":receipt}


def run_settlement(*,dry_run=True,work_parent, test_hooks=None):
    require(type(dry_run) is bool,"EXACT_DRY_RUN_BOOLEAN_REQUIRED")
    modules=dependencies();issuer=modules["evidence_publication"];gh=issuer.gh
    natural=modules["outcome_collect"].natural;statistics=modules["statistics"]
    injected=test_hooks is not None
    if injected:
        require(type(test_hooks) is dict and set(test_hooks)=={"clock","read_factory","proof_factory","daily_runner",
            "publisher","context","repo_root","state_root"} and all(callable(test_hooks[k]) for k in
            ("clock","read_factory","proof_factory","daily_runner","publisher")),"EXACT_SYNTHETIC_HOOKS_REQUIRED")
        context,event_state=test_hooks["context"],None
        repo_root=gh.path(test_hooks["repo_root"],directory=True)
        read_factory=test_hooks["read_factory"];clock=test_hooks["clock"]
        state_root=test_hooks["state_root"]
    else:
        context,event_state=execution_context(issuer);repo_root=ROOT;clock=lambda:datetime.now(timezone.utc);state_root=None
        read_factory=lambda:gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV,""))
    local,before=code_guard(modules)
    reads=issuer.publication._Reads(read_factory())
    require(injected or reads.actual,"ACTUAL_GITHUB_READER_REQUIRED")
    registration=reads.json("/actions/workflows/"+Path(WORKFLOW_PATH).name)
    run=reads.json("/actions/runs/"+str(context["run_id"]))
    require(type(registration.get("id")) is int and registration["id"]>0 and registration.get("state")=="active"
        and registration.get("name")==WORKFLOW_NAME and registration.get("path")==WORKFLOW_PATH
        and type(run.get("workflow_id")) is int and run.get("workflow_id")==registration["id"]
        and type(run.get("id")) is int and run.get("id")==context["run_id"]
        and run.get("path")==WORKFLOW_PATH and run.get("head_branch")=="main"
        and run.get("name")==WORKFLOW_NAME and run.get("head_sha")==context["code_head_sha"]
        and type(run.get("run_attempt")) is int and run.get("run_attempt")==1
        and run.get("status") in {"queued","in_progress"} and run.get("conclusion") is None
        and run.get("event")==context["event_name"] and run.get("repository",{}).get("full_name")==REPOSITORY
        and run.get("head_repository",{}).get("full_name")==REPOSITORY,
        "REGISTERED_CURRENT_SETTLEMENT_RUN_REQUIRED")
    head=reads.json("/git/ref/heads/main",immutable=False)
    require(head.get("ref")=="refs/heads/main" and head.get("object",{}).get("type")=="commit","CURRENT_MAIN_REQUIRED")
    current_sha=gh.exact_sha(head["object"]["sha"],40)
    _,tree=reads.tree(current_sha);_,code_tree=reads.tree(context["code_head_sha"])
    for path,raw in local.items():
        issuer.publication._blob_matches(tree,path,raw);issuer.publication._blob_matches(code_tree,path,raw)
    checkout=CheckoutReads(repo_root,tree,gh)
    calendar_path=str(statistics.labels.settlement.CALENDAR_PATH)
    calendar_raw=checkout.read(calendar_path,expected=statistics.labels.settlement.CALENDAR_SHA256,limit=2*1024**2)
    now=clock();scheduled_for=scheduled_session(context,run,now=now)
    asof,dates=completed_asof(calendar_raw,statistics=statistics,now=now,scheduled_for=scheduled_for)
    session_selection={"basis":"REGISTERED_SCHEDULE" if scheduled_for is not None else "HOST_CLOSED_TRADING_DATE",
        "host_observed_at_utc":now.astimezone(timezone.utc).isoformat(),
        "scheduled_for_utc":scheduled_for.isoformat() if scheduled_for is not None else None,
        "run_created_at_utc":run.get("created_at") if scheduled_for is not None else None,
        "run_started_at_utc":run.get("run_started_at") if scheduled_for is not None else None,
        "delay_seconds":(now-scheduled_for).total_seconds() if scheduled_for is not None else None,
        "max_schedule_delay_seconds":MAX_SCHEDULE_DELAY.total_seconds(),"as_of_date":asof}
    parent=gh.path(work_parent,directory=True)
    require(parent!=repo_root and repo_root not in parent.parents and parent not in repo_root.parents,"ISOLATED_ARTIFACT_PARENT_REQUIRED")
    artifact=Path(tempfile.mkdtemp(prefix="dc20-candidate-settlement-",dir=parent))
    def retain(name,value):
        body=encoded(value);issuer.capture._safe_body(body)
        gh.write_new(artifact,name,body)
        return sha(body)
    def guard():
        checkout.guard()
        require(code_guard(modules)==(local,before),"SETTLEMENT_CODE_CHANGED")
        if event_state is not None:
            path,raw,identity=event_state
            require(gh.read(path,2*1024**2)==(raw,identity),"SETTLEMENT_EVENT_CHANGED")
        current=clock()
        require(type(current) is datetime and current.tzinfo is not None and current>=now,
            "SETTLEMENT_HOST_CLOCK_MOVED_BACKWARDS")
        if scheduled_for is not None:
            require(scheduled_session(context,run,now=current)==scheduled_for,"SETTLEMENT_SCHEDULE_SESSION_CHANGED")
        else:
            require(current.astimezone(SHANGHAI).date()==now.astimezone(SHANGHAI).date(),
                "SETTLEMENT_HOST_SESSION_CHANGED")
    if asof is None:
        result={"schema_version":SCHEMA,"status":"NO_CLOSED_TRADING_SESSION_TODAY","as_of_date":None,
            "dry_run":dry_run,"test_only":injected,"context":context,"source_main_sha":current_sha,
            "session_selection":session_selection,
            "selected_work_days":[],"processed":[],"recorded_successful_quote_calls":0,
            "git_writes":0,"work_root":str(artifact),"writer_sha256":SELF_SHA,"dependency_sha256":dict(PINS),**FLAGS}
        guard();retain("result.json",result);guard()
        return result
    indexed,future=discover(tree,asof=asof,dates=dates)
    rows=[metadata(r,checkout,modules,asof=asof,test_state_root=state_root) for r in indexed]
    rotation=fair_rotation(scheduled_for if scheduled_for is not None else now)
    selected,covered=choose_work(rows,asof,rotation=rotation);selected_days={r["signal_date"] for r in selected}
    records=[];stats_inputs=[];quotes=0
    plan={"as_of_date":asof,"selected_work_days":sorted(selected_days),"covered_statistic_days":sorted(r["signal_date"] for r in covered),
        "all_indexed_days":[r["signal_date"] for r in rows],"ignored_future_paths":future,
        "planning_metadata_is_not_authority":True,"max_market_work_days":MAX_DAYS,
        "fair_rotation_index":rotation,"session_selection":session_selection,
        "days":[{"signal_date":r["signal_date"],"status":"MISSING_OBSERVER_CONTEXT_PENDING" if r["context"] is None else
            "SELECTED" if r in selected else r["planning_status"] if r in covered else "DEFERRED_BUDGET_NEXT_SCHEDULE"} for r in rows]}
    retain("plan.json",plan)
    for row in covered:
        day=row["signal_date"]
        stage="PUBLICATION_PROOF"
        try:
            proof=(test_hooks["proof_factory"](row) if injected else publication_proof(row,issuer,read_factory))
            require(type(proof) is issuer.VerifiedResearchPublication,"EXACT_PRIVATE_PUBLICATION_PROOF_REQUIRED")
            proof.assert_unchanged()
            require(proof.signal_date==day and proof.snapshot_file_sha256==row["snapshot_sha256"],"PUBLICATION_SNAPSHOT_CHANGED")
            stage="ORIGINAL_JOURNAL_VALIDATION"
            manifest,bodies=load_journal(row,checkout,modules,test_state_root=state_root)
            item=stat_input(row,proof,manifest,bodies)
            # Before using any old state, the original native ledger and four
            # slots pass the real statistics input contract; no dict grants.
            statistics.summarize_natural_statistics([item],as_of_date=asof,calendar_raw=calendar_raw,
                expected_calendar_sha256=statistics.labels.settlement.CALENDAR_SHA256,clock=clock if injected else None)
            guard()
            if dry_run or day not in selected_days:
                stats_inputs.append(item)
                records.append({"signal_date":day,"status":"READ_ONLY_PREFLIGHT" if dry_run else "EXISTING_RESEARCH_LEDGER_REVALIDATED",
                    "api_calls":0,"git_writes":0,"ledger_as_of_date":item["ledger_as_of_date"]})
                continue
            previous={}
            if manifest is not None:
                stage="ORIGINAL_STATE_RESTORE"
                restored=modules["journal"].restore_journal(row["manifest_raw"],bodies,
                    expected_manifest_sha256=row["manifest_sha256"],test_state_root=state_root)
                restore_empty_source_roots(restored, row["frozen"], modules, test_state_root=state_root)
                for source,target in (("final_collection_receipt","collection"),("final_outcomes","outcomes")):
                    bound=restored["endpoints"][source]
                    previous["previous_"+target+"_path"]=bound["origin_path"]
                    previous["expected_previous_"+target+"_sha256"]=bound["sha256"]
            runner=test_hooks["daily_runner"] if injected else modules["daily"].run_daily
            stage="BOUNDED_DAILY_COLLECTION"
            daily=runner(repo_root/row["snapshot_path"],expected_snapshot_sha256=row["snapshot_sha256"],
                calendar_path=repo_root/calendar_path,expected_calendar_sha256=statistics.labels.settlement.CALENDAR_SHA256,
                as_of_date=asof,**previous)
            require(daily.get("signal_date")==day and daily.get("as_of_date")==asof
                and daily.get("snapshot_file_sha256")==row["snapshot_sha256"]
                and daily.get("test_only") is injected and daily.get("publishable") is (not injected)
                and daily.get("daily_module_sha256")==PINS["candidate_natural_daily"]
                and daily.get("dependencies")==modules["daily"].PINS
                and type(daily.get("api_calls")) is int and 0<=daily["api_calls"]<=96,
                "EXACT_BOUNDED_DAILY_RESULT_REQUIRED")
            quotes+=daily["api_calls"];require(quotes<=MAX_DAYS*96,"RUN_QUOTE_BUDGET_EXCEEDED")
            binding={k:getattr(proof,k) for k in ("evidence_commit","observer_run_id","evidence_manifest_sha256","snapshot_file_sha256")}
            stage="NEW_JOURNAL_VALIDATION"
            proof.assert_unchanged();guard()
            journal=modules["journal"].build_journal(daily,publication_binding=binding,
                previous_manifest_sha256=row["manifest_sha256"],test_state_root=state_root)
            body_prefix=JOURNAL_PREFIX+day+"/"+asof+"/"
            fresh_bodies={p.removeprefix(body_prefix):b for p,b in journal["files"].items() if p!=body_prefix+"manifest.json"}
            manifest_raw=journal["files"][body_prefix+"manifest.json"]
            checked=modules["journal"].validate_journal(manifest_raw,fresh_bodies,
                expected_manifest_sha256=journal["manifest_sha256"],test_state_root=state_root)
            require(checked==journal["manifest"],"NEW_JOURNAL_FULL_VALIDATION_CHANGED")
            fresh_item=stat_input(row,proof,journal["manifest"],fresh_bodies)
            statistics.summarize_natural_statistics([fresh_item],as_of_date=asof,calendar_raw=calendar_raw,
                expected_calendar_sha256=statistics.labels.settlement.CALENDAR_SHA256,clock=clock if injected else None)
            def pre_cas_guard():
                guard();proof.assert_unchanged()
                repeated=modules["journal"].build_journal(daily,publication_binding=binding,
                    previous_manifest_sha256=row["manifest_sha256"],test_state_root=state_root)
                require(repeated==journal,"NEW_JOURNAL_CHANGED_BEFORE_CAS")
                require(modules["journal"].validate_journal(manifest_raw,fresh_bodies,
                    expected_manifest_sha256=journal["manifest_sha256"],test_state_root=state_root)==checked,
                    "NEW_JOURNAL_CHANGED_DURING_CAS")
            writer=test_hooks["publisher"] if injected else lambda files,**kwargs:modules["journal_git"].publish_new_journal(files,
                github_client=modules["journal_git"].JournalGitWriter(os.environ.get(gh.TOKEN_ENV,"")),**kwargs)
            stage="GIT_APPEND_CAS"
            ack=writer(journal["files"],pre_cas_guard=pre_cas_guard)
            require(ack.get("status")=="JOURNAL_STORAGE_ACKNOWLEDGED" and ack.get("signal_date")==day
                and ack.get("as_of_date")==asof,"RESEARCH_JOURNAL_CAS_NOT_ACKNOWLEDGED")
            retain("publication_"+day+".json",ack)
            stats_inputs.append(fresh_item)
            records.append({"signal_date":day,"status":"SYNTHETIC_PIPELINE_ONLY" if injected else "RESEARCH_JOURNAL_APPENDED",
                "api_calls":daily["api_calls"],"journal_manifest_sha256":journal["manifest_sha256"],
                "journal_commit_sha":ack["commit_sha"],"ledger_as_of_date":asof})
        except Exception:
            records.append({"signal_date":day,"status":"FAILED_CLOSED_RETAIN_LOCAL_ARTIFACTS","api_calls":None,
                "failure_stage":stage,"unknown_calls_not_treated_as_zero":True,"research_admission_issued":False})
            retain("failure_"+day+".json",records[-1])
    stats_inputs.sort(key=lambda row:row["signal_date"])
    statistics_report=statistics.summarize_natural_statistics(stats_inputs,as_of_date=asof,calendar_raw=calendar_raw,
        expected_calendar_sha256=statistics.labels.settlement.CALENDAR_SHA256,clock=clock if injected else None)
    covered_days=[row["signal_date"] for row in stats_inputs]
    statistics_report={"coverage":"PARTIAL_SELECTED_DAYS_NOT_FULL_CUMULATIVE","covered_signal_dates":covered_days,
        "omitted_signal_dates":[r["signal_date"] for r in rows if r["signal_date"] not in covered_days],
        "max_market_work_days":MAX_DAYS,"statistics":statistics_report,"test_only":injected,**FLAGS}
    statistics_sha=retain("statistics.json",statistics_report)
    guard()
    failed=any(r["status"]=="FAILED_CLOSED_RETAIN_LOCAL_ARTIFACTS" for r in records)
    result={"schema_version":SCHEMA,"status":"PARTIAL_FAILURE_RESEARCH_ONLY" if failed else "SYNTHETIC_ORCHESTRATION_ONLY" if injected else
        "READ_ONLY_PREFLIGHT_COMPLETED" if dry_run else "NO_CONTEXT_BOUND_RESEARCH_DAYS" if not covered else
        "BOUNDED_RESEARCH_SETTLEMENT_COMPLETED",
        "as_of_date":asof,"dry_run":dry_run,"test_only":injected,"context":context,"source_main_sha":current_sha,
        "session_selection":session_selection,
        "selected_work_days":sorted(selected_days),"processed":records,"recorded_successful_quote_calls":quotes,
        "unrecorded_failed_day_quote_calls_possible":failed,"statistics_sha256":statistics_sha,"work_root":str(artifact),
        "max_market_work_days":MAX_DAYS,"max_quote_calls_per_day":96,"max_quote_calls_per_run":MAX_DAYS*96,
        "max_quote_seconds_per_day":300,"deferred_days_require_next_schedule":len(covered)<len(rows),
        "writer_sha256":SELF_SHA,"dependency_sha256":dict(PINS),**FLAGS}
    retain("result.json",result);guard()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-parent",type=Path,required=True)
    parser.add_argument("--execute",action="store_true",help="Explicitly run bounded research collection and Git CAS; default is read only")
    args=parser.parse_args(argv)
    try:
        result=run_settlement(dry_run=not args.execute,work_parent=args.work_parent)
        print(json.dumps({k:result[k] for k in ("status","as_of_date","work_root","dry_run","selected_work_days")},sort_keys=True))
        return 1 if result["status"]=="PARTIAL_FAILURE_RESEARCH_ONLY" else 0
    except Exception:
        print("NATURAL_SETTLEMENT_FAILED_CLOSED_RETAIN_ARTIFACTS")
        return 1


if __name__=="__main__": raise SystemExit(main())
