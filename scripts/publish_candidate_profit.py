#!/usr/bin/env python3
"""Publish the activated candidate's versioned public view, never place orders.

Reads already-frozen natural selections and existing verified outcome journals.
No market requests, training, re-ranking, historical edits or credentialed git.
All GitHub writes are an allowlisted, non-forced REST compare-and-swap.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for base in (ROOT, ROOT / "src"):
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))

REPOSITORY = "njedu2023-prog/DC20"
WORKFLOW_PATH = ".github/workflows/publish_candidate_profit.yml"
WORKFLOW_NAME = "DC20 · Publish active candidate profit"
PUBLIC_ROOT = "outputs/decision/candidate_profit_v1/"
CONFIG_PATH = "models/decision_candidate_profit_activation_v1.json"
MAX_PUBLIC_BYTES = 32 * 1024**2
MAX_PUBLIC_FILES = 4098
UPSTREAMS = {
    357027830: ".github/workflows/research_candidate_natural_observer.yml",
    357485346: ".github/workflows/research_candidate_natural_settlement.yml",
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_workflow_context(env, event, run, registration):
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main" and env.get("GITHUB_RUN_ATTEMPT") == "1",
            "FIRST_MAIN_ACTION_REQUIRED")
    require(env.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW_PATH + "@refs/heads/main",
            "EXACT_ACTIVATION_WORKFLOW_REQUIRED")
    event_name = env.get("GITHUB_EVENT_NAME")
    require(event_name in {"schedule", "workflow_dispatch", "workflow_run"}, "ACTIVATION_EVENT_REJECTED")
    head = env.get("GITHUB_SHA", "")
    require(re.fullmatch("[0-9a-f]{40}", head) and env.get("GITHUB_RUN_ID", "").isdigit(),
            "EXACT_RUN_IDENTITIES_REQUIRED")
    require(registration.get("path") == WORKFLOW_PATH and registration.get("name") == WORKFLOW_NAME
            and registration.get("state") == "active" and type(registration.get("id")) is int,
            "REGISTERED_ACTIVATION_WORKFLOW_REQUIRED")
    for key, value in {"id": int(env["GITHUB_RUN_ID"]), "run_attempt": 1, "head_sha": head,
                       "head_branch": "main", "path": WORKFLOW_PATH, "name": WORKFLOW_NAME,
                       "workflow_id": registration["id"], "event": event_name}.items():
        require(type(run.get(key)) is type(value) and run[key] == value, "ACTIVATION_RUN_CHANGED:" + key)
    require(run.get("status") in {"queued", "in_progress"} and run.get("conclusion") is None
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY,
            "CURRENT_SAME_REPOSITORY_RUN_REQUIRED")
    if event_name == "workflow_run":
        upstream = event.get("workflow_run", {})
        require(type(upstream.get("workflow_id")) is int and upstream["workflow_id"] in UPSTREAMS
                and upstream.get("path") == UPSTREAMS[upstream["workflow_id"]]
                and upstream.get("status") == "completed" and upstream.get("conclusion") == "success"
                and type(upstream.get("run_attempt")) is int and upstream["run_attempt"] == 1
                and upstream.get("head_branch") == "main" and upstream.get("event") != "push"
                and upstream.get("repository", {}).get("full_name") == REPOSITORY
                and upstream.get("head_repository", {}).get("full_name") == REPOSITORY,
                "SUCCESSFUL_SAME_REPOSITORY_NATURAL_TRIGGER_REQUIRED")
    return head


def validate_public_files(files):
    require(type(files) is dict and 2 <= len(files) <= MAX_PUBLIC_FILES, "BOUNDED_PUBLIC_PACKAGE_REQUIRED")
    require(PUBLIC_ROOT + "index.json" in files and PUBLIC_ROOT + "summary.json" in files,
            "INDEX_AND_SUMMARY_REQUIRED")
    total = 0
    for path, raw in files.items():
        require(type(path) is str and re.fullmatch(re.escape(PUBLIC_ROOT) + r"(?:index|summary|day_20[0-9]{6})\.json", path)
                and type(raw) is bytes and 0 < len(raw) <= MAX_PUBLIC_BYTES,
                "PUBLIC_PATH_OR_BODY_REJECTED")
        value = json.loads(raw)
        require(type(value) is dict and encoded(value) == raw, "CANONICAL_PUBLIC_JSON_REQUIRED")
        total += len(raw)
    require(total <= MAX_PUBLIC_BYTES, "PUBLIC_PACKAGE_BUDGET_EXCEEDED")


def publish_files(files, *, source_head, source_tree_sha, source_tree, client, guard):
    """Only update versioned public pointers; never replace a frozen day file."""
    from work.profit_1000_upgrade import candidate_natural_journal_git as git_api
    validate_public_files(files)
    sealed = tuple(sorted(files.items()))
    leaves = lambda tree: {p: (x["mode"], x["type"], x["sha"]) for p, x in tree.items() if x["type"] != "tree"}
    changes = {}
    for path, raw in sealed:
        old = source_tree.get(path)
        if old is not None:
            require(old["type"] == "blob" and old["mode"] == "100644", "REGULAR_PUBLIC_BLOB_REQUIRED")
            if git_api.git_blob(raw) == old["sha"]:
                continue
            require(not re.fullmatch(re.escape(PUBLIC_ROOT) + r"day_20[0-9]{6}\.json", path),
                    "FROZEN_PUBLIC_DAY_CANNOT_BE_REPLACED")
        changes[path] = raw
    current = client.call("GET", "/git/ref/heads/main")
    require(current.get("ref") == "refs/heads/main" and current.get("object", {}).get("sha") == source_head,
            "MAIN_MOVED_REBUILD_WITH_NEW_INPUTS")
    guard()
    if not changes:
        return {"status": "UNCHANGED", "commit_sha": source_head, "files_changed": 0}
    made = client.call("POST", "/git/trees", {"base_tree": source_tree_sha, "tree": [
        {"path": path, "mode": "100644", "type": "blob", "content": raw.decode()}
        for path, raw in sorted(changes.items())]})
    new_tree_sha = git_api.sha40(made["sha"])
    verified = git_api._tree(client.call("GET", "/git/trees/" + new_tree_sha + "?recursive=1"), new_tree_sha)
    expected = {**leaves(source_tree), **{p: ("100644", "blob", git_api.git_blob(raw)) for p, raw in changes.items()}}
    require(leaves(verified) == expected, "UNEXPECTED_PUBLICATION_FILE_CHANGE")
    commit = client.call("POST", "/git/commits", {
        "message": "feat: publish active candidate profit view [dc20-candidate-pages-owned]",
        "tree": new_tree_sha, "parents": [source_head]})
    head = git_api.sha40(commit["sha"])
    require(commit["tree"]["sha"] == new_tree_sha and [p["sha"] for p in commit["parents"]] == [source_head],
            "PUBLICATION_COMMIT_CHANGED")
    require(client.call("GET", "/git/ref/heads/main")["object"]["sha"] == source_head, "MAIN_MOVED_NO_RETRY")
    guard()
    require(tuple(sorted(files.items())) == sealed, "PUBLIC_PACKAGE_CHANGED")
    moved = client.call("PATCH", "/git/refs/heads/main", {"sha": head, "force": False})
    require(moved.get("object", {}).get("sha") == head, "UNCERTAIN_CAS_READ_ONLY_RECONCILIATION_REQUIRED")
    require(client.call("GET", "/git/ref/heads/main")["object"]["sha"] == head,
            "PUBLISHED_HEAD_CHANGED_RECHECK_BEFORE_DEPLOY")
    try:
        # The PATCH and final read can cross the last pre-buy boundary even
        # when the pre-CAS check passed. Never describe that acknowledged write
        # as an on-time publication. Preserve it for read-only reconciliation;
        # do not delete, rewrite, or retry the already-frozen day here.
        guard()
    except Exception:
        raise ValueError(
            "PUBLISHED_CAS_ACKNOWLEDGED_BUT_POST_GUARD_FAILED_READ_ONLY_RECONCILIATION_REQUIRED"
        ) from None
    return {"status": "PUBLISHED", "commit_sha": head, "files_changed": len(changes)}


def build_package(*, repo_root, tree, source_head, read_factory, now):
    from top10decision.decision import candidate_profit_publication as projection
    from top10decision.decision import candidate_formal_shadow_summary as ledger
    from work.profit_1000_upgrade import candidate_natural_settlement_workflow as natural_run
    modules = natural_run.dependencies()
    issuer = modules["evidence_publication"]
    checkout = natural_run.CheckoutReads(repo_root, tree, issuer.gh)
    activation = json.loads(checkout.read(CONFIG_PATH))
    projection.validate_activation(activation)
    calendar_path = str(modules["statistics"].labels.settlement.CALENDAR_PATH)
    calendar_sha = modules["statistics"].labels.settlement.CALENDAR_SHA256
    calendar_raw = checkout.read(calendar_path, expected=calendar_sha)
    dates = modules["statistics"]._calendar(calendar_raw, calendar_sha)
    today = now.astimezone(projection.SHANGHAI).strftime("%Y%m%d")
    require(dates[0] <= today <= dates[-1], "HOST_DATE_OUTSIDE_REVIEWED_CALENDAR")
    completed = [d for d in dates if modules["statistics"].labels._timestamp(
        modules["statistics"].labels._at(d, "15:00:00")) <= now]
    require(bool(completed), "NO_COMPLETED_REVIEWED_SESSION")
    asof = completed[-1]
    indexed, _ = natural_run.discover(tree, asof=asof, dates=dates)
    previous_summary = (json.loads(checkout.read(PUBLIC_ROOT + "summary.json"))
                        if PUBLIC_ROOT + "summary.json" in tree else None)
    terminal_cache = set()
    if previous_summary is not None:
        prior_config = {**activation, "enabled": previous_summary.get("enabled")}
        ledger.validate_summary(previous_summary, activation_config=prior_config)
        prior_groups = {name: {r["signal_date"]: r for r in previous_summary["groups"][name]["daily_sequence"]}
                        for name in ledger.GROUPS}
        cacheable = modules["outcome_collect"].outcomes.TERMINAL | {"MISSING_CANDIDATE"}
        terminal_cache = {day for day in previous_summary["published_signal_dates"]
                          if all(prior_groups[name][day]["status"] in cacheable for name in ledger.GROUPS)}
    projections, inputs, waiting, new_deadlines, cached_days = [], [], [], [], []
    for item in indexed:
        if item["signal_date"] < activation["effective_from_signal_date"]:
            continue
        path = PUBLIC_ROOT + "day_" + item["signal_date"] + ".json"
        if not activation["enabled"] and path not in tree:
            continue  # Explicit rollback stops new adoption, not old holdings.
        if item["signal_date"] in terminal_cache:
            require(path in tree, "SETTLED_PUBLIC_DAY_MISSING")
            previous_raw = checkout.read(path)
            p0_raw = checkout.read("outputs/decision/three_rank_top10_" + item["signal_date"] + ".json")
            day = projection.validate_persisted_day(previous_raw, expected_sha256=digest(previous_raw),
                snapshot_raw=checkout.read(item["snapshot_path"]), current_p0_raw=p0_raw, activation=activation)
            projections.append(day)
            cached_days.append(item["signal_date"])
            continue  # Already-published terminal books do not depend on expiring Actions archives.
        row = natural_run.metadata(item, checkout, modules, asof=asof)
        if row["context"] is None:
            require(path not in tree, "EXISTING_FORMAL_DAY_LOST_ORIGINAL_PROOF")
            waiting.append({"signal_date": row["signal_date"], "status": "PUBLICATION_VERIFICATION_PENDING"})
            continue
        deadline = modules["statistics"].labels._timestamp(
            modules["statistics"].labels._at(row["frozen"]["exec_date"], "09:20:00"))
        if path not in tree and now >= deadline:
            # Preserve research observations but never retroactively adopt
            # an already-mature prediction as a new formal model sample.
            waiting.append({"signal_date": row["signal_date"], "status": "MISSED_FORMAL_PUBLICATION_CUTOFF"})
            continue
        proof = natural_run.publication_proof(row, issuer, read_factory)
        p0_raw = checkout.read("outputs/decision/three_rank_top10_" + row["signal_date"] + ".json")
        if path in tree:
            # Preserve the first public projection's provenance timestamp/head.
            previous_raw = checkout.read(path)
            day = projection.validate_cached_day(previous_raw, expected_sha256=digest(previous_raw),
                snapshot_raw=row["snapshot_raw"], current_p0_raw=p0_raw, publication_proof=proof,
                activation=activation)
        else:
            day = projection.project_verified_day(snapshot_raw=row["snapshot_raw"], publication_proof=proof,
                current_p0_raw=p0_raw, activation=activation, source_main_sha=source_head)
            new_deadlines.append(datetime.fromisoformat(day["formal_publication_deadline_utc"].replace("Z", "+00:00")))
        manifest, bodies = natural_run.load_journal(row, checkout, modules)
        inputs.append(natural_run.stat_input(row, proof, manifest, bodies))
        projections.append(day)
    summary = ledger.build_summary(activation_config=activation, projections=projections, day_inputs=inputs,
        as_of_date=asof, calendar_raw=calendar_raw, expected_calendar_sha256=calendar_sha,
        previous_summary=previous_summary, cached_terminal_days=cached_days)
    index = projection.build_public_index(projections, activation=activation, source_main_sha=source_head,
        generated_at_utc=datetime.now(timezone.utc).isoformat())
    if PUBLIC_ROOT + "index.json" in tree:
        previous_index_raw = checkout.read(PUBLIC_ROOT + "index.json")
        previous_activation = dict(activation)
        previous_activation["enabled"] = json.loads(previous_index_raw).get("enabled")
        previous_index = projection.validate_public_index(previous_index_raw,
            expected_sha256=digest(previous_index_raw), activation=previous_activation)
        require({d["signal_date"] for d in previous_index["days"]} <= {d["signal_date"] for d in index["days"]},
                "EXISTING_FORMAL_DAY_CANNOT_DISAPPEAR")
        if all(previous_index.get(k) == v for k, v in index.items() if k not in {"source_main_sha", "generated_at_utc"}):
            index = previous_index  # No timestamp-only commits or pretend new freezes.
    files = {PUBLIC_ROOT + "index.json": encoded(index), PUBLIC_ROOT + "summary.json": encoded(summary)}
    files.update({PUBLIC_ROOT + "day_" + day["signal_date"] + ".json": encoded(day) for day in projections})
    validate_public_files(files)
    def guard():
        checkout.guard()
        current = datetime.now(timezone.utc)
        require(current >= now, "HOST_CLOCK_MOVED_BACKWARDS")
        require(all(current < deadline for deadline in new_deadlines), "NEW_FORMAL_DAY_PUBLICATION_DEADLINE_PASSED")
    return files, {"as_of_date": asof, "verified_days": len(projections), "waiting": waiting}, guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="publish allowlisted public view through REST")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    from work.profit_1000_upgrade import candidate_natural_settlement_workflow as natural_run
    from work.profit_1000_upgrade import candidate_natural_journal_git as git_api
    modules = natural_run.dependencies(); issuer = modules["evidence_publication"]
    token = os.environ.get(issuer.gh.TOKEN_ENV, "")
    read_factory = lambda: issuer.gh.GitHubReadClient(token)
    reads = issuer.publication._Reads(read_factory())
    event_raw = Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes()
    code_head = validate_workflow_context(os.environ, json.loads(event_raw),
        reads.json("/actions/runs/" + os.environ["GITHUB_RUN_ID"]),
        reads.json("/actions/workflows/publish_candidate_profit.yml"))
    ref = reads.json("/git/ref/heads/main", immutable=False)
    source_head = issuer.gh.exact_sha(ref["object"]["sha"], 40)
    commit, tree = reads.tree(source_head)
    _, code_tree = reads.tree(code_head)
    local, state = natural_run.code_guard(modules)
    for path in ("scripts/publish_candidate_profit.py", WORKFLOW_PATH, CONFIG_PATH,
                 "src/top10decision/decision/candidate_profit_publication.py",
                 "src/top10decision/decision/candidate_formal_shadow_summary.py"):
        raw, _ = issuer.gh.read(ROOT / path)
        local[path] = raw
    for path, raw in local.items():
        issuer.publication._blob_matches(tree, path, raw)
        issuer.publication._blob_matches(code_tree, path, raw)
    now = datetime.now(timezone.utc)
    files, report, source_guard = build_package(repo_root=ROOT, tree=tree, source_head=source_head,
        read_factory=read_factory, now=now)
    def guard():
        source_guard()
        for path, raw in local.items():
            require(issuer.gh.read(ROOT / path)[0] == raw, "PUBLICATION_CODE_CHANGED")
        require(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes() == event_raw, "ACTION_EVENT_CHANGED")
    guard()
    output = Path(args.output_root).resolve()
    require(output != ROOT and ROOT not in output.parents and output not in ROOT.parents
            and not output.exists(), "NEW_ISOLATED_OUTPUT_REQUIRED")
    output.mkdir(parents=True)
    for path, raw in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    result = {"status": "DRY_RUN", "commit_sha": source_head, "files_changed": 0}
    if args.publish:
        result = publish_files(files, source_head=source_head, source_tree_sha=commit["tree"]["sha"],
            source_tree=tree, client=git_api.JournalGitWriter(token), guard=guard)
    result.update(report)
    (output / "publication.json").write_bytes(encoded(result))
    if "GITHUB_OUTPUT" in os.environ:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as handle:
            handle.write("commit_sha=" + result["commit_sha"] + "\n")
            handle.write("published=" + str(args.publish).lower() + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
