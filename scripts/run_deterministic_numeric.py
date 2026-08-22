#!/usr/bin/env python3
"""Run an allowlisted Decision model entry point under one numeric runtime.

The GitHub-hosted runner pool can expose different x86 CPUs.  NumPy and
OpenBLAS otherwise select CPU-specific kernels at import time, while
scikit-learn and LightGBM may also initialize OpenMP thread pools.  This
launcher re-executes Python once with an exact environment before importing
any numerical package, verifies the loaded libraries, and only then executes
one reviewed model entry point in the same process.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import runpy
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


RUNTIME_SCHEMA = "dc20_deterministic_numeric_runtime_v1"
BOOTSTRAP_ENV = "DC20_DETERMINISTIC_NUMERIC_RUNTIME"
EVIDENCE_FILE_ENV = "DC20_NUMERIC_RUNTIME_EVIDENCE_FILE"
REQUIRED_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "OMP_THREAD_LIMIT": "1",
    "OMP_DYNAMIC": "FALSE",
    "OPENBLAS_NUM_THREADS": "1",
    "OPENBLAS_CORETYPE": "Haswell",
    "GOTO_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "MKL_DYNAMIC": "FALSE",
    "BLIS_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "NPY_ENABLE_CPU_FEATURES": "X86_V3",
}
NUMPY_ALLOWED_BASELINE_FEATURES = frozenset(
    {
        "MMX",
        "SSE",
        "SSE2",
        "SSE3",
        "SSSE3",
        "SSE41",
        "POPCNT",
        "SSE42",
        "LAHF",
        "CX16",
        "X86_V2",
    }
)
NUMPY_REQUIRED_ACTIVE_DISPATCH = ("X86_V3",)
ALLOWED_TARGETS = frozenset(
    {
        "replay_frozen_canonical_v2.py",
        "run_auction_v3.py",
    }
)


class DeterministicNumericRuntimeError(RuntimeError):
    """The model process is not bound to the reviewed numeric runtime."""


def configured_environment(base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env.pop("NPY_DISABLE_CPU_FEATURES", None)
    env.update(REQUIRED_ENV)
    env[BOOTSTRAP_ENV] = RUNTIME_SCHEMA
    return env


def validate_environment(env: Mapping[str, str]) -> None:
    expected = {**REQUIRED_ENV, BOOTSTRAP_ENV: RUNTIME_SCHEMA}
    drift = {
        key: {"expected": value, "actual": env.get(key)}
        for key, value in expected.items()
        if env.get(key) != value
    }
    if drift:
        raise DeterministicNumericRuntimeError(
            "deterministic numeric environment drift: "
            + json.dumps(drift, sort_keys=True, separators=(",", ":"))
        )
    if "NPY_DISABLE_CPU_FEATURES" in env:
        raise DeterministicNumericRuntimeError(
            "NPY_DISABLE_CPU_FEATURES is forbidden by the numeric runtime"
        )


def validate_numpy_dispatch(
    *,
    cpu_features: Mapping[str, Any],
    cpu_baseline: Sequence[str],
    cpu_dispatch: Sequence[str],
) -> None:
    """Require a V2-or-lower baseline and exactly one active V3 target.

    ``__cpu_features__`` also contains raw hardware capability flags.  A raw
    AVX512 leaf may therefore remain true when it is not a compiled dispatch
    target.  NumPy defines the active targets as the intersection of
    ``__cpu_dispatch__`` and true entries in ``__cpu_features__``; validate
    that surface instead of treating every hardware flag as executable code.
    """

    if isinstance(cpu_baseline, (str, bytes)) or isinstance(cpu_dispatch, (str, bytes)):
        raise DeterministicNumericRuntimeError(
            "NumPy baseline and dispatch metadata must be sequences"
        )
    if any(type(name) is not str or not name or name != name.upper() for name in cpu_baseline):
        raise DeterministicNumericRuntimeError("NumPy baseline metadata is invalid")
    if any(type(name) is not str or not name or name != name.upper() for name in cpu_dispatch):
        raise DeterministicNumericRuntimeError("NumPy dispatch metadata is invalid")

    baseline = tuple(cpu_baseline)
    dispatch = tuple(cpu_dispatch)
    if len(set(baseline)) != len(baseline) or len(set(dispatch)) != len(dispatch):
        raise DeterministicNumericRuntimeError(
            "NumPy baseline or dispatch metadata contains duplicates"
        )
    forbidden_baseline = sorted(
        set(baseline).difference(NUMPY_ALLOWED_BASELINE_FEATURES)
    )
    if forbidden_baseline:
        raise DeterministicNumericRuntimeError(
            "NumPy baseline exceeds X86_V2: " + ", ".join(forbidden_baseline)
        )

    if "X86_V3" not in dispatch:
        raise DeterministicNumericRuntimeError(
            "NumPy wheel does not provide the required X86_V3 dispatch target"
        )
    invalid_feature_values = sorted(
        name for name in dispatch if type(cpu_features.get(name)) is not bool
    )
    if invalid_feature_values:
        raise DeterministicNumericRuntimeError(
            "NumPy dispatch feature values are not boolean: "
            + ", ".join(invalid_feature_values)
        )
    active = tuple(
        sorted(
            name
            for name in dispatch
            if cpu_features.get(name) is True
        )
    )
    if active != NUMPY_REQUIRED_ACTIVE_DISPATCH:
        rendered = ", ".join(active) if active else "none"
        raise DeterministicNumericRuntimeError(
            "NumPy active dispatch targets are not exactly X86_V3: " + rendered
        )


def resolve_target(raw: str, *, launcher: Path | None = None) -> Path:
    launcher_path = (launcher or Path(__file__)).resolve()
    root = launcher_path.parents[1]
    candidate_input = Path(raw)
    if not candidate_input.is_absolute():
        candidate_input = Path.cwd() / candidate_input
    if candidate_input.is_symlink():
        raise DeterministicNumericRuntimeError(
            "numeric launcher target must not be a symlink"
        )
    candidate = candidate_input.resolve()
    allowed = {
        (root / "scripts" / name).resolve()
        for name in ALLOWED_TARGETS
    }
    if candidate not in allowed:
        raise DeterministicNumericRuntimeError(
            "numeric launcher target is not allowlisted"
        )
    if not candidate.is_file() or candidate.is_symlink():
        raise DeterministicNumericRuntimeError(
            "numeric launcher target must be one regular repository file"
        )
    return candidate


def validate_threadpools(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    safe: dict[tuple[str, int, str | None, str | None, str | None], dict[str, Any]] = {}
    internal_apis: set[str] = set()
    for record in records:
        internal_api = str(record.get("internal_api") or "").strip().lower()
        if not internal_api:
            continue
        if internal_api not in {"openblas", "openmp"}:
            raise DeterministicNumericRuntimeError(
                f"numeric library {internal_api!r} is not allowlisted"
            )
        internal_apis.add(internal_api)
        threads = record.get("num_threads")
        if type(threads) is not int or threads != 1:
            raise DeterministicNumericRuntimeError(
                f"numeric library {internal_api!r} exposed {threads!r} threads"
            )
        architecture = str(record.get("architecture") or "").strip()
        user_api = str(record.get("user_api") or "").strip().lower()
        expected_user_api = "blas" if internal_api == "openblas" else "openmp"
        if user_api != expected_user_api:
            raise DeterministicNumericRuntimeError(
                f"numeric library {internal_api!r} has user API {user_api!r}"
            )
        if internal_api == "openblas" and architecture.casefold() != "haswell":
            raise DeterministicNumericRuntimeError(
                f"OpenBLAS architecture is not Haswell: {architecture!r}"
            )
        if internal_api == "openmp" and architecture:
            raise DeterministicNumericRuntimeError(
                f"OpenMP architecture metadata is unexpected: {architecture!r}"
            )
        item = {
            "architecture": "Haswell" if internal_api == "openblas" else None,
            "internal_api": internal_api,
            "num_threads": threads,
            "prefix": str(record.get("prefix") or "").strip() or None,
            "version": str(record.get("version") or "").strip() or None,
        }
        key = (
            internal_api,
            threads,
            item["architecture"],
            item["prefix"],
            item["version"],
        )
        safe[key] = item
    missing = sorted({"openblas", "openmp"}.difference(internal_apis))
    if missing:
        raise DeterministicNumericRuntimeError(
            "numeric threadpool audit did not load: " + ", ".join(missing)
        )
    return sorted(
        safe.values(),
        key=lambda item: (
            str(item["internal_api"]),
            str(item["prefix"]),
            str(item["version"]),
        ),
    )


def validate_host_contract(
    *,
    env: Mapping[str, str] | None = None,
    os_release_path: Path = Path("/etc/os-release"),
) -> None:
    runtime_env = os.environ if env is None else env
    if platform.system() != "Linux" or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise DeterministicNumericRuntimeError(
            "production numeric launcher requires Linux x86_64"
        )
    if runtime_env.get("GITHUB_ACTIONS") != "true":
        raise DeterministicNumericRuntimeError(
            "production numeric launcher requires GitHub Actions"
        )
    try:
        lines = os_release_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DeterministicNumericRuntimeError(
            "production numeric launcher cannot verify /etc/os-release"
        ) from exc
    release: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in release:
            raise DeterministicNumericRuntimeError(
                f"duplicate operating-system release field: {key}"
            )
        release[key] = value.strip().strip('"')
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise DeterministicNumericRuntimeError(
            "production numeric launcher requires Ubuntu 24.04"
        )


def runtime_evidence(target: Path) -> dict[str, Any]:
    validate_host_contract()

    # Import after the exact environment is installed by the bootstrap exec.
    import numpy as np
    import scipy.linalg  # noqa: F401
    from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: F401
    from threadpoolctl import threadpool_info

    # Force one BLAS call before inspecting the loaded libraries.
    np.matmul(np.ones((2, 2), dtype=np.float64), np.ones((2, 2), dtype=np.float64))
    multiarray = np._core._multiarray_umath
    cpu_features = getattr(multiarray, "__cpu_features__", {})
    cpu_baseline = getattr(multiarray, "__cpu_baseline__", ())
    cpu_dispatch = getattr(multiarray, "__cpu_dispatch__", ())
    validate_numpy_dispatch(
        cpu_features=cpu_features,
        cpu_baseline=cpu_baseline,
        cpu_dispatch=cpu_dispatch,
    )
    pools = validate_threadpools(threadpool_info())
    return {
        "schema_version": RUNTIME_SCHEMA,
        "host_contract": "github_ubuntu_24_04_x86_64",
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_cpu_dispatch_cap": "X86_V3",
        "numpy_x86_v4_disabled": True,
        "openblas_coretype": "Haswell",
        "target": target.name,
        "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "threadpools": pools,
    }


def write_evidence_file(evidence: Mapping[str, Any], raw_path: str) -> None:
    path = Path(raw_path)
    if not path.is_absolute():
        raise DeterministicNumericRuntimeError(
            "numeric runtime evidence path must be absolute"
        )
    if not path.parent.is_dir() or path.parent.is_symlink() or path.exists():
        raise DeterministicNumericRuntimeError(
            "numeric runtime evidence path is not a new regular file"
        )
    rendered = json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, rendered.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise DeterministicNumericRuntimeError("numeric launcher target is required")

    if os.environ.get(BOOTSTRAP_ENV) != RUNTIME_SCHEMA:
        env = configured_environment(os.environ)
        os.execve(
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *arguments],
            env,
        )
        raise AssertionError("os.execve unexpectedly returned")

    validate_environment(os.environ)
    target = resolve_target(arguments[0])
    evidence = runtime_evidence(target)
    evidence_path = os.environ.get(EVIDENCE_FILE_ENV, "").strip()
    if evidence_path:
        write_evidence_file(evidence, evidence_path)
    print(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )

    sys.argv = [str(target), *arguments[1:]]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit:
        validate_environment(os.environ)
        if runtime_evidence(target)["threadpools"] != evidence["threadpools"]:
            raise DeterministicNumericRuntimeError(
                "numeric threadpool contract drifted during target execution"
            )
        raise
    validate_environment(os.environ)
    if runtime_evidence(target)["threadpools"] != evidence["threadpools"]:
        raise DeterministicNumericRuntimeError(
            "numeric threadpool contract drifted during target execution"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
