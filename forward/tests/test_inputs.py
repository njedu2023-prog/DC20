"""Independent input acquisition uses immutable sources, never old rankings."""
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from forward import inputs

ROOT = Path(__file__).resolve().parents[2]
D = "20260908"
T = "20260909"
PRED = "1" * 40
MARKET = "2" * 40
NOW = datetime(2026, 9, 9, 0, tzinfo=timezone.utc)


def csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def table_bytes(table, date):
    row = {key: "1" for key in inputs.FIELDS[table]}
    row.update(ts_code="600000.SH", trade_date=date, name="公司甲", industry="行业甲",
               list_date="20000101", verify_date=T, generated_at_utc="2026-09-08T15:17:12Z")
    columns = sorted(inputs.FIELDS[table])
    return csv_bytes(columns, [{key: row[key] for key in columns}])


def metadata(date, optional=True):
    jobs = [{"key": table, "status": "ok", "error": None,
             "rows": 2 if table == "limit_list_d" else 1,
             "kwargs": {} if table == "stock_basic" else {"trade_date": date}}
            for table in inputs.REQUIRED_TABLES]
    result = {"resolved_trade_date": date, "requested_trade_date": None,
              "generated_at_bj": f"{date[:4]}-{date[4:6]}-{date[6:]} 22:07:41",
              "jobs": jobs, "derived": {"limit_list_d_policy": {
                  "policy": "CLOSE_EQ_UP_LIMIT", "trade_date": date,
                  "input_rows": 2, "output_rows": 1}}}
    if optional:
        result["derived"]["hot_board_tags"] = {"tagged": 1, "hot_boards": 1}
        result["intraday_features"] = {"ok": True, "rows": 1}
    return result


class Source:
    def __init__(self, optional=True, mutation=None):
        self.urls = []
        self.optional = optional
        self.mutation = mutation

    def __call__(self, url):
        self.urls.append(url)
        inputs._validate_url(url)
        if "/a-top10/" in url:
            assert f"/{PRED}/outputs/decisio/pred_decisio_{D}.csv" in url
            table, date = "candidate", D
            body = table_bytes(table, date)
        else:
            assert f"/{MARKET}/data/raw/2026/" in url
            date, file = url.split("/")[-2:]
            table = file.split(".")[0]
            body = (json.dumps(metadata(date, self.optional)).encode() if table == "_meta"
                    else table_bytes(table, date))
        return self.mutation(table, date, body) if self.mutation else body


class InputCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-input-tests.")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve() / "bundle"

    def collect(self, source=None, **kwargs):
        return inputs.collect_inputs(ROOT, self.output, D, PRED, MARKET,
                                     fetch=source or Source(), now_utc=NOW, **kwargs)

    def fail_collect(self, source, message=None):
        with self.assertRaises(inputs.InputError) as caught:
            self.collect(source)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertFalse((self.output / "manifest.json").exists())

    def test_full_bundle_original_bytes_provenance_and_no_release(self):
        source = Source()
        result = self.collect(source)
        self.assertEqual((result["signal_date"], result["exec_date"], result["exit_date"]),
                         (D, T, "20260910"))
        self.assertEqual(result["history_sessions"][0], "20260811")
        self.assertEqual(len(result["history_sessions"]), 20)
        self.assertEqual(len(result["market_sessions"]), 21)
        self.assertEqual(result["request_count"], 190)
        self.assertEqual(len(set(source.urls)), 190)
        self.assertEqual(result["optional_gaps"], [])
        self.assertTrue(result["optional_tables_complete"])
        self.assertEqual(len(result["files"]), 191)
        self.assertEqual(json.loads((self.output / "manifest.json").read_bytes()), result)
        for path, record in result["files"].items():
            body = (self.output / path).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), record["sha256"])
            self.assertEqual(len(body), record["bytes"])
        candidate = self.output / result["candidate_path"]
        self.assertTrue(candidate.read_bytes().startswith(b"\xef\xbb\xbf"))
        stock = result["files"][result["market"][D]["required_tables"]["stock_basic"]]
        self.assertFalse(stock["date_scoped"])
        self.assertEqual(stock["snapshot_date"], D)
        self.assertNotIn("trade_date", stock["columns"])
        # limit_list_d is derived from 2 raw rows but has 1 actual CSV row.
        limit = result["files"][result["market"][D]["required_tables"]["limit_list_d"]]
        self.assertEqual(limit["row_count"], 1)
        for flag in ("model_inference_performed", "training_performed", "ledger_written",
                     "production_enabled", "forward_ledger_eligible",
                     "historical_point_in_time_acquisition_claimed"):
            self.assertIs(result[flag], False)
        canonical = dict(result)
        expected = canonical.pop("bundle_sha256")
        self.assertEqual(hashlib.sha256(json.dumps(canonical, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(), expected)

    def test_optional_absence_is_explicit_and_never_falls_back(self):
        source = Source(optional=False)
        result = self.collect(source)
        self.assertEqual(result["request_count"], 127)
        self.assertEqual(len(result["optional_gaps"]), 63)
        self.assertFalse(result["optional_tables_complete"])
        self.assertTrue(all(gap["reason"] == "NOT_DECLARED_AVAILABLE" for gap in result["optional_gaps"]))
        self.assertFalse(any("latest" in url or "/DC20/" in url for url in source.urls))

    def test_declared_optional_404_is_preserved_not_filled(self):
        def missing(table, date, body):
            if table == "limit_up_tags" and date == D:
                raise inputs.FetchError("not found", status=404)
            return body
        result = self.collect(Source(mutation=missing))
        self.assertEqual(result["market"][D]["optional_tables"]["limit_up_tags"],
                         {"status": "DECLARED_BUT_UNAVAILABLE", "http_status": 404})
        self.assertNotIn(f"market/2026/{D}/limit_up_tags.csv", result["files"])

    def test_required_404_blocks_before_any_bundle_write(self):
        def missing(table, date, body):
            if table == "daily":
                raise inputs.FetchError("not found", status=404)
            return body
        self.fail_collect(Source(mutation=missing), "20260811/daily.csv (HTTP 404)")
        self.assertFalse(self.output.exists())

    def test_fetch_error_diagnostic_contains_path_but_not_fetcher_message(self):
        source = Source(mutation=lambda *args: (_ for _ in ()).throw(
            inputs.FetchError("Authorization: Bearer secret", status=403)))
        with self.assertRaises(inputs.FetchError) as caught:
            self.collect(source)
        message = str(caught.exception)
        self.assertIn(f"{inputs.PRED_REPO}@{PRED}/outputs/decisio/pred_decisio_{D}.csv", message)
        self.assertIn("HTTP 403", message)
        self.assertNotIn("secret", message)
        self.assertEqual(caught.exception.status, 403)

    def test_invalid_required_job_date_and_counts_block(self):
        variants = [lambda meta: meta.update(resolved_trade_date="20260907"),
                    lambda meta: meta["jobs"][0].update(status="error"),
                    lambda meta: meta["jobs"][0].update(rows=2),
                    lambda meta: meta["jobs"][0]["kwargs"].update(trade_date="20260907"),
                    lambda meta: meta["derived"]["limit_list_d_policy"].update(output_rows=3)]
        for change in variants:
            with self.subTest(change=change):
                def mutation(table, date, body):
                    if table == "_meta":
                        value = json.loads(body)
                        change(value)
                        return json.dumps(value).encode()
                    return body
                self.fail_collect(Source(mutation=mutation))

    def test_csv_wrong_date_duplicate_code_invalid_numeric_blocks(self):
        variants = [lambda row: row.update(trade_date="20260907"),
                    lambda row: row.update(ts_code="bad"),
                    lambda row: row.update(vol="-1"),
                    lambda row: row.update(open="Infinity"),
                    lambda row: row.update(close="true")]
        for change in variants:
            with self.subTest(change=change):
                def mutation(table, date, body):
                    if table == "daily":
                        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                        change(rows[0])
                        return csv_bytes(rows[0].keys(), rows)
                    return body
                self.fail_collect(Source(mutation=mutation))
        body = table_bytes("daily", D)
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        with self.assertRaisesRegex(inputs.InputError, "duplicate stock"):
            inputs._csv_profile(csv_bytes(rows[0].keys(), rows * 2), "daily", D)

    def test_missing_numeric_cell_is_not_coerced_to_zero(self):
        def mutation(table, date, body):
            if table == "daily":
                rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                rows[0]["vol"] = ""
                return csv_bytes(rows[0].keys(), rows)
            return body
        result = self.collect(Source(optional=False, mutation=mutation))
        path = result["market"][D]["required_tables"]["daily"]
        self.assertEqual(result["files"][path]["missing_numeric_cells"], {"vol": 1})
        rows = list(csv.DictReader(io.StringIO((self.output / path).read_bytes().decode("utf-8-sig"))))
        self.assertEqual(rows[0]["vol"], "")
        self.assertFalse(result["forward_ledger_eligible"])

    def test_candidate_T_name_generation_and_uniform_timestamp_are_strict(self):
        base = list(csv.DictReader(io.StringIO(table_bytes("candidate", D).decode("utf-8-sig"))))[0]
        for key, value in [("verify_date", D), ("name", ""),
                           ("generated_at_utc", "2026-09-08T06:59:59Z"),
                           ("generated_at_utc", "2026-09-10T00:00:00Z"),
                           ("generated_at_utc", "2026-09-08T00:00:00"),
                           ("generated_at_utc", "not-date")]:
            with self.subTest(key=key, value=value), self.assertRaises(inputs.InputError):
                row = {**base, key: value}
                inputs._csv_profile(csv_bytes(base.keys(), [row]), "candidate", D, exec_date=T, now=NOW)
        second = {**base, "ts_code": "600001.SH", "generated_at_utc": "2026-09-08T16:00:00Z"}
        with self.assertRaisesRegex(inputs.InputError, "mixed generation"):
            inputs._csv_profile(csv_bytes(base.keys(), [base, second]), "candidate", D, exec_date=T, now=NOW)

    def test_metadata_future_duplicate_keys_and_nonfinite_fail(self):
        meta = metadata(D)
        meta["generated_at_bj"] = "2026-09-10 22:07:41"
        with self.assertRaisesRegex(inputs.InputError, "postdates"):
            inputs._meta_contract(json.dumps(meta).encode(), D, NOW)
        meta["generated_at_bj"] = "2026-09-08 14:59:59"
        with self.assertRaisesRegex(inputs.InputError, "precedes"):
            inputs._meta_contract(json.dumps(meta).encode(), D, NOW)
        for body in [b'{"jobs":[],"jobs":[]}', b'{"value":NaN}', b'[]']:
            with self.assertRaises(inputs.InputError):
                inputs._object(body)

    def test_commit_and_url_allowlist_reject_mutable_or_unapproved_sources(self):
        for commit in ["main", "short", "A" * 40, "1" * 39]:
            with self.subTest(commit=commit), self.assertRaises(inputs.InputError):
                inputs.collect_inputs(ROOT, self.output, D, commit, MARKET, fetch=Source(), now_utc=NOW)
        for url in [f"https://raw.githubusercontent.com/{inputs.PRED_REPO}/main/file.csv",
                    f"https://raw.githubusercontent.com/njedu2023-prog/DC20/{PRED}/file.csv",
                    f"https://raw.githubusercontent.com/{inputs.PRED_REPO}/{PRED}/../file.csv",
                    f"https://raw.githubusercontent.com/{inputs.PRED_REPO}/{PRED}/file.csv?q=1",
                    f"http://raw.githubusercontent.com/{inputs.PRED_REPO}/{PRED}/file.csv"]:
            with self.subTest(url=url), self.assertRaises(inputs.InputError):
                inputs._validate_url(url)

    def test_output_nonempty_symlink_repository_and_relative_are_rejected(self):
        source = Source()
        self.output.mkdir()
        (self.output / "keep.txt").write_text("do not overwrite")
        with self.assertRaises(inputs.InputError):
            self.collect(source)
        self.assertEqual((self.output / "keep.txt").read_text(), "do not overwrite")
        link = self.output.parent / "link"
        link.symlink_to(self.output, target_is_directory=True)
        for target in [link, ROOT / "forward" / "input-bundle", Path("relative")]:
            with self.subTest(target=target), self.assertRaises(inputs.InputError):
                inputs.collect_inputs(ROOT, target, D, PRED, MARKET, fetch=source, now_utc=NOW)
        self.assertEqual(source.urls, [])

    def test_collection_rejects_pre_close_nontrading_date_and_naive_now(self):
        for date, now in [(D, "2026-09-08T06:59:59Z"), ("20260906", NOW),
                          (D, datetime(2026, 9, 9)), ("20260999", NOW)]:
            with self.subTest(date=date, now=now), self.assertRaises(ValueError):
                inputs.collect_inputs(ROOT, self.output, date, PRED, MARKET, fetch=Source(), now_utc=now)

    def test_budget_timeout_and_response_type_fail_without_manifest(self):
        with patch.object(inputs, "MAX_REQUESTS", 1):
            self.fail_collect(Source(), "budget")
        with patch.object(inputs, "MAX_TOTAL_BYTES", 1):
            self.fail_collect(Source(), "size budget")
        with patch.object(inputs, "MAX_FILE_BYTES", 1):
            self.fail_collect(Source(), "bounded")
        with patch.object(inputs.time, "monotonic", side_effect=[0, 0, 721]):
            self.fail_collect(Source(), "deadline")
        self.fail_collect(lambda url: "text not bytes", "bytes")

    def test_no_old_outputs_model_or_ledger_read_or_write(self):
        old_open = io.open
        opened = []
        def safe_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(file)).resolve()
                if path.is_relative_to(ROOT):
                    relative = path.relative_to(ROOT).as_posix()
                    opened.append(relative)
                    if relative not in {inputs.CALENDAR, "forward/inputs.py"}:
                        raise AssertionError(f"unexpected repository input: {relative}")
                    if any(flag in mode for flag in "wax+"):
                        raise AssertionError("repository write")
            return old_open(file, mode, *args, **kwargs)
        with patch("io.open", side_effect=safe_open):
            self.collect(Source(optional=False))
        self.assertEqual(set(opened), {inputs.CALENDAR, "forward/inputs.py"})


if __name__ == "__main__":
    unittest.main()
