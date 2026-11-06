"""Read-only historical DC20 model/evidence replay, never a natural freeze.

Run this same script in separate processes against baseline and cleaned code,
with the SAME source root. The report and temporary original-blob copies are
written outside both checkouts; no repository file is written.
No network, training, dispatch, market collection, publication or clock override.
Timings measure local replay stages, not GitHub queue or upstream/Pages latency.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from collections.abc import Mapping

sys.dont_write_bytecode = True
PREFIX = 'work/profit_1000_upgrade/'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def plain(value):
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def canonical(value):
    return sha(json.dumps(plain(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def first_difference(actual, expected, path='$'):
    if type(actual) is not type(expected):
        return f'{path}: {type(actual).__name__} != {type(expected).__name__}'
    if isinstance(actual, dict):
        if actual.keys() != expected.keys():
            return f'{path}: object keys differ'
        for key in actual:
            if actual[key] != expected[key]:
                return first_difference(actual[key], expected[key], path+'.'+key)
    elif isinstance(actual, list):
        if len(actual) != len(expected):
            return f'{path}: list lengths differ'
        for i, value in enumerate(actual):
            if value != expected[i]:
                return first_difference(value, expected[i], path+f'[{i}]')
    elif actual != expected:
        return f'{path}: {actual!r} != {expected!r}'
    return ''


def compare_frozen(actual, expected, *, tolerance=1e-12):
    """Allow only reported binary64 drift; identities/ranks/schema stay exact."""
    result = {'exact_match': actual == expected, 'absolute_float_tolerance': tolerance,
              'float_differences': 0, 'max_float_absolute_delta': 0.0}
    def visit(a, b, path='$'):
        require(type(a) is type(b), 'FROZEN_VALUE_TYPE_CHANGED:'+path)
        if isinstance(a, dict):
            require(a.keys() == b.keys(), 'FROZEN_OBJECT_KEYS_CHANGED:'+path)
            for key in a:
                visit(a[key], b[key], path+'.'+key)
        elif isinstance(a, list):
            require(len(a) == len(b), 'FROZEN_LIST_LENGTH_CHANGED:'+path)
            for i, value in enumerate(a):
                visit(value, b[i], path+f'[{i}]')
        elif isinstance(a, float):
            require(math.isfinite(a) and math.isfinite(b), 'NONFINITE_FROZEN_VALUE:'+path)
            delta = abs(a-b)
            result['float_differences'] += int(delta != 0)
            result['max_float_absolute_delta'] = max(result['max_float_absolute_delta'], delta)
            require(delta <= tolerance, 'FROZEN_NUMERIC_VALUE_CHANGED:'+first_difference(a,b,path))
        else:
            require(a == b, 'FROZEN_VALUE_CHANGED:'+first_difference(a,b,path))
    visit(actual, expected)
    return result


def safe_path(root, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute()
            and '..' not in Path(relative).parts and Path(relative).as_posix() == relative,
            'EXACT_RELATIVE_INPUT_PATH_REQUIRED')
    path = root / relative
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
            'MISSING_OR_ALIASED_REPLAY_INPUT:'+relative)
    return path


def root_path(value):
    supplied = value.absolute()
    require(not any(p.is_symlink() for p in (supplied, *supplied.parents)),
            'ALIASED_REPLAY_ROOT_REJECTED')
    root = supplied.resolve(strict=True)
    require(root.is_dir(), 'EXISTING_REPLAY_ROOT_REQUIRED')
    return root


@contextmanager
def offline():
    original = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def denied(*args, **kwargs):
        raise ValueError('NETWORK_FORBIDDEN_IN_HISTORICAL_REPLAY')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = original


class Replay:
    def __init__(self, source_root, code_root, day, label):
        self.source_root, self.code_root, self.day = source_root, code_root, day
        self.protected = {}
        self.report = {
            'schema_version': 'dc20_daily_cleanup_historical_replay_v1',
            'status': 'RUNNING', 'label': label, 'signal_date': day,
            'started_at_host_utc': datetime.now(timezone.utc).isoformat(),
            'evidence_tier': 'HISTORICAL_READ_ONLY_RECALCULATION',
            'natural_freeze_issued': False, 'existing_freeze_used_as_comparison': True,
            'market_data_collected': False, 'model_training_performed': False,
            'formal_files_written': 0, 'network_calls_performed': 0,
            'workflow_dispatched': False, 'actual_execution_claimed': False,
            'timing_scope': 'LOCAL_REPLAY_ONLY_EXCLUDES_UPSTREAM_RUNS_QUEUE_AND_PAGES_DEPLOYMENT',
            'source_root': str(source_root), 'code_root': str(code_root),
            'stages': [], 'runtime': {'python': sys.version.split()[0]},
        }
        for package in ('numpy', 'pandas', 'scikit-learn', 'joblib'):
            try:
                self.report['runtime'][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                self.report['runtime'][package] = 'MISSING'

    def read(self, relative, *, expected=None):
        body = safe_path(self.source_root, relative).read_bytes()
        value = sha(body)
        require(expected is None or value == expected, 'REPLAY_INPUT_SHA_MISMATCH:'+relative)
        require(relative not in self.protected or self.protected[relative] == value,
                'REPLAY_INPUT_CHANGED:'+relative)
        self.protected[relative] = value
        return body

    def doc(self, relative, *, expected=None):
        value = json.loads(self.read(relative, expected=expected))
        require(isinstance(value, dict), 'REPLAY_OBJECT_REQUIRED:'+relative)
        return value

    @contextmanager
    def stage(self, name):
        item = {'name': name, 'status': 'RUNNING'}
        self.report['stages'].append(item)
        start = time.perf_counter()
        try:
            yield item
            item['status'] = 'PASS'
        except Exception as exc:
            item.update(status='FAIL', error_type=type(exc).__name__, reason=str(exc))
            raise
        finally:
            item['seconds'] = round(time.perf_counter()-start, 6)

    def run(self, temporary_root):
        day = self.day
        with self.stage('original_frozen_inputs_and_hashes') as stage:
            frozen_path = PREFIX+f'candidate_natural_forward/day_{day}.json'
            workflow = self.doc(PREFIX+f'candidate_natural_forward/workflow_{day}.json')
            frozen = self.doc(frozen_path, expected=workflow['snapshot_file_sha256'])
            imported = self.doc(PREFIX+f'candidate_natural_forward/p0_sources_{day}.json',
                                expected=workflow['source_import_receipt_sha256'])
            context = self.doc(PREFIX+f'candidate_natural_evidence/{day}/context.json')
            require(context['snapshot_file_sha256'] == workflow['snapshot_file_sha256']
                    and context['freeze_run_id'] == workflow['run_id']
                    and frozen['signal_date'] == imported['signal_date'] == context['signal_date'] == day,
                    'FROZEN_WORKFLOW_OBSERVER_DATE_BINDING_CHANGED')
            expected = imported['expected_p0_sha256']
            require(expected == frozen['input_arguments']['expected_p0_sha256'], 'FROZEN_P0_BINDING_CHANGED')
            p0_paths = {'receipt': f'outputs/decision/primary_d_receipt_{day}.json',
                        'runtime_features': f'outputs/decision/primary_d_runtime_features_{day}.csv',
                        'three_rank_json': f'outputs/decision/three_rank_top10_{day}.json',
                        'three_rank_csv': f'outputs/decision/three_rank_top10_{day}.csv'}
            for key, path in p0_paths.items():
                self.read(path, expected=expected[key])
            receipt, p0 = self.doc(p0_paths['receipt']), self.doc(p0_paths['three_rank_json'])
            require(receipt['generation_mode'] == 'NATURAL' and receipt['primary_status'] == 'READY'
                    and p0['signal_date'] == day, 'EXISTING_NATURAL_P0_REQUIRED')
            model_path = PREFIX+'candidate_natural_model/evaluation.json'
            evaluation = self.doc(model_path, expected=frozen['model_evaluation']['sha256'])
            inputs = receipt['inputs']
            for item in inputs['committed_static_inputs']['model']:
                self.read(item['path'], expected=item['sha256'])
            stage.update(original_p0_cas_commit=imported['published_git_sha'],
                         original_code_head=inputs['git_head'],
                         pred_commit=inputs['candidate']['resolved_commit'],
                         market_commit=inputs['market']['resolved_commit'],
                         original_freeze_run=workflow['run_id'],
                         source_file_count=len(imported['source_file_bindings']))
            self.report.update(exec_date=p0['exec_date'], exit_date=p0['exit_date'])

        with self.stage('independent_original_evidence_capsule_replay') as stage:
            from work.profit_1000_upgrade import candidate_natural_evidence as capture
            directory = PREFIX+f'candidate_natural_evidence/{day}/'
            manifest_raw = self.read(directory+'manifest.json', expected=context['manifest_sha256'])
            manifest = json.loads(manifest_raw)
            bodies = {item['path']: self.read(directory+item['path'], expected=item['sha256'])
                      for item in manifest['files']}
            verified = capture.verify_materials(manifest_raw, bodies, expected_manifest_sha256=context['manifest_sha256'])
            require(verified['snapshot_file_sha256'] == workflow['snapshot_file_sha256']
                    and verified['signal_date'] == day, 'CAPSULE_FROZEN_SNAPSHOT_CHANGED')
            stage.update(body_count=len(bodies), original_source_count=len(verified['original_source_bindings']),
                         provenance_kind=verified['p0_provenance_kind'], manifest_sha256=sha(manifest_raw))

        with self.stage('original_registered_sources_from_verified_capsule') as stage:
            # Mutable sync metadata may have been refreshed since D. Recover
            # its exact original blob from the already verified observation,
            # never relabel current bytes or overwrite the repository copy.
            require(verified['original_source_bindings'] == imported['source_file_bindings'],
                    'CAPSULE_AND_IMPORT_SOURCE_BINDINGS_CHANGED')
            source_map, rebound = {}, []
            for binding in imported['source_file_bindings']:
                path, expected_sha = binding['path'], binding['sha256']
                local = safe_path(self.source_root, path)
                if sha(local.read_bytes()) == expected_sha:
                    self.read(path, expected=expected_sha)
                    continue
                api_path = '/repos/njedu2023-prog/DC20/git/blobs/'+binding['git_blob_sha1']
                observations = [row for row in verified['http_observations'] if row['api_path'] == api_path]
                require(observations, 'ORIGINAL_CAPSULE_BLOB_MISSING:'+path)
                values = [json.loads(bodies[row['retained_body']['path']]) for row in observations]
                require(all(value == values[0] for value in values), 'ORIGINAL_CAPSULE_BLOB_CONFLICT:'+path)
                blob = values[0]
                require(blob.get('encoding') == 'base64' and blob.get('sha') == binding['git_blob_sha1'],
                        'ORIGINAL_CAPSULE_BLOB_ENCODING_CHANGED:'+path)
                original = base64.b64decode(blob['content'])
                require(len(original) == binding['bytes'] and sha(original) == expected_sha
                        and hashlib.sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()
                            == binding['git_blob_sha1'], 'ORIGINAL_CAPSULE_BLOB_SHA_CHANGED:'+path)
                destination = temporary_root/path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(original)
                source_map[path] = str(destination)
                rebound.append({'path':path, 'sha256':expected_sha, 'bytes':len(original)})
            stage.update(registered_source_count=len(imported['source_file_bindings']),
                         exact_original_blob_mappings=rebound)

        with self.stage('production_promotion_only_rescore') as stage:
            import pandas as pd
            from top10decision.decision.three_engine_models import load_promotion_only_artifacts, score_three_engine_snapshot
            runtime = pd.read_csv(self.source_root/p0_paths['runtime_features'], dtype={'ts_code': str},
                                  float_precision='round_trip')
            loaded = load_promotion_only_artifacts(self.source_root/inputs['model_validation']['path'], root=self.source_root)
            scored = score_three_engine_snapshot(runtime, loaded, signal_date=day)
            require(scored.model_metadata['promotion']['status'] == 'READY', 'PROMOTION_REPLAY_NOT_READY')
            actual = scored.rows.sort_values('promotion_rank', kind='stable')
            ordered = runtime.sort_values('promotion_rank', kind='stable')
            require(actual['ts_code'].tolist() == ordered['ts_code'].tolist(), 'PROMOTION_RANK_CHANGED')
            max_delta = max((abs(float(a)-float(b)) for a,b in zip(actual['predicted_promotion_probability'],
                ordered['predicted_promotion_probability'])), default=0.0)
            require(max_delta <= 1e-12, 'PROMOTION_PROBABILITY_CHANGED')
            require(scored.top10_members_sha256 == p0['top10_members_sha256'], 'PROMOTION_TOP10_MEMBERS_CHANGED')
            require(actual.head(len(p0['rows']))['ts_code'].tolist() == [r['ts_code'] for r in p0['rows']],
                    'PUBLISHED_PROMOTION_ORDER_CHANGED')
            stage.update(pool_count=len(runtime), top3=actual.head(3)['ts_code'].tolist(),
                         max_probability_absolute_delta=max_delta,
                         feature_snapshot_matches=scored.feature_snapshot_sha256 == p0['feature_snapshot_sha256'],
                         top10_members_sha256=scored.top10_members_sha256)

        with self.stage('production_bound_p0_feature_projection') as stage:
            from work.profit_1000_upgrade import candidate_d_source_adapter as adapter
            projected = plain(adapter.project_bound_p0_d(self.source_root, signal_date=day,
                expected_p0_sha256=expected, source_path_map=source_map))
            projection = projected['projection']
            comparison = compare_frozen(projection, frozen['D_source_evidence']['projection'])
            stage.update(candidate_count=projection['candidate_count'],
                         registered_daily_file_count=projected['registered_daily_file_count'],
                         projection_status=projected['status'], projection_sha256=canonical(projection),
                         individual_exclusions=projection['blocked_candidates'], comparison=comparison)

        with self.stage('production_fixed_ridge_rescore') as stage:
            from work.profit_1000_upgrade import candidate_eligible_pool as eligible
            prediction = eligible.predict_eligible(evaluation['candidate_model'], projection,
                expected_model_sha256=frozen['model_canonical_sha256'])
            eligible.validate_prediction(frozen['prediction'], frozen['D_source_evidence']['projection'],
                expected_model_sha256=frozen['model_canonical_sha256'])
            # A one-ULP feature difference changes a cryptographic digest. Check
            # each digest against its own fully compared values; never apply a
            # numeric tolerance to dates, ranks, model hashes or arbitrary text.
            actual_values, frozen_values = dict(prediction), dict(frozen['prediction'])
            actual_feature_sha, frozen_feature_sha = (values.pop('full_D_rows_sha256')
                                                      for values in (actual_values, frozen_values))
            require(actual_feature_sha == canonical(prediction['promotion_rows'])
                    and frozen_feature_sha == canonical(frozen['prediction']['promotion_rows']),
                    'DERIVED_FEATURE_FINGERPRINT_CHANGED')
            comparison = compare_frozen(actual_values, frozen_values)
            stage.update(eligible_candidate_count=prediction['eligible_candidate_count'],
                         profit_top2=prediction['candidate_slots'],
                         model_canonical_sha256=frozen['model_canonical_sha256'],
                         prediction_sha256=canonical(prediction), exact_prediction_match=prediction == frozen['prediction'],
                         comparison=comparison, derived_feature_fingerprint={
                             'replayed':actual_feature_sha, 'frozen':frozen_feature_sha,
                             'each_verified_against_own_values':True,
                             'exact_match':actual_feature_sha == frozen_feature_sha})
            self.report['promotion_top3'] = frozen['promotion_top3']
            self.report['profit_top2'] = prediction['candidate_slots']
            self.report['business_signature_sha256'] = canonical({'signal_date':day,
                'promotion':actual[['ts_code','promotion_rank','predicted_promotion_probability']].to_dict('records'),
                'candidate_prediction':prediction})

        with self.stage('all_read_inputs_remain_unchanged') as stage:
            for relative, value in self.protected.items():
                require(sha(safe_path(self.source_root, relative).read_bytes()) == value,
                        'READ_ONLY_REPLAY_INPUT_CHANGED:'+relative)
            stage.update(verified_file_count=len(self.protected))
            self.report['input_inventory_sha256'] = canonical(self.protected)
            self.report['input_hashes'] = dict(sorted(self.protected.items()))
        self.report['status'] = 'PASS'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Use one checkout for both code and source inputs')
    parser.add_argument('--code-root', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--signal-date', default='20261106')
    parser.add_argument('--label', default='historical-replay')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    require(re.fullmatch(r'[0-9]{8}', args.signal_date), 'EXACT_SIGNAL_DATE_REQUIRED')
    datetime.strptime(args.signal_date, '%Y%m%d')
    require((args.code_root or args.root) is not None and (args.source_root or args.root) is not None,
            'CODE_AND_SOURCE_ROOTS_REQUIRED')
    code, source, output = root_path(args.code_root or args.root), root_path(args.source_root or args.root), args.output.absolute()
    require(not output.resolve().is_relative_to(code.resolve()) and not output.resolve().is_relative_to(source.resolve()),
            'REPORT_OUTPUT_MUST_BE_OUTSIDE_BOTH_CHECKOUTS')
    sys.path[:0] = [str(code), str(code/'src')]
    runner = Replay(source, code, args.signal_date, args.label)
    start = time.perf_counter()
    try:
        with offline(), tempfile.TemporaryDirectory(prefix='dc20-historical-replay-') as temporary:
            runner.run(Path(temporary).resolve())
    except Exception as exc:
        runner.report.update(status='FAIL', error_type=type(exc).__name__, reason=str(exc))
    finally:
        runner.report['seconds_total'] = round(time.perf_counter()-start, 6)
        runner.report['finished_at_host_utc'] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(runner.report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        print(json.dumps({k:runner.report[k] for k in ('status','signal_date','label','seconds_total')}
                         | {'report':str(output), 'reason':runner.report.get('reason','')}, ensure_ascii=False))
    return 0 if runner.report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
