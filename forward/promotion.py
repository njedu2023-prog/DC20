"""Independent P0 recomputation with retained, byte-pinned promotion weights.

This module never opens legacy ranking outputs, profit weights, Actions, Shadow
selections or cumulative statistics.  It reuses the audited feature/scoring
functions, not the legacy publisher.  It performs inference, never training.
Phase 2 is an isolated REPLAY computation; a trusted natural scheduling and
publication receipt must be integrated separately before forward activation.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import io
import json
import math
import re
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "dc20_forward_promotion_day_v1"
RUNTIME_SCHEMA = "dc20_forward_promotion_runtime_v1"
FIXED_INPUTS = {
    "models/decision_three_engines/promotion.joblib": "b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c",
    "models/decision_three_engines/validation_latest.json": "de4cae2427593b05f2e8ab208f7ff80c4e8483dc46cba0be55a4b0543bc93d73",
    "data/decision_three_engines/five_year_supervised_ledger.csv.gz": "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46",
    "data/market/trade_cal_sse.csv": "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748",
}
SECONDARY_PREFIXES = ("profit_", "big_loss_", "p_fill_shadow_")
SECONDARY_FIELDS = {"predicted_profit_probability", "predicted_big_loss_probability"}
REPLAY_CANDIDATE_META = {
    "20260908": {
        "path": "forward/replay_inputs/candidate_meta_20260908.json",
        "sha256": "fd3452a52362ef66162862308e9c2d0333748fd415ee9a81f510460802b884d0",
        "source_commit": "76d1d779dd55f29f986d5c545cb80fd18fa64797",
        "source_path": "data/pred/_pred_source_meta.json",
        "source_blob_sha1": "b5d03a763ed435482334ecb4fb5a58e474ea20d4",
    }
}


class PromotionError(ValueError):
    """P0 cannot prove its own inputs; secondary systems are not consulted."""


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _safe_file(root, relative):
    if not isinstance(relative, str) or "\\" in relative or any(x in {"", ".", ".."} for x in relative.split("/")):
        raise PromotionError("unsafe input path")
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise PromotionError(f"symlink input forbidden: {relative}")
    if not path.is_file():
        raise PromotionError(f"required input missing: {relative}")
    return path


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aware(value):
    if not isinstance(value, str):
        raise PromotionError("generated_at_utc must be an aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionError("invalid generation timestamp") from exc
    if parsed.utcoffset() is None:
        raise PromotionError("generation timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _preflight_code(root):
    """Verify required source bytes against Git BEFORE importing any of them.

    Discover only the P0's fixed runtime list and its executable top-level
    imports. Unused P1 source/weights are not P0 prerequisites.
    """
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(root), *args], check=True,
                capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PromotionError("P0 source verification requires a real Git checkout") from exc
    if Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise PromotionError("Git checkout is not rooted at the supplied repository")
    head = git("rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise PromotionError("invalid Git source commit")
    entry = "scripts/publish_primary_three_rank.py"
    listing = git("ls-tree", "-r", "-z", "HEAD", "--", "src/top10decision", entry)
    tree = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        tree[path.decode()] = (mode, kind, blob)
    checked = {}

    def verify(path):
        if path not in tree or tree[path][0] not in {"100644", "100755"} or tree[path][1] != "blob":
            raise PromotionError(f"P0 source is not a committed regular file: {path}")
        body = _safe_file(root, path).read_bytes()
        blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        if blob != tree[path][2]:
            raise PromotionError(f"P0 source differs from HEAD before import: {path}")
        checked[path] = dict(path=path, sha256=hashlib.sha256(body).hexdigest(),
            git_blob_sha1=blob, git_mode=tree[path][0])
        return body

    entry_tree = ast.parse(verify(entry), filename=entry)
    required = None
    for node in entry_tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "PRIMARY_RUNTIME_CODE_PATHS" for n in node.targets):
            required = ast.literal_eval(node.value)
    if not isinstance(required, tuple) or not required or not all(isinstance(p, str) for p in required):
        raise PromotionError("P0 fixed runtime source inventory is not a literal tuple")
    queue, visited = [entry, *required], set()

    def add_module(name):
        if not name or not (name == "top10decision" or name.startswith("top10decision.")):
            return
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            base = "src/" + "/".join(parts[:i])
            candidates = (base + ".py", base + "/__init__.py")
            for path in candidates:
                if path in tree:
                    queue.append(path)
                elif (root / path).exists():
                    raise PromotionError(f"uncommitted import source: {path}")

    def executable_nodes(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield node
            for _, value in ast.iter_fields(node):
                if isinstance(value, list):
                    yield from executable_nodes([x for x in value if isinstance(x, ast.AST)])
                elif isinstance(value, ast.AST):
                    yield from executable_nodes([value])

    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        parsed = ast.parse(verify(path), filename=path)
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        package = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
        for node in executable_nodes(parsed.body):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add_module(alias.name)
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1] + ([prefix] if prefix else []))
                add_module(prefix)
                for alias in node.names:
                    if alias.name != "*":
                        add_module(prefix + "." + alias.name)
    return head, [checked[path] for path in sorted(checked)]


def _import_p0(root):
    # Import only the retained P0 computation surface. Disable bytecode writes
    # so merely loading its modules cannot mutate the source checkout.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        for path in (root, root / "src"):
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)
        module = importlib.import_module("scripts.publish_primary_three_rank")
        if Path(module.__file__).resolve() != root / "scripts/publish_primary_three_rank.py":
            raise PromotionError("loaded P0 module belongs to another checkout")
        for name, item in list(sys.modules.items()):
            if name == "top10decision" or name.startswith("top10decision."):
                filename = getattr(item, "__file__", None)
                if filename and not Path(filename).resolve().is_relative_to(root / "src/top10decision"):
                    raise PromotionError("retained P0 import belongs to another checkout")
        return module
    finally:
        sys.dont_write_bytecode = previous


def _json_records(frame, pd):
    def scalar(value):
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, float) and not math.isfinite(value):
            raise PromotionError("runtime feature contains infinite value")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise PromotionError("unsupported runtime scalar")
        return value
    return [{str(key): scalar(value) for key, value in row.items()}
            for row in frame.to_dict("records")]


def _load_replay_candidate(root, date, exec_date, legacy):
    """Use a pinned dated metadata copy, never tomorrow's mutable pointer."""
    spec = REPLAY_CANDIDATE_META.get(date)
    if spec is None:
        raise PromotionError(f"no independently pinned candidate metadata for replay D={date}")
    relative = f"data/pred/archive/pred_source_{date}.csv"
    path = _safe_file(root, relative)
    actual_sha = _sha(path)
    frame = legacy._read_csv(path, label="exact-D replay candidate archive")
    legacy._validate_exact_dates(frame, date, label="replay candidate archive")
    meta_path = _safe_file(root, spec["path"])
    if _sha(meta_path) != spec["sha256"]:
        raise PromotionError("dated replay candidate metadata hash changed")
    meta = legacy._read_json(meta_path, label="pinned replay candidate metadata")
    normal = legacy._normal_date
    if normal(meta.get("resolved_trade_date")) != date or any(meta.get(key) != actual_sha for key in ("sha256", "body_sha256")):
        raise PromotionError("replay candidate date/body hash mismatch")
    consistency, profile = meta.get("consistency"), meta.get("csv_profile")
    if not isinstance(consistency, dict) or consistency.get("archive_path") != relative:
        raise PromotionError("replay candidate archive path is not exact-D")
    if not isinstance(profile, dict) or normal(profile.get("trade_date")) != date:
        raise PromotionError("replay candidate CSV profile is not exact-D")
    for record in (consistency, profile):
        target = normal(record.get("target_trade_date"))
        if target and target != exec_date:
            raise PromotionError("replay candidate target is not strict calendar T")
    source_repo, commit = meta.get("source_repository"), meta.get("resolved_commit")
    if source_repo != "njedu2023-prog/a-top10" or not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise PromotionError("replay candidate immutable upstream identity is invalid")
    return frame, dict(path=relative, sha256=actual_sha, row_count=len(frame),
        meta_path=spec["path"], meta_sha256=spec["sha256"], source_repository=source_repo,
        resolved_commit=commit, created_at_utc=meta.get("created_at_utc", ""),
        metadata_archive_source=dict(source_commit=spec["source_commit"], path=spec["source_path"],
            git_blob_sha1=spec["source_blob_sha1"], sha256=spec["sha256"]))


def compute_promotion_day(root, signal_date, *, generated_at_utc, generation_mode="REPLAY"):
    """Return promotion-only rows; no profit availability is required."""
    return compute_promotion_bundle(root, signal_date,
        generated_at_utc=generated_at_utc, generation_mode=generation_mode)["day"]


def compute_promotion_bundle(root, signal_date, *, generated_at_utc, generation_mode="REPLAY"):
    """Rebuild raw features and run only the retained promotion model.

    A real Git checkout is mandatory even for replay. Current D raw/candidate
    files are exact-date hash-bound to their upstream immutable commits;
    preceding market sessions, model and feature code are bound to Git HEAD.
    The returned full-pool runtime sidecar is a separate optional downstream
    input, never a reason for P0 to read or await a profit output.
    """
    if generation_mode != "REPLAY":
        raise PromotionError("Phase 2 accepts REPLAY only until trusted natural scheduling is integrated")
    if not isinstance(signal_date, str) or not re.fullmatch(r"20\d{6}", signal_date):
        raise PromotionError("signal_date must be YYYYMMDD")
    try:
        datetime.strptime(signal_date, "%Y%m%d")
    except ValueError as exc:
        raise PromotionError("invalid signal date") from exc
    timestamp = _aware(generated_at_utc)
    if timestamp > datetime.now(timezone.utc):
        raise PromotionError("generation timestamp is in the future")
    root = Path(root).resolve(strict=True)
    for path, expected in FIXED_INPUTS.items():
        if _sha(_safe_file(root, path)) != expected:
            raise PromotionError(f"retained model/input bytes changed: {path}")
    source_commit, code_bindings = _preflight_code(root)
    legacy = _import_p0(root)
    try:
        return _compute(root, signal_date, timestamp.isoformat(), legacy, source_commit, code_bindings)
    except PromotionError:
        raise
    except (legacy.PrimaryDGenerationError, legacy.ThreeEngineArtifactError,
            legacy.ThreeRankContractError, ValueError, TypeError, KeyError, OSError) as exc:
        raise PromotionError(f"P0 computation blocked: {exc}") from exc


def compute_promotion_from_inputs(root, bundle_dir, *, expected_manifest_sha256,
        generated_at_utc, generation_mode="REPLAY"):
    """Run retained P0 from independently verified, in-memory upstream bytes.

    Unlike the dated legacy replay route, no root market/candidate metadata or
    history is consulted. The manifest's externally supplied SHA is mandatory;
    its collector revision is not confused with the current inference HEAD.
    This is still REPLAY, not an admission, publication or point-in-time claim.
    """
    if generation_mode != "REPLAY":
        raise PromotionError("bundle computation accepts REPLAY only")
    timestamp = _aware(generated_at_utc)
    if timestamp > datetime.now(timezone.utc):
        raise PromotionError("generation timestamp is in the future")
    root = Path(root).resolve(strict=True)
    for path, expected in FIXED_INPUTS.items():
        if _sha(_safe_file(root, path)) != expected:
            raise PromotionError(f"retained model/input bytes changed: {path}")
    source_commit, code_bindings = _preflight_code(root)
    from .bundle import VerifiedInputBundle
    try:
        verified = VerifiedInputBundle(root, bundle_dir, expected_manifest_sha256)
        manifest = verified.manifest
        if timestamp < _aware(manifest["collected_at_utc"]):
            raise PromotionError("inference timestamp precedes verified input collection")
        legacy = _import_p0(root)
        try:
            return _compute_from_inputs(root, verified, timestamp.isoformat(), legacy,
                source_commit, code_bindings)
        except (legacy.PrimaryDGenerationError, legacy.ThreeEngineArtifactError,
                legacy.ThreeRankContractError) as exc:
            raise PromotionError(f"bundle P0 computation blocked: {exc}") from exc
    except PromotionError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise PromotionError(f"bundle P0 computation blocked: {exc}") from exc


def _input_binding(verified, relative):
    binding = verified.file_record(relative)
    # Bundle-relative identity is deliberate: never pretend the byte source is
    # a committed DC20 file or synthesize the old _sync_meta contract.
    return dict(binding, path=relative,
        binding_basis="verified_input_manifest", input_manifest_sha256=verified.manifest_sha256)


def _input_frame(legacy, body, *, date, table):
    if body is None:
        raise PromotionError(f"required bundle table unavailable: {date}/{table}")
    frame = legacy.pd.read_csv(io.BytesIO(body), encoding="utf-8-sig", low_memory=False)
    if table != "stock_basic":
        legacy._validate_exact_dates(frame, date, label=f"bundle {date}/{table}")
    if table in {"daily", "daily_basic", "stk_limit", "stock_basic"} and frame.empty:
        raise PromotionError(f"required bundle table empty: {date}/{table}")
    return frame


def _bundle_engine(legacy, verified, root, calendar, verifier):
    """Keep retained mathematical methods but replace their complete I/O edge.

    CSV parsing/index normalization matches AuctionV3Engine.market_table.
    Minute bytes are not in this bundle contract: missing means unavailable,
    never root fallback or fabricated zero-price observations.
    """
    manifest, pd = verified.manifest, legacy.pd
    retained_engine = sys.modules[legacy.PrimaryDReadOnlyEngine.__mro__[1].__module__]

    class ReadOnlyConfig(legacy.AuctionV3Config):
        def ensure_directories(self):
            return None

    class BundleEngine(legacy.PrimaryDReadOnlyEngine):
        def _check_date(self, date):
            if date not in self._primary_context_dates:
                raise PromotionError("feature requested data outside verified D-close history")

        def _market_path(self, trade_date, name):
            self._check_date(trade_date)
            raise PromotionError("bundle market data must be consumed as verified bytes")

        def _record_path(self, trade_date, name, path):
            self._check_date(trade_date)
            relative = str(path)
            binding = _input_binding(verified, relative)
            if binding.get("snapshot_date") != trade_date or binding.get("table") != name:
                raise PromotionError("bundle consumed-file identity mismatch")
            self._primary_consumed[relative] = dict(binding, trade_date=trade_date)

        def market_table(self, trade_date, name):
            self._check_date(trade_date)
            key = (trade_date, name)
            if key in self._market_cache:
                return self._market_cache[key]
            body = verified.read_market_bytes(trade_date, name)
            frame = pd.DataFrame() if body is None else _input_frame(
                legacy, body, date=trade_date, table=name)
            if body is not None:
                inventory = manifest["market"][trade_date]
                relative = (inventory["required_tables"].get(name)
                    or inventory["optional_tables"][name]["path"])
                self._record_path(trade_date, name, relative)
            if not frame.empty and "trade_date" in frame.columns:
                source_dates = frame["trade_date"].map(retained_engine._normal_date)
                frame = frame[source_dates.eq(trade_date)].copy()
                if not frame.empty:
                    frame["trade_date"] = trade_date
            if not frame.empty and "ts_code" in frame.columns:
                frame = frame.copy()
                frame["ts_code"] = frame["ts_code"].map(retained_engine._normal_code)
                frame = frame.drop_duplicates("ts_code", keep="last").set_index("ts_code", drop=False)
            self._market_cache[key] = frame
            return frame

        def _minute_path(self, trade_date, code):
            self._check_date(trade_date)
            raise PromotionError("minute paths are not part of the verified input bundle")

        def minute_table(self, trade_date, code):
            self._check_date(trade_date)
            return pd.DataFrame()

    return BundleEngine(ReadOnlyConfig(root=root), signal_date=verified.signal_date,
        context_dates=list(calendar["runtime_context_dates"]), verifier=verifier,
        exact_d_inventory={})


def _compute_from_inputs(root, verified, generated, legacy, preflight_commit, code_bindings):
    manifest, date = verified.manifest, verified.signal_date

    class SafeHeadVerifier(legacy.GitHeadInputVerifier):
        def bind(self, relative, *, label):
            _safe_file(self.root, Path(relative).as_posix())
            return super().bind(relative, label=label)

    verifier = SafeHeadVerifier(root)
    if verifier.head != preflight_commit:
        raise PromotionError("Git HEAD changed between import and bundle computation")
    for path in legacy.PRIMARY_RUNTIME_CODE_PATHS:
        _safe_file(root, path)
        verifier.bind(path, label="retained P0 runtime source")
    exec_date, exit_date, calendar = legacy.load_strict_sse_dates(root, date, verifier=verifier)
    if (exec_date, exit_date) != (verified.exec_date, verified.exit_date) or \
            calendar["runtime_context_dates"] != manifest["market_sessions"] or \
            calendar["historical_dates"] != manifest["history_sessions"]:
        raise PromotionError("bundle dates disagree with retained strict SSE calendar")
    if _sha(_safe_file(root, calendar["path"])) != hashlib.sha256(verified.read_calendar_bytes()).hexdigest():
        raise PromotionError("bundle calendar differs from retained strict SSE bytes")
    candidates = _input_frame(legacy, verified.read_candidate_bytes(), date=date, table="candidate")
    candidate_binding = _input_binding(verified, manifest["candidate_path"])
    candidate_binding["resolved_commit"] = manifest["pred_commit"]
    market, market_bindings, history_files = {}, {}, []
    for session in manifest["market_sessions"]:
        inventory = manifest["market"][session]
        for name, relative in inventory["required_tables"].items():
            frame = _input_frame(legacy, verified.read_market_bytes(session, name), date=session, table=name)
            binding = dict(_input_binding(verified, relative), trade_date=session)
            if session == date:
                market[name], market_bindings[name] = frame, binding
            else:
                history_files.append(binding)
        if session == date:
            for name, entry in inventory["optional_tables"].items():
                if entry["status"] == "PRESENT":
                    market_bindings[name] = dict(_input_binding(verified, entry["path"]), trade_date=date)
    history = dict(read_only=True, network_fetch_allowed=False,
        binding_basis="verified_input_manifest", source_repository="njedu2023-prog/a-share-top3-data",
        source_commit=manifest["market_commit"], input_manifest_sha256=verified.manifest_sha256,
        session_count=len(manifest["history_sessions"]), dates=manifest["history_sessions"],
        tables=list(legacy.HISTORY_CONTEXT_TABLES), file_count=len(history_files), files=history_files)
    metadata_path = manifest["market"][date]["metadata_path"]
    market_binding = dict(source_repository="njedu2023-prog/a-share-top3-data",
        resolved_commit=manifest["market_commit"], meta_path=metadata_path,
        meta_sha256=verified.file_record(metadata_path)["sha256"], tables=market_bindings,
        binding_basis="verified_input_manifest", input_manifest_sha256=verified.manifest_sha256,
        optional_gaps=manifest["optional_gaps"])
    pool, pool_audit = legacy.build_exact_primary_pool(candidates, market, date)
    validation_relative = "models/decision_three_engines/validation_latest.json"
    for relative in legacy.PROMOTION_PRIOR_SOURCE_PATHS:
        _safe_file(root, relative)
    loaded = legacy.load_promotion_only_artifacts(root / validation_relative, root=root)
    if any(payload.get("bundle") is not None for head, payload in loaded.payloads.items() if head != "promotion"):
        raise PromotionError("secondary model loaded into promotion-only computation")
    static = legacy.bind_committed_static_inputs(root, verifier,
        validation_relative=validation_relative, loaded=loaded)
    engine = _bundle_engine(legacy, verified, root, calendar, verifier)
    return _complete_promotion(root, date, generated, legacy, verifier, code_bindings,
        exec_date, exit_date, calendar, history, candidate_binding, market_binding,
        pool, pool_audit, loaded, static, engine, input_manifest=manifest,
        input_manifest_sha256=verified.manifest_sha256)


def _compute(root, date, generated, legacy, preflight_commit, code_bindings):
    pd = legacy.pd

    class SafeHeadVerifier(legacy.GitHeadInputVerifier):
        def bind(self, relative, *, label):
            _safe_file(self.root, Path(relative).as_posix())
            return super().bind(relative, label=label)

    class ReadOnlyConfig(legacy.AuctionV3Config):
        def ensure_directories(self):
            # The legacy constructor otherwise creates Action/metrics dirs.
            return None

    class ExactReadOnlyEngine(legacy.PrimaryDReadOnlyEngine):
        def _market_path(self, trade_date, name):
            if trade_date not in self._primary_context_dates:
                raise PromotionError("feature requested market data outside D-close history")
            relative = f"data/market/raw/{trade_date[:4]}/{trade_date}/{name}.csv"
            target = self.config.root / relative
            if not target.exists():
                return None  # Optional table; never substitute a latest file.
            target = _safe_file(self.config.root, relative)
            self._record_path(trade_date, name, target)
            return target

        def minute_table(self, trade_date, code):
            if trade_date not in self._primary_context_dates:
                raise PromotionError("minute feature requested future/outside-window data")
            return super().minute_table(trade_date, code)

    verifier = SafeHeadVerifier(root)
    if verifier.head != preflight_commit:
        raise PromotionError("Git HEAD changed between import and computation")
    # Recheck required source immediately before feature/model computation.
    for path in legacy.PRIMARY_RUNTIME_CODE_PATHS:
        verifier.bind(path, label="retained P0 runtime source")
    exec_date, exit_date, calendar = legacy.load_strict_sse_dates(root, date, verifier=verifier)
    history = legacy.bind_committed_history_context(root, date, calendar["historical_dates"], verifier)
    _safe_file(root, f"data/market/raw/{date[:4]}/{date}/_sync_meta.json")
    candidates, candidate_binding = _load_replay_candidate(root, date, exec_date, legacy)
    if candidate_binding["source_repository"] != "njedu2023-prog/a-top10" or not re.fullmatch(r"[0-9a-f]{40}", candidate_binding["resolved_commit"]):
        raise PromotionError("candidate source is not the expected immutable upstream")
    market, market_binding = legacy.load_exact_market_package(root, date)
    if market_binding["source_repository"] != "njedu2023-prog/a-share-top3-data":
        raise PromotionError("market source is not the expected immutable upstream")
    for binding in market_binding["tables"].values():
        _safe_file(root, binding["path"])
    for name in ("daily", "daily_basic", "limit_list_d", "stk_limit"):
        legacy._validate_exact_dates(market[name], date, label=f"P0 exact-D {name}")
    pool, pool_audit = legacy.build_exact_primary_pool(candidates, market, date)
    validation_relative = "models/decision_three_engines/validation_latest.json"
    loaded = legacy.load_promotion_only_artifacts(root / validation_relative, root=root)
    if any(payload.get("bundle") is not None for head, payload in loaded.payloads.items() if head != "promotion"):
        raise PromotionError("secondary model loaded into promotion-only computation")
    static = legacy.bind_committed_static_inputs(root, verifier,
        validation_relative=validation_relative, loaded=loaded)
    engine = ExactReadOnlyEngine(ReadOnlyConfig(root=root), signal_date=date,
        context_dates=list(calendar["runtime_context_dates"]), verifier=verifier,
        exact_d_inventory=market_binding["tables"])
    return _complete_promotion(root, date, generated, legacy, verifier,
        code_bindings, exec_date, exit_date, calendar, history, candidate_binding,
        market_binding, pool, pool_audit, loaded, static, engine)


def _complete_promotion(root, date, generated, legacy, verifier, code_bindings,
        exec_date, exit_date, calendar, history, candidate_binding, market_binding,
        pool, pool_audit, loaded, static, engine, *, input_manifest=None,
        input_manifest_sha256=None):
    """Shared retained feature/scoring math for the two read-only input routes."""
    pd = legacy.pd
    base = engine._current_base(date, pool) if not pool.empty else pool
    inference = legacy.augment_three_engine_runtime_base(engine, date, base)
    pool_audit["hard_to_inference"] = legacy.audit_complete_hard_pool(
        pool, base, inference, engine=engine, signal_date=date)
    inference = legacy._ensure_empty_promotion_schema(inference, loaded)
    scored = legacy.score_three_engine_snapshot(inference, loaded, signal_date=date, top_n=10)
    if scored.promotion_pool_size != len(inference) or set(scored.rows["ts_code"]) != set(inference["ts_code"]):
        raise PromotionError("promotion scoring lost or replaced hard-pool members")
    # Pure contract construction validates promotion gates; it neither reads nor
    # writes legacy ranking output, and unavailable secondary heads stay empty.
    contract = legacy.build_primary_contract(scored, signal_date=date,
        exec_date=exec_date, exit_date=exit_date, generated_at_utc=generated)
    runtime, runtime_binding = legacy.build_runtime_feature_snapshot(scored, loaded,
        signal_date=date, generated_at_utc=generated, hard_pool_size=len(pool))
    columns = [name for name in runtime.columns if not name.startswith(SECONDARY_PREFIXES)
               and name not in SECONDARY_FIELDS]
    runtime_rows = _json_records(runtime[columns], pd)
    runtime_envelope = dict(schema_version=RUNTIME_SCHEMA, signal_date=date,
        columns=columns, rows=runtime_rows)
    runtime_sha = canonical_sha256(runtime_envelope)
    features_by_code = {row["ts_code"]: row for row in runtime_rows}
    rows = []
    for frozen in contract["rows"]:
        feature = features_by_code[frozen["ts_code"]]
        label, delta = feature.get("path_label"), feature.get("path_strength_delta")
        if feature.get("path_label_code") == "INSUFFICIENT" or label in {None, "", "路径数据不足"}:
            delta = None
        rows.append(dict(ts_code=frozen["ts_code"], name=frozen["name"], industry=frozen["industry"],
            stage_transition=frozen["stage_transition"], promotion_rank=frozen["promotion_rank"],
            promotion_probability=frozen["predicted_promotion_probability"], path_label=label,
            path_change_pct=delta))
    consumed = engine.consumed_bindings()
    # Detect source mutation during computation, including the metadata files.
    observed = [calendar, *code_bindings]
    if input_manifest is None:
        observed += [candidate_binding, *market_binding["tables"].values(), *history["files"], *consumed]
    observed += [item for group in ("runtime_code", "promotion_prior_sources", "model") for item in static[group]]
    for binding in observed:
        if _sha(_safe_file(root, binding["path"])) != binding["sha256"]:
            raise PromotionError("input changed during promotion computation")
    for binding in (() if input_manifest is not None else (candidate_binding, market_binding)):
        if _sha(_safe_file(root, binding["meta_path"])) != binding["meta_sha256"]:
            raise PromotionError("input metadata changed during promotion computation")
    if legacy.GitHeadInputVerifier(root).head != verifier.head:
        raise PromotionError("Git HEAD changed during promotion computation")
    source = dict(schema_version="dc20_forward_promotion_source_v1", repository="njedu2023-prog/DC20",
        source_commit=verifier.head, model_sha256=FIXED_INPUTS["models/decision_three_engines/promotion.joblib"],
        model_version=contract["models"]["promotion"]["version"],
        feature_snapshot_sha256=scored.feature_snapshot_sha256,
        members_sha256=scored.top10_members_sha256, runtime_sha256=runtime_sha,
        candidate=candidate_binding, market=market_binding, calendar=calendar,
        history=history, static_inputs=static, consumed_market_files=consumed,
        preimport_source_files=code_bindings, adapter_sha256=_sha(Path(__file__).resolve()),
        computation_performed=True, inference_performed=bool(runtime_rows), model_prediction_rows=len(runtime_rows),
        training_performed=False, legacy_ranking_read=False,
        legacy_action_or_statistics_read=False, secondary_models_loaded=False,
        production_enabled=False, forward_ledger_eligible=False,
        time_semantics="isolated_replay_recomputation_not_original_freeze",
        runtime_role="promotion_only_computation_reusing_retained_feature_functions")
    if input_manifest is not None:
        source.update(input_manifest_sha256=input_manifest_sha256,
            input_bundle_sha256=input_manifest["bundle_sha256"],
            input_collector_commit=input_manifest["source_commit"],
            input_collected_at_utc=input_manifest["collected_at_utc"],
            input_binding_basis="verified_manifest_and_immutable_upstream_bytes",
            root_market_or_candidate_read=False,
            minute_input_status="NOT_IN_INPUT_BUNDLE_NO_ROOT_FALLBACK")
    day = dict(signal_date=date, exec_date=exec_date, exit_date=exit_date,
        generated_at_utc=generated, generation_mode="REPLAY", source=source, rows=rows)
    receipt = dict(schema_version="dc20_forward_promotion_compute_receipt_v1", signal_date=date,
        promotion_pool_size=len(pool), selected_count=len(rows), gate_status="READY",
        pool_audit=pool_audit, runtime_identity_sha256=runtime_binding["runtime_identity_sha256"],
        runtime_raw_feature_columns=runtime_binding["runtime_raw_feature_columns"],
        runtime_raw_feature_columns_sha256=runtime_binding["runtime_raw_feature_columns_sha256"],
        day_sha256=canonical_sha256(day), runtime_sha256=runtime_sha,
        computation_performed=True, inference_performed=bool(runtime_rows),
        training_performed=False, publication_performed=False)
    return dict(day=day, runtime_rows=runtime_rows, runtime_columns=columns,
        feature_snapshot_sha256=scored.feature_snapshot_sha256, runtime_sha256=runtime_sha,
        receipt=receipt)
