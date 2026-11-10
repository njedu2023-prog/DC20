import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostic", ROOT / "scripts/diagnose_candidate_trigger.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class DiagnosticBoundaryTests(unittest.TestCase):
    def env(self):
        return {"GITHUB_ACTIONS":"true", "GITHUB_REPOSITORY":m.REPOSITORY,
            "GITHUB_REF":"refs/heads/main", "GITHUB_EVENT_NAME":"workflow_dispatch",
            "GITHUB_RUN_ATTEMPT":"1", "GITHUB_SHA":"a"*40, "GITHUB_RUN_ID":"123"}
    def run_doc(self):
        return {"id":123, "path":m.WORKFLOW_PATH,"head_branch":"main","head_sha":"a"*40,
            "event":"workflow_dispatch","run_attempt":1,"status":"in_progress","conclusion":None,
            "repository":{"full_name":m.REPOSITORY},"head_repository":{"full_name":m.REPOSITORY}}
    def test_allowed_original_ids(self):
        for id in m.ALLOWED_P0_RUNS: m.verify_context(self.env(),"a"*40,id)
        with self.assertRaisesRegex(ValueError,"P0_RUN_NOT_ALLOWED"):
            m.verify_context(self.env(),"a"*40,"999")
    def test_every_context_boundary_rejects(self):
        for key,value in {"GITHUB_ACTIONS":"false","GITHUB_REPOSITORY":"other/repo",
            "GITHUB_REF":"refs/heads/other","GITHUB_EVENT_NAME":"schedule",
            "GITHUB_RUN_ATTEMPT":"2","GITHUB_SHA":"b"*40,"GITHUB_RUN_ID":"0"}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                e=self.env();e[key]=value;m.verify_context(e,"a"*40,"42726704719")
    def test_actual_api_identity_is_required(self):
        m.validate_actual_run(self.run_doc(),self.env(),"a"*40)
        for key,value in {"id":124,"path":"wrong","head_branch":"wrong","head_sha":"b"*40,
            "event":"schedule","run_attempt":2,"status":"completed","conclusion":"success",
            "repository":{"full_name":"wrong"},"head_repository":{"full_name":"wrong"}}.items():
            with self.subTest(key=key),self.assertRaises(ValueError):
                doc=self.run_doc();doc[key]=value;m.validate_actual_run(doc,self.env(),"a"*40)
    def test_only_literal_code_is_disclosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"scripts").mkdir()
            (root/"scripts"/"guard.py").write_text("raise ValueError('FIXED_GUARD_CODE')\n")
            codes=m.static_codes(root)
            self.assertEqual(m.safe_reason(ValueError("FIXED_GUARD_CODE"),codes),"FIXED_GUARD_CODE")
            for secret in ("TOPSECRET_TOKEN", "https://private.example/path", "FIXED_GUARD_CODE:secret"):
                self.assertEqual(m.safe_reason(ValueError(secret),codes),"REDACTED_NON_STATIC_EXCEPTION")
    def test_source_has_no_publication_or_network_writer(self):
        tree=ast.parse((ROOT/"scripts/diagnose_candidate_trigger.py").read_text())
        attrs=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        self.assertNotIn("main",attrs) # the mutation-capable business CLI is not called
        self.assertIn("inspect_p0",attrs)
        self.assertFalse(set(attrs)&{"post","put","patch","delete","publish","write_bytes","write_text"})
    def test_manual_workflow_and_minimal_permissions(self):
        raw=(ROOT/".github/workflows/diagnose_candidate_trigger.yml").read_text()
        self.assertIn("workflow_dispatch:",raw)
        self.assertNotIn("schedule:",raw);self.assertNotIn("workflow_run:",raw)
        self.assertNotIn("write",raw.split("permissions:",1)[1].split("concurrency:",1)[0])
        self.assertIn("contents: read",raw);self.assertIn("actions: read",raw)
        self.assertIn("persist-credentials: false",raw)
        self.assertIn("ref: ${{ github.sha }}",raw)
        self.assertIn("github.run_attempt == 1",raw)

    def exercise_main(self, error=None):
        gate = types.ModuleType("work.profit_1000_upgrade.candidate_natural_trigger_gate")
        api_calls = []
        head = {"ref":"refs/heads/main","object":{"type":"commit","sha":"a"*40}}
        run = self.run_doc()
        class ReadClient:
            def __init__(self, token): pass
            def get_json(self, path):
                api_calls.append(path)
                return run if path.endswith("/actions/runs/123") else head
        gh = types.SimpleNamespace(GitHubReadClient=ReadClient, API_PREFIX="/repos/"+m.REPOSITORY,
                                   TOKEN_ENV="DC20_CANDIDATE_GITHUB_TOKEN")
        gate.workflow = types.SimpleNamespace(dependencies=lambda:(None,gh))
        def inspect_p0(**kwargs):
            self.assertEqual(kwargs["run_id"],"42726704719")
            if error is not None: raise error
            return {"status":"EXISTING_CANDIDATE_VERIFIED_NO_NEW_FREEZE",
                    "signal_date":"20261109","needs_work":False}
        gate.inspect_p0 = inspect_p0
        package = types.ModuleType("work.profit_1000_upgrade")
        package.candidate_natural_trigger_gate = gate
        modules={"work":types.ModuleType("work"), "work.profit_1000_upgrade":package,
                 "work.profit_1000_upgrade.candidate_natural_trigger_gate":gate}
        stream=io.StringIO();previous=sys.gettrace()
        def sentinel(frame,event,arg): return sentinel
        try:
            sys.settrace(sentinel)
            with mock.patch.dict(sys.modules,modules), mock.patch.dict(m.os.environ,self.env(),clear=True), \
                 mock.patch.object(m,"static_codes",return_value={"EXACT_STATIC_GUARD"}), \
                 contextlib.redirect_stdout(stream):
                code=m.main(["--expected-head","a"*40,"--p0-run-id","42726704719"])
            self.assertIs(sys.gettrace(),sentinel)
        finally:
            sys.settrace(previous)
        self.assertTrue(all(path.startswith("/repos/"+m.REPOSITORY+"/") for path in api_calls))
        return code,json.loads(stream.getvalue())

    def test_main_reports_static_failure_and_restores_trace(self):
        code,result=self.exercise_main(ValueError("EXACT_STATIC_GUARD"))
        self.assertEqual(code,1)
        self.assertEqual(result["reason"],"EXACT_STATIC_GUARD")
        self.assertEqual(result["stage"],"ORIGINAL_DUPLICATE_P0_PREFLIGHT")

    def test_main_redacts_arbitrary_exception_body(self):
        code,result=self.exercise_main(ValueError("PRIVATE_PROVIDER_BODY_TOKEN_VALUE"))
        self.assertEqual(code,1)
        self.assertEqual(result["reason"],"REDACTED_NON_STATIC_EXCEPTION")
        self.assertNotIn("PRIVATE_PROVIDER_BODY_TOKEN_VALUE",json.dumps(result))

    def test_main_success_preserves_noop_result_and_restores_trace(self):
        code,result=self.exercise_main()
        self.assertEqual(code,0)
        self.assertEqual(result["status"],"READ_ONLY_PREFLIGHT_PASSED")
        self.assertIs(result["needs_work"],False)

if __name__ == "__main__":
    unittest.main()
