"""Offline reader rejects hash-valid-but-inconsistent input contracts as well."""
import copy
import csv
import hashlib
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forward import bundle, inputs
from forward.tests.test_inputs import D, MARKET, NOW, PRED, ROOT, Source, csv_bytes


class VerifiedBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-bundle-tests.")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "bundle"
        self.manifest = inputs.collect_inputs(ROOT, self.path, D, PRED, MARKET,
                                             fetch=Source(), now_utc=NOW)
        self.digest = hashlib.sha256((self.path / "manifest.json").read_bytes()).hexdigest()

    def verify(self, digest=None):
        return bundle.VerifiedInputBundle(ROOT, self.path, self.digest if digest is None else digest)

    def resign(self):
        value = {key: val for key, val in self.manifest.items() if key != "bundle_sha256"}
        self.manifest["bundle_sha256"] = hashlib.sha256(bundle._canonical(value)).hexdigest()
        body = inputs._json_bytes(self.manifest)
        (self.path / "manifest.json").write_bytes(body)
        self.digest = hashlib.sha256(body).hexdigest()

    def rewrite(self, relative, body):
        (self.path / relative).write_bytes(body)
        self.manifest["files"][relative].update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        self.resign()

    def test_full_contract_and_original_bom_bytes(self):
        verified = self.verify()
        self.assertEqual((verified.signal_date, verified.exec_date, verified.exit_date),
                         (D, "20260909", "20260910"))
        self.assertEqual(verified.manifest_sha256, self.digest)
        self.assertEqual(verified.bundle_sha256, self.manifest["bundle_sha256"])
        self.assertEqual(verified.source_commit, self.manifest["source_commit"])
        self.assertEqual(verified.manifest, self.manifest)
        self.assertEqual(verified.manifest_bytes, (self.path / "manifest.json").read_bytes())
        self.assertTrue(verified.read_candidate_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(verified.read_calendar_bytes(), (ROOT / inputs.CALENDAR).read_bytes())
        self.assertEqual(len(verified.manifest["files"]), 191)
        self.assertEqual(verified.read_market_metadata(D)["resolved_trade_date"], D)
        self.assertIsNotNone(verified.read_market_bytes(D, "limit_up_tags"))
        path = self.manifest["market"][D]["required_tables"]["stock_basic"]
        self.assertFalse(verified.file_record(path)["date_scoped"])

    def test_collection_revision_need_not_equal_consuming_code_revision(self):
        self.manifest["source_commit"] = "3" * 40
        self.manifest["files"]["calendar/trade_cal_sse.csv"]["source_commit"] = "3" * 40
        self.resign()
        verified = self.verify()
        self.assertEqual(verified.source_commit, "3" * 40)
        self.assertFalse(verified.manifest["forward_ledger_eligible"])

    def test_external_manifest_digest_is_mandatory(self):
        for value in [None, "", "A" * 64, "short", "0" * 64]:
            with self.subTest(value=value), self.assertRaises(bundle.BundleError):
                bundle.VerifiedInputBundle(ROOT, self.path, value)

    def test_manifest_original_bytes_hash_not_just_parsed_json(self):
        with (self.path / "manifest.json").open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(bundle.BundleError, "external receipt"):
            self.verify()

    def test_self_hash_is_recomputed(self):
        self.manifest["status"] = "changed"
        raw = inputs._json_bytes(self.manifest)
        (self.path / "manifest.json").write_bytes(raw)
        with self.assertRaisesRegex(bundle.BundleError, "self hash"):
            self.verify(hashlib.sha256(raw).hexdigest())

    def test_true_flags_cannot_grant_natural_or_release_identity(self):
        for key in ["production_enabled", "model_inference_performed", "training_performed",
                    "ledger_written", "forward_ledger_eligible",
                    "historical_point_in_time_acquisition_claimed"]:
            with self.subTest(key=key):
                self.manifest[key] = True
                self.resign()
                with self.assertRaises(bundle.BundleError):
                    self.verify()
                self.manifest[key] = False

    def test_strict_T_T1_sessions_schema_and_collection_close(self):
        variants = [("exec_date", "20260908"), ("exit_date", "20260911"),
                    ("history_sessions", self.manifest["history_sessions"][1:]),
                    ("market_sessions", self.manifest["market_sessions"][:-1]),
                    ("schema_version", "old"), ("status", "READY"),
                    ("collected_at_utc", "2026-09-08T06:59:59+00:00"),
                    ("collected_at_utc", "2026-09-09T00:00:00")]
        original = copy.deepcopy(self.manifest)
        for key, value in variants:
            with self.subTest(key=key):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                self.resign()
                with self.assertRaises(bundle.BundleError):
                    self.verify()

    def test_source_identity_cannot_be_relabelled_latest_or_other_repo(self):
        path = self.manifest["candidate_path"]
        original = copy.deepcopy(self.manifest)
        for key, value in [("source_commit", "main"),
                           ("source_repository", "njedu2023-prog/top10-decision"),
                           ("source_path", "outputs/decisio/pred_decisio_latest.csv"),
                           ("source_url", f"https://raw.githubusercontent.com/{inputs.PRED_REPO}/main/file.csv")]:
            with self.subTest(key=key):
                self.manifest = copy.deepcopy(original)
                self.manifest["files"][path][key] = value
                self.resign()
                with self.assertRaises(bundle.BundleError):
                    self.verify()

    def test_raw_file_hash_and_size_must_match_original_bytes(self):
        path = self.manifest["candidate_path"]
        with (self.path / path).open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(bundle.BundleError, "original bytes/hash"):
            self.verify()

    def test_hash_valid_csv_wrong_date_or_duplicate_stock_still_blocks(self):
        path = self.manifest["market"][D]["required_tables"]["daily"]
        original_bytes = (self.path / path).read_bytes()
        rows = list(csv.DictReader(io.StringIO(original_bytes.decode("utf-8-sig"))))
        rows[0]["trade_date"] = "20260907"
        self.rewrite(path, csv_bytes(rows[0].keys(), rows))
        with self.assertRaisesRegex(bundle.BundleError, "exact-date"):
            self.verify()
        rows[0]["trade_date"] = D
        self.rewrite(path, csv_bytes(rows[0].keys(), rows * 2))
        with self.assertRaisesRegex(bundle.BundleError, "duplicate stock"):
            self.verify()

    def test_hash_valid_bad_metadata_count_and_record_profile_block(self):
        relative = self.manifest["market"][D]["metadata_path"]
        raw = (self.path / relative).read_bytes()
        value = json.loads(raw)
        value["jobs"][0]["rows"] = 3
        self.rewrite(relative, json.dumps(value).encode())
        with self.assertRaisesRegex(bundle.BundleError, "row count mismatch"):
            self.verify()
        self.rewrite(relative, raw)
        self.manifest["files"][self.manifest["candidate_path"]]["row_count"] = 999
        self.resign()
        with self.assertRaisesRegex(bundle.BundleError, "reconstructed"):
            self.verify()

    def test_candidate_generated_after_collection_or_wrong_T_rejected(self):
        path = self.manifest["candidate_path"]
        original = (self.path / path).read_bytes()
        for key, value in [("verify_date", "20260910"),
                           ("generated_at_utc", "2026-09-10T00:00:00Z")]:
            with self.subTest(key=key):
                rows = list(csv.DictReader(io.StringIO(original.decode("utf-8-sig"))))
                rows[0][key] = value
                self.rewrite(path, csv_bytes(rows[0].keys(), rows))
                with self.assertRaises(bundle.BundleError):
                    self.verify()

    def test_traversal_or_undeclared_file_is_rejected_without_reading_contents(self):
        self.manifest["market"][D]["required_tables"]["daily"] = "../../secret"
        self.resign()
        original_read = bundle._regular_bytes
        observed = []
        def read(descriptor, relative, limit):
            observed.append(relative)
            return original_read(descriptor, relative, limit)
        with patch.object(bundle, "_regular_bytes", side_effect=read), self.assertRaises(bundle.BundleError):
            self.verify()
        self.assertEqual(observed, ["manifest.json"])

    def test_extra_file_or_directory_and_missing_file_are_not_ignored(self):
        extra = self.path / "latest.csv"
        extra.write_text("do not read this")
        with self.assertRaisesRegex(bundle.BundleError, "undeclared"):
            self.verify()
        extra.unlink()
        (self.path / "future").mkdir()
        with self.assertRaisesRegex(bundle.BundleError, "undeclared"):
            self.verify()
        (self.path / "future").rmdir()
        (self.path / self.manifest["candidate_path"]).unlink()
        with self.assertRaisesRegex(bundle.BundleError, "inventory"):
            self.verify()

    def test_manifest_cannot_declare_new_out_of_window_or_backslash_file(self):
        original = copy.deepcopy(self.manifest)
        for path in ["market/2026/20260909/daily.csv", "market\\daily.csv", "../outside.csv", "/abs.csv"]:
            with self.subTest(path=path):
                self.manifest = copy.deepcopy(original)
                self.manifest["files"][path] = copy.deepcopy(self.manifest["files"][self.manifest["candidate_path"]])
                self.resign()
                with self.assertRaisesRegex(bundle.BundleError, "declared file set"):
                    self.verify()

    def test_regular_files_only_no_symlinks_hardlinks_or_fifo(self):
        relative = self.manifest["candidate_path"]
        target = self.path / relative
        source = self.path.parent / "outside.csv"
        body = target.read_bytes()
        source.write_bytes(body)
        target.unlink()
        target.symlink_to(source)
        with self.assertRaises(bundle.BundleError):
            self.verify()
        target.unlink()
        os.link(source, target)
        with self.assertRaises(bundle.BundleError):
            self.verify()
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(bundle.BundleError):
            self.verify()

    def test_bundle_root_symlink_relative_and_repo_inside_rejected(self):
        linked = self.path.parent / "linked"
        linked.symlink_to(self.path, target_is_directory=True)
        for path in [linked, Path("relative"), ROOT / "forward"]:
            with self.subTest(path=path), self.assertRaises(bundle.BundleError):
                bundle.VerifiedInputBundle(ROOT, path, self.digest)

    def test_file_and_manifest_budgets_and_boolean_size_rejected(self):
        with patch.object(bundle, "MAX_MANIFEST_BYTES", 2), self.assertRaises(bundle.BundleError):
            self.verify()
        with patch.object(bundle, "MAX_BUNDLE_FILES", 1), self.assertRaises(bundle.BundleError):
            self.verify()
        self.manifest["files"][self.manifest["candidate_path"]]["bytes"] = True
        self.resign()
        with self.assertRaises(bundle.BundleError):
            self.verify()

    def test_malformed_optional_record_fails_as_bundle_error(self):
        self.manifest["market"][D]["optional_tables"]["limit_up_tags"] = None
        self.resign()
        with self.assertRaises(bundle.BundleError):
            self.verify()

    def test_optional_status_must_agree_with_metadata_presence(self):
        entry = self.manifest["market"][D]["optional_tables"]["limit_up_tags"]
        path = entry["path"]
        del self.manifest["files"][path]
        (self.path / path).unlink()
        self.manifest["market"][D]["optional_tables"]["limit_up_tags"] = {"status": "NOT_DECLARED_AVAILABLE"}
        self.resign()
        with self.assertRaisesRegex(bundle.BundleError, "declares available"):
            self.verify()

    def test_optional_unavailable_is_explicit_not_a_fabricated_input(self):
        missing_path = Path(self.temp.name).resolve() / "missing"
        def mutate(table, date, body):
            if table == "limit_up_tags" and date == D:
                raise inputs.FetchError("missing", status=404)
            return body
        inputs.collect_inputs(ROOT, missing_path, D, PRED, MARKET, fetch=Source(mutation=mutate), now_utc=NOW)
        digest = hashlib.sha256((missing_path / "manifest.json").read_bytes()).hexdigest()
        verified = bundle.VerifiedInputBundle(ROOT, missing_path, digest)
        self.assertIsNone(verified.read_market_bytes(D, "limit_up_tags"))
        self.assertEqual(verified.manifest["optional_gaps"], [dict(signal_date=D, table="limit_up_tags", reason="DECLARED_BUT_UNAVAILABLE")])

    def test_not_declared_optional_and_missing_numeric_stay_missing(self):
        missing_path = Path(self.temp.name).resolve() / "not-declared"
        def mutate(table, date, body):
            if table == "daily":
                rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                rows[0]["vol"] = ""
                return csv_bytes(rows[0].keys(), rows)
            return body
        inputs.collect_inputs(ROOT, missing_path, D, PRED, MARKET,
                              fetch=Source(optional=False, mutation=mutate), now_utc=NOW)
        digest = hashlib.sha256((missing_path / "manifest.json").read_bytes()).hexdigest()
        verified = bundle.VerifiedInputBundle(ROOT, missing_path, digest)
        self.assertIsNone(verified.read_market_bytes(D, "limit_up_tags"))
        rows = list(csv.DictReader(io.StringIO(verified.read_market_bytes(D, "daily").decode("utf-8-sig"))))
        self.assertEqual(rows[0]["vol"], "")
        self.assertEqual(len(verified.manifest["optional_gaps"]), 63)

    def test_request_counts_downloaded_bytes_and_calendar_binding_recomputed(self):
        original = copy.deepcopy(self.manifest)
        for key, value in [("request_count", 127), ("downloaded_bytes", 1),
                           ("optional_tables_complete", False)]:
            with self.subTest(key=key):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                self.resign()
                with self.assertRaises(bundle.BundleError):
                    self.verify()
        self.manifest = original
        self.manifest["files"]["calendar/trade_cal_sse.csv"]["git_blob_sha1"] = "9" * 40
        self.resign()
        with self.assertRaises(bundle.BundleError):
            self.verify()

    def test_no_second_read_after_verify_and_public_dict_cannot_mutate_source(self):
        verified = self.verify()
        original = verified.read_candidate_bytes()
        (self.path / self.manifest["candidate_path"]).write_bytes(b"mutated after validation")
        exported = verified.manifest
        exported["market"].clear()
        record = verified.file_record(self.manifest["candidate_path"])
        record["sha256"] = "0" * 64
        metadata = verified.read_market_metadata(D)
        metadata["resolved_trade_date"] = "wrong"
        with patch.object(bundle, "_regular_bytes", side_effect=AssertionError("second read")):
            self.assertEqual(verified.read_candidate_bytes(), original)
            self.assertEqual(verified.read_market_metadata(D)["resolved_trade_date"], D)
            self.assertNotEqual(verified.file_record(self.manifest["candidate_path"])["sha256"], "0" * 64)
            self.assertIsNotNone(verified.read_market_bytes(D, "daily"))

    def test_read_scope_never_falls_back_to_future_latest_or_old_outputs(self):
        verified = self.verify()
        for path in ["latest.csv", "../../outputs/decision/latest.json", "manifest.json"]:
            with self.subTest(path=path), self.assertRaises(bundle.BundleError):
                verified.get_bytes(path)
        for date, table in [("20260909", "daily"), ("20260810", "daily"),
                            (D, "unknown"), (D, "minute_1m")]:
            with self.subTest(date=date, table=table), self.assertRaises(bundle.BundleError):
                verified.read_market_bytes(date, table)
        with self.assertRaises(bundle.BundleError):
            verified.read_market_metadata("20260909")

    def test_no_network_no_repo_data_other_than_pinned_calendar(self):
        old_open = io.open
        observed = []
        def check(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(file)).resolve()
                if path.is_relative_to(ROOT):
                    relative = path.relative_to(ROOT).as_posix()
                    observed.append(relative)
                    if relative != inputs.CALENDAR or any(flag in mode for flag in "wax+"):
                        raise AssertionError(f"unexpected repository access: {relative}")
            return old_open(file, mode, *args, **kwargs)
        with patch("io.open", side_effect=check), patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
            self.verify()
        self.assertEqual(set(observed), {inputs.CALENDAR})


if __name__ == "__main__":
    unittest.main()
