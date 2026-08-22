from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_deterministic_numeric as runtime


def _threadpools(*, blas_threads: int = 1, architecture: str = "Haswell"):
    return [
        {
            "architecture": architecture,
            "filepath": "/secret/libopenblas.so",
            "internal_api": "openblas",
            "num_threads": blas_threads,
            "prefix": "libscipy_openblas",
            "user_api": "blas",
            "version": "0.3.30",
        },
        {
            "filepath": "/secret/libgomp.so",
            "internal_api": "openmp",
            "num_threads": 1,
            "prefix": "libgomp",
            "user_api": "openmp",
            "version": None,
        },
    ]


def test_configured_environment_is_exact_and_preserves_unrelated_values() -> None:
    configured = runtime.configured_environment(
        {"NPY_DISABLE_CPU_FEATURES": "X86_V4", "UNRELATED": "kept"}
    )
    assert configured["UNRELATED"] == "kept"
    assert "NPY_DISABLE_CPU_FEATURES" not in configured
    assert configured[runtime.BOOTSTRAP_ENV] == runtime.RUNTIME_SCHEMA
    assert {key: configured[key] for key in runtime.REQUIRED_ENV} == runtime.REQUIRED_ENV
    runtime.validate_environment(configured)


def test_environment_validation_rejects_any_contract_drift() -> None:
    configured = runtime.configured_environment({})
    configured["OPENBLAS_NUM_THREADS"] = "2"
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="environment drift"):
        runtime.validate_environment(configured)
    forbidden = runtime.configured_environment({})
    forbidden["NPY_DISABLE_CPU_FEATURES"] = "X86_V4"
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="is forbidden"):
        runtime.validate_environment(forbidden)


def test_numpy_dispatch_allows_raw_avx512_hardware_outside_dispatch() -> None:
    runtime.validate_numpy_dispatch(
        cpu_features={
            "X86_V3": True,
            "X86_V4": False,
            "AVX512F": True,
        },
        cpu_baseline=("SSE", "SSE2", "SSE3"),
        cpu_dispatch=("X86_V3", "X86_V4"),
    )


@pytest.mark.parametrize(
    ("features", "baseline", "dispatch", "message"),
    [
        (
            {"X86_V3": True, "X86_V4": True},
            ("SSE", "SSE2", "SSE3"),
            ("X86_V3", "X86_V4"),
            "not exactly X86_V3",
        ),
        (
            {"X86_V3": True, "AVX512F": True},
            ("SSE", "SSE2", "SSE3"),
            ("X86_V3", "AVX512F"),
            "not exactly X86_V3",
        ),
        (
            {"X86_V3": True},
            ("SSE", "SSE2", "SSE3", "AVX2"),
            ("X86_V3",),
            "baseline exceeds X86_V2",
        ),
        (
            {"AVX2": True},
            ("SSE", "SSE2", "SSE3"),
            ("AVX2",),
            "does not provide the required X86_V3",
        ),
        (
            {"X86_V3": False},
            ("SSE", "SSE2", "SSE3"),
            ("X86_V3",),
            "not exactly X86_V3",
        ),
        (
            {"X86_V3": True, "X86_V4": 0},
            ("SSE", "SSE2", "SSE3"),
            ("X86_V3", "X86_V4"),
            "not boolean",
        ),
    ],
)
def test_numpy_dispatch_rejects_baseline_or_active_target_drift(
    features, baseline, dispatch, message
) -> None:
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match=message):
        runtime.validate_numpy_dispatch(
            cpu_features=features,
            cpu_baseline=baseline,
            cpu_dispatch=dispatch,
        )


@pytest.mark.skipif(
    platform.system() != "Linux" or platform.machine().lower() not in {"amd64", "x86_64"},
    reason="production NumPy dispatch contract is Linux x86_64 only",
)
def test_pinned_numpy_starts_with_exact_x86_v3_dispatch_in_fresh_process() -> None:
    root = Path(__file__).resolve().parents[1]
    source = """
import json
import numpy as np
from scripts import run_deterministic_numeric as runtime

multiarray = np._core._multiarray_umath
features = multiarray.__cpu_features__
baseline = multiarray.__cpu_baseline__
dispatch = multiarray.__cpu_dispatch__
runtime.validate_numpy_dispatch(
    cpu_features=features,
    cpu_baseline=baseline,
    cpu_dispatch=dispatch,
)
print(json.dumps({
    "active": sorted(name for name in dispatch if features.get(name) is True),
    "baseline": list(baseline),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=root,
        env=runtime.configured_environment(os.environ),
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(completed.stdout)
    assert evidence["active"] == ["X86_V3"]


def test_target_resolution_is_repo_local_allowlisted_and_not_a_symlink(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "run_deterministic_numeric.py"
    launcher.write_text("# launcher\n", encoding="utf-8")
    allowed = scripts / "replay_frozen_canonical_v2.py"
    allowed.write_text("# replay\n", encoding="utf-8")
    forbidden = scripts / "not_reviewed.py"
    forbidden.write_text("# no\n", encoding="utf-8")

    assert runtime.resolve_target(str(allowed), launcher=launcher) == allowed.resolve()
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="not allowlisted"):
        runtime.resolve_target(str(forbidden), launcher=launcher)

    alias = scripts / "run_auction_v3.py"
    alias.symlink_to(allowed)
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="must not be a symlink"):
        runtime.resolve_target(str(alias), launcher=launcher)


def test_threadpool_audit_requires_one_thread_and_haswell_without_paths() -> None:
    evidence = runtime.validate_threadpools(_threadpools())
    assert {item["internal_api"] for item in evidence} == {"openblas", "openmp"}
    assert all(item["num_threads"] == 1 for item in evidence)
    assert "/secret" not in repr(evidence)
    duplicated = runtime.validate_threadpools(_threadpools() + _threadpools())
    assert duplicated == evidence

    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="exposed 2 threads"):
        runtime.validate_threadpools(_threadpools(blas_threads=2))
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="not Haswell"):
        runtime.validate_threadpools(_threadpools(architecture="Zen"))
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="did not load"):
        runtime.validate_threadpools(_threadpools()[0:1])
    unknown = _threadpools() + [
        {
            "architecture": "",
            "internal_api": "mkl",
            "num_threads": 1,
            "prefix": "libmkl",
            "user_api": "blas",
            "version": "1",
        }
    ]
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="not allowlisted"):
        runtime.validate_threadpools(unknown)
    wrong_api = _threadpools()
    wrong_api[0]["user_api"] = "openmp"
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="has user API"):
        runtime.validate_threadpools(wrong_api)
    openmp_architecture = _threadpools()
    openmp_architecture[1]["architecture"] = "x86_64"
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="unexpected"):
        runtime.validate_threadpools(openmp_architecture)


def test_host_contract_requires_github_ubuntu_2404_x86_64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding="utf-8")
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    runtime.validate_host_contract(
        env={"GITHUB_ACTIONS": "true"}, os_release_path=release
    )
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="GitHub Actions"):
        runtime.validate_host_contract(env={}, os_release_path=release)
    release.write_text('ID=ubuntu\nVERSION_ID="22.04"\n', encoding="utf-8")
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="Ubuntu 24.04"):
        runtime.validate_host_contract(
            env={"GITHUB_ACTIONS": "true"}, os_release_path=release
        )


def test_runtime_evidence_is_emitted_on_stderr_to_preserve_target_stdout() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "file=sys.stderr" in source


def test_runtime_evidence_file_is_new_absolute_and_canonical(tmp_path: Path) -> None:
    evidence = {"schema_version": runtime.RUNTIME_SCHEMA, "target": "replay.py"}
    path = tmp_path / "numeric-runtime.json"
    runtime.write_evidence_file(evidence, str(path))
    assert path.read_text(encoding="utf-8") == (
        '{"schema_version":"dc20_deterministic_numeric_runtime_v1",'
        '"target":"replay.py"}\n'
    )
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="new regular file"):
        runtime.write_evidence_file(evidence, str(path))
    with pytest.raises(runtime.DeterministicNumericRuntimeError, match="must be absolute"):
        runtime.write_evidence_file(evidence, "relative.json")
