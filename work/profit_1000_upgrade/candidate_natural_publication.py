"""GET-only independent observation of a new research prediction publication.

Only a same-run immutable artifact and GitHub-observed successful job can
identify the publication. No caller-made acknowledgement, market data request,
file extraction, prediction, fit, economic settlement or trading permission.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile
import zlib

ROOT = Path(__file__).absolute().parents[2]
IMPORTER_PATH = "work/profit_1000_upgrade/candidate_natural_p0_github.py"
IMPORTER_SHA = '833f6607c078ae25144d333110d018fa142687e49588cbaf099a731ca42e3cbf'
WORKFLOW_PATH = ".github/workflows/research_candidate_natural_forward.yml"
WORKFLOW_NAME = "DC20 · Fixed candidate natural forward (research)"
WORKFLOW_ID = 357010624
COORDINATOR_PATH = "work/profit_1000_upgrade/candidate_natural_workflow.py"
CONTINUATION_ONLY_FILES = {'work/profit_1000_upgrade/candidate_p0_generation_artifact.py', 'work/profit_1000_upgrade/candidate_natural_p0_continuation.py'}
FIXED_FILES = {
    IMPORTER_PATH: IMPORTER_SHA,
    'work/profit_1000_upgrade/candidate_natural_p0_continuation.py': '680047e2913581dd1cdffa2da4ce684606f7cae6d77e3607e05bc84dbc7b74b9',
    'work/profit_1000_upgrade/candidate_p0_generation_artifact.py': '26efcd53238e3abbbae4891f8fcf773e1d98987227eb2c633d94d75c9d23f94e',
    COORDINATOR_PATH: 'ab0dea692440f151658ceac6da442b61b742b94a81bb96c61047fddb0c73088b',
    WORKFLOW_PATH: "384d43d96a941c4537baafa898ca477db0b5cabe4fc199bc591c79c0c13232bd",
    "requirements-dev.lock": "773ce43677ceff7e5829252816ba017738011ff60a389d60f0435b96264005f2",
    "work/profit_1000_upgrade/candidate_natural_forward.py": '99872ceec2203a586d03cd17f9efd2d91a7f5524d6349fc01f5d6894922dc1a3',
    "work/profit_1000_upgrade/candidate_natural_forward_registration.json": 'fb8ac959b727dcafebf217098b8544d4624aa75dfebbacd4eafef7429c0b4399',
    "work/profit_1000_upgrade/candidate_natural_model/evaluation.json": "779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872",
    "work/profit_1000_upgrade/candidate_eligible_forward.py": '77e7d2f21e39350c2e78cad435093c25072c7694e8cdc6757fed68e9a1d3036f',
    "work/profit_1000_upgrade/candidate_eligible_forward_registration.json": '6e23e6f3ce4dc0f67dd31cf3ebe24c238bf62b276cefe203eee756501076755c',
    "work/profit_1000_upgrade/candidate_eligible_pool.py": "86d8fbb049afc5cbc207e3bb9adc83481023abb345a69029e5c661be5a6edd23",
    "work/profit_1000_upgrade/candidate_snapshot_versions.py": '2612731ef3512abf72e590f3151d3edd2bf75d642a9beb1c41ad2a89d3ae2f83',
    "work/profit_1000_upgrade/candidate_legacy_workflow.py": 'be1b6f9d65e5d5737c5d77c1db1e02539345ef53ed20f78313275f00a7841913',
}
# Reviewed original profile; current code guards remain independently pinned.
HISTORICAL_CODE_BINDINGS = [{'path': '.github/workflows/research_candidate_natural_forward.yml',
  'sha256': '337f35739b50874124836f1d4496cc498fe696ddc803f43a3b9a3c51a4b8ce6c',
  'git_blob_sha1': 'c67b6ce12823984ee75091a94d281c50d30f0f13'},
 {'path': 'forward/storage.py',
  'sha256': 'cc54e3b2ac1750e021a706641c592306cfcac3454c296f7c0b12b893ff13d098',
  'git_blob_sha1': '7e622895c32455123d71244ae491a7302b9b2abe'},
 {'path': 'requirements-dev.lock',
  'sha256': '773ce43677ceff7e5829252816ba017738011ff60a389d60f0435b96264005f2',
  'git_blob_sha1': '892183b9e5c4ed2e1c1899589b8793f556b32136'},
 {'path': 'scripts/publish_primary_three_rank.py',
  'sha256': '5996a8f9e0b55f34c22e4e10e78924616cf8a8ebfb08be789ed169a9b3bb8d98',
  'git_blob_sha1': '71d647b58399f52801791f7f7216328ae3da0cdb'},
 {'path': 'src/top10decision/decision/d_close_features.py',
  'sha256': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e',
  'git_blob_sha1': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7'},
 {'path': 'src/top10decision/decision/three_rank.py',
  'sha256': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a',
  'git_blob_sha1': 'cf18b86656e71eff5a38e0e57d35494db172528f'},
 {'path': 'work/profit_1000_upgrade/candidate_d_feature_projection.py',
  'sha256': 'aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f',
  'git_blob_sha1': '0e11d8291ef7fde857ea3dd0afb1aadd7f277dbe'},
 {'path': 'work/profit_1000_upgrade/candidate_d_source_adapter.py',
  'sha256': 'bb9b32de5be84b55793d8e8c19dd6960727e90de83605c4f5a1c360dbd3d982e',
  'git_blob_sha1': '9e6b7e15fbb9bdbcfb404100ca2983f7baa604bb'},
 {'path': 'work/profit_1000_upgrade/candidate_eligible_forward.py',
  'sha256': '9177b183813626de51affaf6f9f17a0aed6c557b50a56018023fe8a62f28ea2c',
  'git_blob_sha1': 'a70b8da62a7129b5fd8bfffaed44346633be42f7'},
 {'path': 'work/profit_1000_upgrade/candidate_eligible_forward_registration.json',
  'sha256': '9aa093c0cb83cc7c6bccf86e912e29bb266a18f1112e19e7238435a07f28da1d',
  'git_blob_sha1': '32a74662197a0326827e862e7e1f0c4350b0e430'},
 {'path': 'work/profit_1000_upgrade/candidate_eligible_pool.py',
  'sha256': '86d8fbb049afc5cbc207e3bb9adc83481023abb345a69029e5c661be5a6edd23',
  'git_blob_sha1': '3855dbedf6e4b6c85454c50d95a22cc128d52273'},
 {'path': 'work/profit_1000_upgrade/candidate_forward_predict.py',
  'sha256': '6ada7749e033b5d3e5c39c4d674d835b9113d83b4a37ef9d7e7fbad70ac95719',
  'git_blob_sha1': 'e69a1792ca8c35cd73deffac53f772d47bcb432c'},
 {'path': 'work/profit_1000_upgrade/candidate_legacy_workflow.py',
  'sha256': '0623a000570a7994b4f3687f778eda66499e1026d8b606c4376f31b44031f0ce',
  'git_blob_sha1': 'b5e6202baf0efeb8081587064e2f830c5b9ceb20'},
 {'path': 'work/profit_1000_upgrade/candidate_live_feature_math.py',
  'sha256': '922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68',
  'git_blob_sha1': '4c38f38adae98d8f3a8a9eb4bd0c72b0e2bd5e04'},
 {'path': 'work/profit_1000_upgrade/candidate_natural_forward.py',
  'sha256': '5a3967c88829be0e6b9a0d7c384b6d2cf12ac7c257bac4dff40d1a15ce2d5c15',
  'git_blob_sha1': 'd87e15e664373bf722cd547dd5a2351d334d8ca8'},
 {'path': 'work/profit_1000_upgrade/candidate_natural_forward_registration.json',
  'sha256': '2b24d549c16b5ad6bb3c682eb491a56907ccd7cd986c4c2c9e494a76f4d79de2',
  'git_blob_sha1': '8edc8e1040fce37268979e412b78cfc0ab0c2c18'},
 {'path': 'work/profit_1000_upgrade/candidate_natural_model/evaluation.json',
  'sha256': '779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872',
  'git_blob_sha1': '2546af3bfab7ece915d43c86ffa5779b3e19e2a0'},
 {'path': 'work/profit_1000_upgrade/candidate_natural_p0_github.py',
  'sha256': '6adc11beaef7c5b940043a4063dc812f6f56c169c71ecaafd9c9be6364729cb4',
  'git_blob_sha1': '1c91e3a830cee558102d030821ca75ef616b3fd2'},
 {'path': 'work/profit_1000_upgrade/candidate_natural_workflow.py',
  'sha256': 'bbf68d26495c6a2708e17e58e280c34f99bffae9e19ab7a9128d491ec06cc044',
  'git_blob_sha1': '22b78dca1b5bfcfab577e40eb278d76d1af690db'},
 {'path': 'work/profit_1000_upgrade/candidate_snapshot_versions.py',
  'sha256': 'bda60c1f947e2b16c520ec457cf56fcf38bec6cb6775b0a8839eb6ebef1dac27',
  'git_blob_sha1': 'a3bd00a550bc7123e7f543569bf84c564ae379f3'}]
HISTORICAL_IMPORT_PROFILE = {'importer_sha256': '6adc11beaef7c5b940043a4063dc812f6f56c169c71ecaafd9c9be6364729cb4',
 'source_adapter_sha256': 'bb9b32de5be84b55793d8e8c19dd6960727e90de83605c4f5a1c360dbd3d982e',
 'source_adapter_dependency_sha256': {'scripts/publish_primary_three_rank.py': '5996a8f9e0b55f34c22e4e10e78924616cf8a8ebfb08be789ed169a9b3bb8d98',
                                      'work/profit_1000_upgrade/candidate_d_feature_projection.py': 'aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f',
                                      'work/profit_1000_upgrade/candidate_live_feature_math.py': '922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68',
                                      'src/top10decision/decision/d_close_features.py': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e',
                                      'src/top10decision/decision/three_rank.py': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a'}}

ELIGIBLE_ONLY_FILES = {p for p in FIXED_FILES if p.endswith(("candidate_eligible_forward.py", "candidate_eligible_forward_registration.json", "candidate_eligible_pool.py", "candidate_snapshot_versions.py", "candidate_legacy_workflow.py"))}
LEGACY_COORDINATOR_BINDING = {"path": COORDINATOR_PATH,
    "sha256": "0623a000570a7994b4f3687f778eda66499e1026d8b606c4376f31b44031f0ce",
    "git_blob_sha1": "b5e6202baf0efeb8081587064e2f830c5b9ceb20"}
MODEL_PATH = "work/profit_1000_upgrade/candidate_natural_model/evaluation.json"
PREFIX = "work/profit_1000_upgrade/candidate_natural_forward/"
SCHEMA = "dc20_independent_candidate_research_publication_observation_v1"
JOBS = ("Validate fixed natural candidate contracts", "Freeze original P0 bound new model research slots")
MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 256
MAX_JSON_BYTES = 4_000_000
MAX_CALLS = 40  # Includes two artifact downloads, each at most two HTTP calls.
# The 2026-09-14 publication predates the read-only duplicate trigger gate.
# Only this exact historical workflow is accepted remotely; local code must
# still match FIXED_FILES. No original model/importer/coordinator pin changes.
LEGACY_WORKFLOW_BINDING = {"path": WORKFLOW_PATH,
    "sha256": "4fb4b74ae221f49f66b2c191ebeb113e4ef3aba4b4c1428d46bded4c17d029af",
    "git_blob_sha1": "a9076078b15724baf31966ebb8b7e9118a7c3208"}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _bootstrap():
    import hashlib
    for relative, expected in FIXED_FILES.items():
        p = ROOT / relative
        require(p.is_file() and p.stat().st_nlink == 1 and not any(q.is_symlink() for q in (p, *p.parents))
            and hashlib.sha256(p.read_bytes()).hexdigest() == expected, "FROZEN_PUBLICATION_DEPENDENCY_REQUIRED")


_bootstrap()
from work.profit_1000_upgrade import candidate_natural_p0_github as gh
from work.profit_1000_upgrade import candidate_natural_workflow as workflow

eligible_runner, _importer = workflow.dependencies()
from work.profit_1000_upgrade import candidate_natural_forward as natural
from work.profit_1000_upgrade import candidate_legacy_workflow as legacy_workflow
require(_importer is gh, "SAME_IMPORTER_MODULE_REQUIRED")
SELF_SHA = gh.sha256(gh.read(Path(__file__).absolute())[0])


def code_guard():
    require(Path(gh.__file__).absolute() == ROOT / IMPORTER_PATH
        and Path(workflow.__file__).absolute() == ROOT / COORDINATOR_PATH, "PUBLICATION_IMPORT_ORIGIN_CHANGED")
    wanted = {**FIXED_FILES, **natural.PINS, **natural.source.CODE_PINS}
    values, state = {}, []
    for relative, expected in wanted.items():
        raw, identity = gh.read(ROOT / relative)
        require(gh.sha256(raw) == expected, "PUBLICATION_FIXED_BYTES_CHANGED:" + relative)
        values[relative] = raw
        state.append((relative, expected, identity))
    raw, identity = gh.read(Path(__file__).absolute())
    require(gh.sha256(raw) == SELF_SHA, "PUBLICATION_VERIFIER_CHANGED")
    return values, (tuple(state), identity, gh.code_guard(), natural._guard(), eligible_runner._guard())


# Exact previously accepted bytes at c56c2bde; old evidence is never rewritten.
PRE_BOUNDARY_CODE_BINDINGS = [{'path': '.github/workflows/research_candidate_natural_forward.yml', 'sha256': '384d43d96a941c4537baafa898ca477db0b5cabe4fc199bc591c79c0c13232bd', 'git_blob_sha1': 'e35031a9c899cfe68260bddaccd4c7cfe7adb6e8'}, {'path': 'forward/storage.py', 'sha256': 'cc54e3b2ac1750e021a706641c592306cfcac3454c296f7c0b12b893ff13d098', 'git_blob_sha1': '7e622895c32455123d71244ae491a7302b9b2abe'}, {'path': 'requirements-dev.lock', 'sha256': '773ce43677ceff7e5829252816ba017738011ff60a389d60f0435b96264005f2', 'git_blob_sha1': '892183b9e5c4ed2e1c1899589b8793f556b32136'}, {'path': 'scripts/publish_primary_three_rank.py', 'sha256': 'f691fc579b1cd9b44168cd90393b492ffa833fe3a4576a2322bcd307dadbc9d1', 'git_blob_sha1': 'a9e4a67d2458309d0faed75f04dafc913a8b68ea'}, {'path': 'src/top10decision/decision/d_close_features.py', 'sha256': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e', 'git_blob_sha1': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7'}, {'path': 'src/top10decision/decision/three_rank.py', 'sha256': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a', 'git_blob_sha1': 'cf18b86656e71eff5a38e0e57d35494db172528f'}, {'path': 'work/profit_1000_upgrade/candidate_d_feature_projection.py', 'sha256': 'aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f', 'git_blob_sha1': '0e11d8291ef7fde857ea3dd0afb1aadd7f277dbe'}, {'path': 'work/profit_1000_upgrade/candidate_d_source_adapter.py', 'sha256': '067e6c1a6c9210cc42712106ad726a09829fb1a62e3157320613705d85ceb64b', 'git_blob_sha1': '838f8b10958fdcad09b896c5e75d7f635ccf281d'}, {'path': 'work/profit_1000_upgrade/candidate_eligible_forward.py', 'sha256': '86c8e6f7f55d17609f02800a5243fa1b5e947bb5e32020d556403e33ecc9f2d2', 'git_blob_sha1': '8d77cbf477ddf96ec9ecde03e4f14d16b3c2bbbe'}, {'path': 'work/profit_1000_upgrade/candidate_eligible_forward_registration.json', 'sha256': '6f68eb804ae4e894b56f5d245a0b45f08f9a6c4a315c9edae4b9345be85a6878', 'git_blob_sha1': '7072d235220ecdfa139cd030c147ba5328694dde'}, {'path': 'work/profit_1000_upgrade/candidate_eligible_pool.py', 'sha256': '86d8fbb049afc5cbc207e3bb9adc83481023abb345a69029e5c661be5a6edd23', 'git_blob_sha1': '3855dbedf6e4b6c85454c50d95a22cc128d52273'}, {'path': 'work/profit_1000_upgrade/candidate_forward_predict.py', 'sha256': '6ada7749e033b5d3e5c39c4d674d835b9113d83b4a37ef9d7e7fbad70ac95719', 'git_blob_sha1': 'e69a1792ca8c35cd73deffac53f772d47bcb432c'}, {'path': 'work/profit_1000_upgrade/candidate_legacy_workflow.py', 'sha256': 'c2020bf627c4c73625b5f8ba7353c08acb663a848ea666fbea2272c05de025ee', 'git_blob_sha1': 'f45464afe541c80b9c3c632bfd4519069a0845b4'}, {'path': 'work/profit_1000_upgrade/candidate_live_feature_math.py', 'sha256': '922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68', 'git_blob_sha1': '4c38f38adae98d8f3a8a9eb4bd0c72b0e2bd5e04'}, {'path': 'work/profit_1000_upgrade/candidate_natural_forward.py', 'sha256': 'eb1f25bef6b7279102a4465ef6b756cb559dc6d04bd912c36573e07bfbd09029', 'git_blob_sha1': '15ad20fa489eccf86be313478760b4ff3f776721'}, {'path': 'work/profit_1000_upgrade/candidate_natural_forward_registration.json', 'sha256': 'b0f27d967d32bf5df854e7c8662aaa56883c5959d39db9436afbd92a50d91c8b', 'git_blob_sha1': '2c21163392acabaaf6c1f2919a0454df48223b4d'}, {'path': 'work/profit_1000_upgrade/candidate_natural_model/evaluation.json', 'sha256': '779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872', 'git_blob_sha1': '2546af3bfab7ece915d43c86ffa5779b3e19e2a0'}, {'path': 'work/profit_1000_upgrade/candidate_natural_p0_continuation.py', 'sha256': '680047e2913581dd1cdffa2da4ce684606f7cae6d77e3607e05bc84dbc7b74b9', 'git_blob_sha1': '4fef6088fbe25cdfad6e6d19243eed375c27d1da'}, {'path': 'work/profit_1000_upgrade/candidate_natural_p0_github.py', 'sha256': 'fc068fdf16c9dfd27a9ddaa4230c1d951f2809752b94784a09a6c2fb9be40507', 'git_blob_sha1': '46cbb2a01a5535e7b817b400e250c5292454faca'}, {'path': 'work/profit_1000_upgrade/candidate_natural_workflow.py', 'sha256': 'ce8d282b19b1aa934c520ea501e1efa03cada8f13eea58c054693db8bb7e4b4e', 'git_blob_sha1': '72ad86bc59d63d2b6272a75f7c0c147d0ec74609'}, {'path': 'work/profit_1000_upgrade/candidate_p0_generation_artifact.py', 'sha256': '26efcd53238e3abbbae4891f8fcf773e1d98987227eb2c633d94d75c9d23f94e', 'git_blob_sha1': 'aec9f7eda757e6ab784e9d591da41f7abc43819d'}, {'path': 'work/profit_1000_upgrade/candidate_snapshot_versions.py', 'sha256': 'b3cf21151cb604a3281a1f70e953764a9ed9eff6f9da6f17379f7b50bb316ff8', 'git_blob_sha1': '0aee931fea4fbf66631c3963dac6cc88c9c66c9d'}]
PRE_BOUNDARY_IMPORT_PROFILE = {'importer_sha256': 'fc068fdf16c9dfd27a9ddaa4230c1d951f2809752b94784a09a6c2fb9be40507', 'source_adapter_sha256': '067e6c1a6c9210cc42712106ad726a09829fb1a62e3157320613705d85ceb64b', 'source_adapter_dependency_sha256': {'scripts/publish_primary_three_rank.py': 'f691fc579b1cd9b44168cd90393b492ffa833fe3a4576a2322bcd307dadbc9d1', 'work/profit_1000_upgrade/candidate_d_feature_projection.py': 'aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f', 'work/profit_1000_upgrade/candidate_live_feature_math.py': '922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68', 'src/top10decision/decision/d_close_features.py': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e', 'src/top10decision/decision/three_rank.py': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a'}}

def match_publication_code(code_tree, published_tree, local_files, *, day=None):
    """Bind one complete reviewed code version in both immutable Git trees."""
    bindings = [{"path": p, "sha256": gh.sha256(b), "git_blob_sha1": gh.git_blob(b)}
        for p, b in sorted(local_files.items())]
    previous = {item["path"]: item for item in PRE_BOUNDARY_CODE_BINDINGS}
    pre_boundary = code_tree.get(IMPORTER_PATH, {}).get("sha") == previous[IMPORTER_PATH]["git_blob_sha1"]
    if pre_boundary:
        require(type(day) is str and gh.date(day) < "20261028" and set(local_files) == set(previous),
                "PRE_BOUNDARY_PUBLICATION_DATE_OR_CODE_SET_CHANGED")
        bindings = [dict(previous[p]) for p in sorted(previous)]
    historical = {item["path"]: item for item in HISTORICAL_CODE_BINDINGS}
    historical_profile = code_tree.get(IMPORTER_PATH, {}).get("sha") == historical[IMPORTER_PATH]["git_blob_sha1"]
    if historical_profile:
        historical_paths = set(local_files) - CONTINUATION_ONLY_FILES
        require(historical_paths == set(historical), "HISTORICAL_PUBLICATION_CODE_SET_CHANGED")
        bindings = [dict(historical[p]) for p in sorted(historical_paths)]
    current_legacy_raw = local_files["work/profit_1000_upgrade/candidate_legacy_workflow.py"]
    current_legacy = {"path": COORDINATOR_PATH, "sha256": gh.sha256(current_legacy_raw),
        "git_blob_sha1": gh.git_blob(current_legacy_raw)}
    expected_legacy = (LEGACY_COORDINATOR_BINDING if historical_profile else
        {**previous["work/profit_1000_upgrade/candidate_legacy_workflow.py"], "path": COORDINATOR_PATH}
        if pre_boundary else current_legacy)
    legacy_coordinator = expected_legacy if code_tree.get(COORDINATOR_PATH, {}).get("sha") == expected_legacy["git_blob_sha1"] else None
    if legacy_coordinator:
        bindings = [dict(legacy_coordinator) if b["path"] == COORDINATOR_PATH else b
            for b in bindings if b["path"] not in ELIGIBLE_ONLY_FILES]
    legacy = code_tree.get(WORKFLOW_PATH, {}).get("sha") == LEGACY_WORKFLOW_BINDING["git_blob_sha1"]
    if legacy:
        require(legacy_coordinator == LEGACY_COORDINATOR_BINDING, "LEGACY_WORKFLOW_CANNOT_RUN_NEW_COORDINATOR")
        bindings = [dict(LEGACY_WORKFLOW_BINDING) if b["path"] == WORKFLOW_PATH else b for b in bindings]
    for binding in bindings:
        for tree in (code_tree, published_tree):
            entry = tree.get(binding["path"], {})
            require(entry.get("type") == "blob" and entry.get("mode") == "100644"
                and entry.get("sha") == binding["git_blob_sha1"], "REVIEWED_PUBLICATION_CODE_VERSION_CHANGED")
    return bindings


class _Reads:
    """Finite GET facade; no injected client can issue observed authority."""
    def __init__(self, client):
        self.client, self.cost = client, 0
        self.actual = type(client) is gh.GitHubReadClient
        require(not self.actual or client.calls == 0, "FRESH_READ_CLIENT_REQUIRED")
        self.bound = {}

    def json(self, suffix, *, immutable=True):
        self.cost += 1
        require(self.cost <= MAX_CALLS, "PUBLICATION_GET_BUDGET_EXCEEDED")
        value = self.client.get_json(gh.API_PREFIX + suffix)
        require(type(value) is dict, "GITHUB_JSON_OBJECT_REQUIRED")
        if immutable:
            raw = gh.json_bytes(value)
            require(suffix not in self.bound or self.bound[suffix] == raw, "GITHUB_EVIDENCE_CHANGED")
            self.bound[suffix] = raw
        return value

    def archive(self, artifact):
        self.cost += 2
        require(self.cost <= MAX_CALLS, "PUBLICATION_GET_BUDGET_EXCEEDED")
        raw = self.client.get_artifact_zip(artifact["id"])
        require(type(raw) is bytes and len(raw) == artifact["size_in_bytes"]
            and gh.sha256(raw) == artifact["digest"][7:], "ORIGINAL_ARTIFACT_DIGEST_MISMATCH")
        return raw

    def tree(self, commit_sha):
        gh.exact_sha(commit_sha, 40)
        commit = self.json("/git/commits/" + commit_sha)
        require(commit.get("sha") == commit_sha and type(commit.get("tree")) is dict,
            "EXACT_GIT_COMMIT_REQUIRED")
        tree_sha = gh.exact_sha(commit["tree"].get("sha"), 40)
        return commit, gh.validate_tree(self.json("/git/trees/" + tree_sha + "?recursive=1"), tree_sha)


def validate_run(run, registration, run_id):
    require(type(registration.get("id")) is int and registration["id"] == WORKFLOW_ID
        and registration.get("path") == WORKFLOW_PATH and registration.get("name") == WORKFLOW_NAME
        and registration.get("state") == "active", "REGISTERED_FREEZE_WORKFLOW_REQUIRED")
    require(type(run.get("id")) is int and run["id"] == int(run_id)
        and type(run.get("workflow_id")) is int and run["workflow_id"] == registration["id"]
        and run.get("path") == WORKFLOW_PATH and run.get("name") == WORKFLOW_NAME
        and run.get("head_branch") == "main" and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("event") in ("workflow_run", "workflow_dispatch")
        and run.get("repository", {}).get("full_name") == gh.REPOSITORY
        and run.get("head_repository", {}).get("full_name") == gh.REPOSITORY, "SUCCESSFUL_FIRST_MAIN_FREEZE_RUN_REQUIRED")
    gh.exact_sha(run.get("head_sha"), 40)
    require(gh.utc(run.get("created_at")) <= gh.utc(run.get("run_started_at")) <= gh.utc(run.get("updated_at")),
        "FREEZE_RUN_TIME_ORDER_INVALID")


def validate_jobs(document, run):
    jobs = document.get("jobs")
    require(type(jobs) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(jobs) == 2, "EXACT_VALIDATE_AND_FREEZE_JOBS_REQUIRED")
    selected, ids = {}, set()
    for job in jobs:
        require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0 and job["id"] not in ids
            and type(job.get("run_id")) is int and job["run_id"] == run["id"]
            and job.get("name") in JOBS and job["name"] not in selected
            and job.get("status") == "completed" and job.get("conclusion") == "success",
            "FREEZE_JOB_IDENTITY_OR_SUCCESS_INVALID")
        ids.add(job["id"])
        require(gh.utc(run["run_started_at"]) <= gh.utc(job.get("started_at")) <= gh.utc(job.get("completed_at"))
            <= gh.utc(run["updated_at"]), "FREEZE_JOB_TIME_ORDER_INVALID")
        selected[job["name"]] = {k: job[k] for k in ("id", "run_id", "name", "status", "conclusion", "started_at", "completed_at")}
    ordered = [selected[name] for name in JOBS]
    require(gh.utc(ordered[0]["completed_at"]) <= gh.utc(ordered[1]["started_at"]), "VALIDATION_MUST_PRECEDE_FREEZE")
    return ordered


def select_artifact(document, run):
    entries = document.get("artifacts")
    require(type(entries) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(entries) <= 100 and all(type(e) is dict for e in entries), "COMPLETE_FREEZE_ARTIFACT_LIST_REQUIRED")
    selected = [x for x in entries if x.get("name") == f"dc20-candidate-natural-{run['id']}-1"]
    require(len(selected) == 1, "UNIQUE_SAME_FREEZE_RUN_ARTIFACT_REQUIRED")
    artifact = selected[0]
    origin = artifact.get("workflow_run", {})
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ZIP_BYTES
        and type(origin.get("id")) is int and origin["id"] == run["id"] and origin.get("head_branch") == "main"
        and origin.get("head_sha") == run["head_sha"], "FREEZE_ARTIFACT_ORIGIN_INVALID")
    require(type(artifact.get("digest")) is str and artifact["digest"].startswith("sha256:"), "ARTIFACT_EXTERNAL_DIGEST_REQUIRED")
    gh.exact_sha(artifact["digest"][7:])
    require(gh.utc(run["run_started_at"]) <= gh.utc(artifact.get("created_at")) <= gh.utc(artifact.get("updated_at"))
        <= gh.utc(run["updated_at"]), "ARTIFACT_TIME_OUTSIDE_FREEZE_RUN")
    return {k: artifact[k] for k in ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "updated_at")}


def _source_path(relative, day):
    if relative in {*gh.adapter._p0_paths(day).values(), gh.adapter.CALENDAR_PATH, gh.adapter.HISTORY_PATH, gh.adapter.SUSPENSION_PATH}:
        return
    m = re.fullmatch(r"data/market/raw/(20[0-9]{2})/(20[0-9]{6})/(daily\.csv|_sync_meta\.json)", relative)
    require(m is not None and m[1] == m[2][:4] and gh.date(m[2]) <= day
        and (m[3] == "daily.csv" or m[2] == day), "NON_D_ONLY_OR_UNKNOWN_ARCHIVE_SOURCE")


def read_archive(raw):
    """No extraction. Reject every unknown path before opening source bodies."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ZIP_BYTES, "FREEZE_ARCHIVE_SIZE_LIMIT")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(0 < len(members) <= MAX_MEMBERS, "FREEZE_ARCHIVE_MEMBER_LIMIT")
            entries, directories, total = {}, set(), 0
            for info in members:
                name = gh.relative(info.filename[:-1] if info.is_dir() else info.filename)
                require(name not in entries and name not in directories and not info.flag_bits & 1,
                    "DUPLICATE_OR_ENCRYPTED_ARCHIVE_MEMBER")
                mode = (info.external_attr >> 16) & 0o170000
                require(mode in ((0, stat.S_IFDIR) if info.is_dir() else (0, stat.S_IFREG)), "REGULAR_ARCHIVE_MEMBERS_ONLY")
                require(0 <= info.file_size <= gh.MAX_SOURCE_BYTES, "ARCHIVE_MEMBER_SIZE_LIMIT")
                total += info.file_size
                require(total <= MAX_ARCHIVE_BYTES, "ARCHIVE_TOTAL_SIZE_LIMIT")
                if info.is_dir():
                    require(info.file_size == 0, "NONEMPTY_ARCHIVE_DIRECTORY")
                    directories.add(name)
                else:
                    entries[name] = info
            acknowledgements = [p for p in entries if PurePosixPath(p).name == "publication.json"]
            require(len(acknowledgements) == 1, "ONE_ORIGINAL_PUBLICATION_ACK_REQUIRED")
            ack_path = acknowledgements[0]
            prefix = ack_path.removesuffix("publication.json")
            require(prefix == "" or re.fullmatch(r"dc20-candidate-natural-[A-Za-z0-9_-]+/", prefix), "EXACT_ARTIFACT_WORK_ROOT_REQUIRED")
            # Non-source names can already be rejected without reading the ACK.
            static = {"publication.json", "p0_source_receipt.json", "local_freeze_receipt.json"}
            for p in entries:
                require(p.startswith(prefix), "MULTIPLE_ARTIFACT_WORK_ROOTS")
                rel = p[len(prefix):]
                require(rel in static or rel.startswith("sources/") or re.fullmatch(r"candidate_natural_forward/day_[0-9]{8}\.json(?:\.lock)?", rel),
                    "UNKNOWN_ARTIFACT_MEMBER")
            def read(name, limit=gh.MAX_SOURCE_BYTES):
                info = entries[prefix + name]
                require(info.file_size <= limit, "ARTIFACT_JSON_SIZE_LIMIT")
                with archive.open(info) as f:
                    value = f.read(limit + 1)
                require(len(value) == info.file_size, "ARCHIVE_MEMBER_LENGTH_CHANGED")
                return value
            ack_raw = read("publication.json", MAX_JSON_BYTES)
            ack = gh.parse_json(ack_raw)
            day = gh.date(ack.get("signal_date"))
            require(day >= "20260914", "NATURAL_PUBLICATION_START_REQUIRED")
            snap_name = f"candidate_natural_forward/day_{day}.json"
            for p in entries:
                rel = p[len(prefix):]
                if rel.startswith("sources/"):
                    _source_path(rel[8:], day)
                else:
                    require(rel in static | {snap_name, snap_name + ".lock"}, "WRONG_DAY_ARCHIVE_MEMBER")
            allowed_dirs = {str(parent) for p in entries for parent in PurePosixPath(p).parents if str(parent) != "."}
            require(directories <= allowed_dirs and not any(p in entries for p in allowed_dirs), "ARCHIVE_DIRECTORY_OR_PARENT_COLLISION")
            receipt_raw = read("p0_source_receipt.json", MAX_JSON_BYTES)
            receipt = gh.parse_json(receipt_raw)
            bindings = receipt.get("source_file_bindings")
            require(type(bindings) is list and 28 <= len(bindings) <= 50, "EXACT_28_ARCHIVED_SOURCE_BINDINGS")
            wanted = set()
            for b in bindings:
                require(type(b) is dict and set(b) == {"path", "sha256", "git_blob_sha1", "git_mode", "bytes"}, "ORIGINAL_SOURCE_BINDING_SHAPE")
                _source_path(b["path"], day)
                require(b["path"] not in wanted and b["git_mode"] == "100644" and type(b["bytes"]) is int
                    and 0 <= b["bytes"] <= gh.MAX_SOURCE_BYTES, "DUPLICATE_OR_INVALID_SOURCE_BINDING")
                gh.exact_sha(b["sha256"]); gh.exact_sha(b["git_blob_sha1"], 40)
                wanted.add(b["path"])
            expected = {prefix + p for p in static | {snap_name} | {"sources/" + p for p in wanted}}
            require(set(entries) - {prefix + snap_name + ".lock"} == expected, "EXACT_ORIGINAL_ARCHIVE_INVENTORY_REQUIRED")
            if prefix + snap_name + ".lock" in entries:
                require(read(snap_name + ".lock", 0) == b"", "NONEMPTY_CAS_LOCK")
            sources = {}
            for b in bindings:
                body = read("sources/" + b["path"])
                require(len(body) == b["bytes"] and gh.sha256(body) == b["sha256"] and gh.git_blob(body) == b["git_blob_sha1"],
                    "ARCHIVED_SOURCE_BYTES_CHANGED")
                sources[b["path"]] = body
            p0_paths = gh.adapter._p0_paths(day)
            gh.validate_original_source_inventory(gh.parse_json(sources[p0_paths["receipt"]]),
                gh.parse_json(sources[p0_paths["three_rank_json"]]), sources, day)
            return {"ack": ack, "ack_raw": ack_raw, "source_receipt": receipt, "source_receipt_raw": receipt_raw,
                "local_raw": read("local_freeze_receipt.json", MAX_JSON_BYTES), "snapshot_raw": read(snap_name, MAX_JSON_BYTES),
                "sources": sources, "prefix": prefix}
    except (zipfile.BadZipFile, zlib.error, EOFError, UnicodeError):
        raise ValueError("INVALID_FREEZE_ARCHIVE") from None


def _blob_matches(entries, relative, raw):
    node = entries.get(relative)
    require(type(node) is dict and node.get("type") == "blob" and node.get("mode") == "100644"
        and type(node.get("size")) is int and node["size"] == len(raw) and node.get("sha") == gh.git_blob(raw),
        "ORIGINAL_GIT_BLOB_BINDING_MISMATCH:" + relative)


def _snapshot(raw, day):
    value = natural._json(raw)
    if value.get("schema_version") == "dc20_fixed_candidate_eligible_research_snapshot_20260922_v2":
        path = ROOT / "work/profit_1000_upgrade/candidate_snapshot_versions.py"
        require(gh.sha256(gh.read(path)[0]) == FIXED_FILES["work/profit_1000_upgrade/candidate_snapshot_versions.py"], "ELIGIBLE_PROFILE_READER_CHANGED")
        from work.profit_1000_upgrade import candidate_snapshot_versions as profiles
        require(Path(profiles.__file__).absolute() == path, "ELIGIBLE_PROFILE_IMPORT_CHANGED")
        result = profiles.validate_snapshot(raw,gh.sha256(raw))
        require(result["signal_date"] == day and result["clock_mode"] == "HOST_SYSTEM_UTC", "NATURAL_ELIGIBLE_SNAPSHOT_REQUIRED")
        return result
    natural._sealed(value)
    provenance = natural.snapshot_profile(value)
    for key, expected in {"schema_version": natural.SCHEMA, "signal_date": day,
            "registration_id": natural.REGISTRATION_ID, **provenance, "model_canonical_sha256": natural.MODEL_SHA,
            "clock_mode": "HOST_SYSTEM_UTC", **natural.BOUNDARIES}.items():
        natural.scorer._exact(value.get(key), expected, "EXACT_ORIGINAL_RESEARCH_SNAPSHOT_REQUIRED:" + key)
    require(value["model_evaluation"]["sha256"] == natural.EVALUATION_SHA, "FIXED_MODEL_EVALUATION_REQUIRED")
    prediction = value["prediction"]
    require(prediction["schema_version"] == natural.scorer.SCHEMA and prediction["signal_date"] == day
        and prediction["model_sha256"] == natural.MODEL_SHA, "FROZEN_PREDICTION_CONTRACT_CHANGED")
    for key, expected in natural.scorer.FLAGS.items():
        natural.scorer._exact(prediction.get(key), expected, "FROZEN_PREDICTION_FLAG_CHANGED")
    rows = prediction["rows"]
    projected = natural.scorer._project(rows, day)  # All identities precede feature/score use.
    require(type(prediction["candidate_count"]) is int and prediction["candidate_count"] == len(rows), "FULL_N_CHANGED")
    for index, row in enumerate(rows, 1):
        require(type(row.get("candidate_rank")) is int and row["candidate_rank"] == index, "EXACT_CANDIDATE_RANK_REQUIRED")
        natural.scorer._number(row["candidate_score"], "candidate_score")
    require(rows == sorted(rows, key=lambda r: (-r["candidate_score"], r["ts_code"])), "SCORE_ORDER_CHANGED")
    original = sorted(projected, key=lambda r: r["promotion_rank"])
    evidence = value["D_source_evidence"]
    expected_projection = natural.scorer._project(evidence["projection"]["rows"], day)
    require(natural.scorer.canonical_sha(original) == prediction["whitelisted_D_rows_sha256"]
        == natural.scorer.canonical_sha(expected_projection), "FROZEN_FULL_N_FEATURE_BINDING_CHANGED")
    for name, rank in (("candidate_slots", "candidate_rank"), ("promotion_slots", "promotion_rank")):
        natural.scorer._exact(value[name], natural._slots(rows, rank), "FOUR_FROZEN_SLOTS_CHANGED")
    return value


def _p0_contract(receipt, contract, snapshot, revision, imported):
    day = snapshot["signal_date"]
    require(receipt.get("generation_mode") == "NATURAL" and receipt.get("primary_status") == "READY"
        and receipt.get("prospective") is True and receipt.get("forward_eligible") is True
        and all(receipt.get(k) is False for k in ("not_forward_generated", "future_market_data_consumed", "latest_fallback_used",
            "action_authorized", "action_input_consumed"))
        and type(receipt.get("formal_trade_count")) is int and receipt["formal_trade_count"] == 0,
        "ORIGINAL_NATURAL_P0_RECEIPT_REQUIRED")
    require([receipt.get(k) for k in ("signal_date", "exec_date", "exit_date")]
        == [contract.get(k) for k in ("signal_date", "exec_date", "exit_date")]
        == [snapshot[k] for k in ("signal_date", "exec_date", "exit_date")]
        and receipt["inputs"]["git_head"] == imported["published_parent_sha"]
        and contract.get("bundle_sha256") == revision["primary_d_bundle_sha256"], "ORIGINAL_P0_CONTRACT_IDENTITY_CHANGED")
    rows = contract.get("rows")
    eligible = snapshot["schema_version"] == "dc20_fixed_candidate_eligible_research_snapshot_20260922_v2"
    if eligible:
        _snapshot(gh.json_bytes(snapshot),day)
    frozen = sorted(snapshot["prediction"]["promotion_rows" if eligible else "rows"], key=lambda r: r["promotion_rank"])
    require(type(rows) is list and type(contract.get("top10_count")) is int and len(rows) == contract["top10_count"]
        == len(frozen) == revision["primary_d_top10_count"], "ORIGINAL_FULL_N_P0_MEMBERSHIP_CHANGED")
    for rank, (original, projected) in enumerate(zip(rows, frozen), 1):
        require(type(original) is dict and original.get("ts_code") == projected["ts_code"]
            and type(original.get("promotion_rank")) is int and original["promotion_rank"] == rank
            and original.get("stage_transition") == {2: "2→3", 3: "3→4"}[projected["board_stage"]],
            "ORIGINAL_P0_RANK_OR_STAGE_CHANGED")
    evidence = snapshot["D_source_evidence"]
    require(evidence.get("signal_date") == evidence["projection"].get("signal_date") == day
        and evidence["projection"].get("exec_date") == snapshot["exec_date"]
        and evidence["projection"].get("exit_date") == snapshot["exit_date"]
        and (eligible or evidence["projection"].get("projection_window_complete") is True)
        and evidence.get("four_file_bytes_verified") is True and evidence.get("registered_source_bytes_verified") is True
        and all(evidence.get(k) is False for k in ("source_authority_issued", "git_membership_verified",
            "point_in_time_availability_verified", "natural_freeze_verified", "production_activation_allowed", "future_outcomes_read")),
        "ORIGINAL_D_SOURCE_PROJECTION_QUALIFICATION_CHANGED")
    require(snapshot["input_arguments"]["source_path_map"] == {}, "ORIGINAL_IMPORT_CANNOT_USE_SOURCE_PATH_REMAP")
    for b in evidence["source_file_bindings"]:
        require(b["origin_path"] == imported["source_root"].rstrip("/") + "/" + b["receipt_path"]
            and b["explicit_original_blob_mapping"] is False, "ORIGINAL_IMPORT_SOURCE_ORIGIN_CHANGED")


def verify_publication(*, expected_freeze_run_id, github_client):
    """Return an observation, not a mutable source/settlement authority object."""
    run_id = gh.exact_id(expected_freeze_run_id)
    local_files, before = code_guard()
    reads = _Reads(github_client)
    run_path = "/actions/runs/" + run_id
    workflow_path = "/actions/workflows/research_candidate_natural_forward.yml"
    registration = reads.json(workflow_path)
    run = reads.json(run_path)
    validate_run(run, registration, run_id)
    jobs = validate_jobs(reads.json(run_path + "/attempts/1/jobs?per_page=100"), run)
    artifact = select_artifact(reads.json(run_path + "/artifacts?per_page=100"), run)
    archive = read_archive(reads.archive(artifact))
    ack, imported = archive["ack"], archive["source_receipt"]
    day = ack["signal_date"]
    require(ack.get("schema_version") == "dc20_natural_candidate_git_publication_ack_v1"
        and ack.get("status") == "GIT_PUBLICATION_ACKNOWLEDGED" and ack.get("existing_files_modified") is False
        and ack.get("timestamp_basis") == "HOST_CLOCK_AFTER_GITHUB_NONFORCE_REF_ACK"
        and ack.get("independent_job_timing_check_still_required") is True
        and all(ack.get(k) is False for k in ("natural_forward_admission_issued", "production_activation_allowed", "actual_execution_claimed")),
        "SUCCESSFUL_ORIGINAL_ACK_REQUIRED")
    published = gh.exact_sha(ack.get("commit_sha"), 40)
    commit, tree = reads.tree(published)
    require(type(commit.get("parents")) is list and len(commit["parents"]) == 1
        and commit["parents"][0].get("sha") == ack.get("parent_sha") and commit["tree"]["sha"] == ack.get("tree_sha"),
        "ACK_PUBLISHED_PARENT_OR_TREE_CHANGED")
    _, parent_tree = reads.tree(gh.exact_sha(ack["parent_sha"], 40))
    _, code_tree = reads.tree(run["head_sha"])
    code_bindings = match_publication_code(code_tree, tree, local_files, day=day)
    files = ack.get("files")
    names = {PREFIX + f"{kind}_{day}.json" for kind in ("day", "p0_sources", "local_freeze", "workflow")}
    require(type(files) is list and len(files) == 4 and {b.get("path") for b in files if type(b) is dict} == names,
        "EXACT_FOUR_PUBLISHED_FILES_REQUIRED")
    blobs = {}
    for b in files:
        require(set(b) == {"path", "sha256", "git_blob_sha1", "bytes"}, "ACK_FILE_BINDING_SHAPE")
        gh.exact_sha(b["sha256"]); gh.exact_sha(b["git_blob_sha1"], 40)
        # Original API blob, not a caller-supplied reconstructed JSON.
        reads.cost += 1
        require(reads.cost <= MAX_CALLS, "PUBLICATION_GET_BUDGET_EXCEEDED")
        body = gh.load_blob(github_client, tree, b["path"])
        require(type(b["bytes"]) is int and len(body) == b["bytes"] and gh.sha256(body) == b["sha256"]
            and gh.git_blob(body) == b["git_blob_sha1"], "ACK_ORIGINAL_FILE_SHA_MISMATCH")
        blobs[b["path"]] = body
    leaves = lambda entries: {p: (v["mode"], v["type"], v["sha"]) for p, v in entries.items() if v["type"] != "tree"}
    old, new = leaves(parent_tree), leaves(tree)
    require(not names.intersection(parent_tree) and new == {**old, **{p: ("100644", "blob", gh.git_blob(b)) for p, b in blobs.items()}},
        "PUBLICATION_MUST_ADD_ONLY_FOUR_NEW_FILES")
    parents = {str(p) for name in names for p in PurePosixPath(name).parents if str(p) != "."}
    require(set(tree) == set(parent_tree) | names | parents
        and all(tree[p]["type"] == "tree" and tree[p]["mode"] == "040000" for p in parents)
        and all(tree[p] == value for p, value in parent_tree.items() if value["type"] == "tree" and p not in parents),
        "PUBLICATION_UNRELATED_DIRECTORY_CHANGE")
    snapshot_raw, source_raw, local_raw, context_raw = [blobs[PREFIX + f"{kind}_{day}.json"]
        for kind in ("day", "p0_sources", "local_freeze", "workflow")]
    require(snapshot_raw == archive["snapshot_raw"] and source_raw == archive["source_receipt_raw"]
        and local_raw == archive["local_raw"], "ARTIFACT_AND_PUBLISHED_ORIGINAL_BYTES_DIFFER")
    snapshot = _snapshot(snapshot_raw, day)
    is_eligible = snapshot["schema_version"] == eligible_runner.SCHEMA
    coordinator_binding = next(b for b in code_bindings if b["path"] == COORDINATOR_PATH)
    code_profile = {binding["path"]: binding["sha256"] for binding in code_bindings}
    runner_path = "work/profit_1000_upgrade/candidate_eligible_forward.py" if is_eligible else "work/profit_1000_upgrade/candidate_natural_forward.py"
    require(snapshot["runner_sha256"] == code_profile[runner_path], "SNAPSHOT_RUNNER_AND_GIT_PROFILE_MISMATCH")
    require(coordinator_binding["sha256"] == code_profile[COORDINATOR_PATH]
        and (is_eligible or coordinator_binding["sha256"] in {LEGACY_COORDINATOR_BINDING["sha256"], legacy_workflow.SELF_SHA}),
        "SNAPSHOT_AND_COORDINATOR_PROFILE_MISMATCH")
    local, context = gh.parse_json(local_raw), gh.parse_json(context_raw)
    expected_context = {"repository": gh.REPOSITORY, "run_id": int(run_id), "run_attempt": 1,
        "code_head_sha": run["head_sha"], "branch": "main", "workflow_path": WORKFLOW_PATH, "signal_date": day,
        "snapshot_file_sha256": gh.sha256(snapshot_raw), "source_import_receipt_sha256": gh.sha256(source_raw),
        "local_freeze_receipt_sha256": gh.sha256(local_raw), "publication_timing_verified": False,
        "production_activation_allowed": False, "independent_job_timing_check_still_required": True}
    natural.scorer._exact(context, expected_context, "PUBLISHED_WORKFLOW_CONTEXT_CHANGED")
    # Pure validation of the original four return payloads; this does no write.
    prepared, safety_cutoff = (workflow if is_eligible else legacy_workflow).prepare_publication(snapshot_raw, local, imported,
        {k: context[k] for k in ("repository", "run_id", "run_attempt", "code_head_sha", "branch", "workflow_path")},
        now=workflow.aware(ack["acknowledged_at_host_utc"]),
        **({"original_sources": archive["sources"]} if is_eligible else {}))
    require(prepared == blobs, "ORIGINAL_FOUR_FILE_SERIALIZATION_CHANGED")
    close, cutoff = natural._window(day, snapshot["exec_date"])
    generated, finished, acknowledged = (workflow.aware(x) for x in (snapshot["prediction_generated_at_utc"],
        local["local_operation_completed_at_utc"], ack["acknowledged_at_host_utc"]))
    require(gh.utc(jobs[1]["started_at"]) <= generated <= finished <= acknowledged < safety_cutoff
        and acknowledged <= gh.utc(jobs[1]["completed_at"]) < cutoff,
        "INDEPENDENT_FREEZE_JOB_MISSES_PRE_AUCTION_DEADLINE")
    # Independently revisit the original P0 API evidence, without reimporting.
    p0_id = gh.exact_id(str(imported["run"]["id"]))
    p0_path = "/actions/runs/" + p0_id
    continuation_proof = None
    if imported.get("schema_version") == "dc20_candidate_p0_continuation_import_v1":
        require(code_profile[IMPORTER_PATH] == IMPORTER_SHA, "CONTINUATION_REQUIRES_CURRENT_COMPLETE_CODE_PROFILE")
        continuation, _ = gh.continuation_dependencies()
        expected_proof = imported["continuation"]
        continuation_proof, checked_sources, p0_revision = continuation.verify_pair(gh, github_client,
            original_run_id=p0_id, original_cas_commit=imported["published_git_sha"],
            reuse_run_id=str(expected_proof["successful_reuse_run"]["id"]),
            original_sources=archive["sources"], source_main_sha=expected_proof["source_main_sha"])
        require(continuation_proof == expected_proof and checked_sources == archive["sources"],
            "ORIGINAL_CONTINUATION_PAIR_OR_SOURCES_CHANGED")
        p0_jobs = continuation_proof["original_generation_jobs"]
        p0_artifact = continuation_proof["successful_reuse_pages_artifact"]
        p0_commit = continuation_proof["original_cas_commit"]
        require(imported["run"] == continuation_proof["original_generation_run"]
            and imported["critical_jobs"] == p0_jobs and imported["pages_artifact"] == p0_artifact
            and imported["pages_revision_sha256"] == continuation_proof["successful_reuse_revision_sha256"]
            and [continuation_proof[k] for k in ("signal_date", "exec_date", "exit_date")]
                == [day, snapshot["exec_date"], snapshot["exit_date"]],
            "ORIGINAL_CONTINUATION_RECEIPT_IDENTITY_CHANGED")
    else:
        p0_run = reads.json(p0_path)
        require(gh.validate_run(p0_run, p0_id) == imported["run"], "ORIGINAL_P0_RUN_CHANGED")
        p0_jobs = gh.validate_jobs(reads.json(p0_path + "/attempts/1/jobs?per_page=100"), p0_run, p0_id)
        p0_artifact = gh.select_artifact(reads.json(p0_path + "/artifacts?per_page=100"), p0_run, p0_id)
        require(p0_jobs == imported["critical_jobs"] and p0_artifact == imported["pages_artifact"], "ORIGINAL_P0_JOBS_OR_ARTIFACT_CHANGED")
        p0_revision, revision_sha = gh.revision_from_zip(reads.archive(p0_artifact))
        p0_day, p0_t, p0_t1, p0_commit = gh.validate_revision(p0_revision, p0_run, p0_id)
        require([p0_day, p0_t, p0_t1] == [day, snapshot["exec_date"], snapshot["exit_date"]]
            and p0_commit == imported["published_git_sha"] and revision_sha == imported["pages_revision_sha256"], "ORIGINAL_P0_REVISION_CHANGED")
    p0_commit_value, p0_tree = reads.tree(p0_commit)
    require(p0_commit_value["tree"]["sha"] == imported["published_tree_sha"]
        and [p["sha"] for p in p0_commit_value.get("parents", [])] == [imported["published_parent_sha"]], "ORIGINAL_P0_GIT_IDENTITY_CHANGED")
    sources = archive["sources"]
    p0_paths = gh.adapter._p0_paths(day)
    receipt, contract = (gh.parse_json(sources[p0_paths[k]]) for k in ("receipt", "three_rank_json"))
    _p0_contract(receipt, contract, snapshot, p0_revision, imported)
    registered, daily, _, _ = gh.adapter._registration(receipt, contract, day)
    class BoundaryReads:
        def get_json(self, path):
            require(path.startswith(gh.API_PREFIX + "/git/"), "BOUNDARY_GIT_READ_ONLY")
            return reads.json(path[len(gh.API_PREFIX):])
    gh.validate_boundary_base_membership(receipt, p0_tree, BoundaryReads())
    gh.adapter._suspension_provenance(receipt, sources, receipt["inputs"]["calendar"]["runtime_context_dates"], day)
    require(set(sources) == set(registered) | set(p0_paths.values()), "ORIGINAL_28_REGISTERED_SOURCE_SET_CHANGED")
    for path, raw in sources.items():
        _blob_matches(p0_tree, path, raw)
        if path in registered:
            require(gh.sha256(raw) == registered[path], "ORIGINAL_REGISTERED_SOURCE_SHA_CHANGED")
    require(imported["expected_p0_sha256"] == {k: gh.sha256(sources[p]) for k, p in p0_paths.items()}, "ORIGINAL_FOUR_P0_HASHES_CHANGED")
    for b in daily:
        if b["trade_date"] != day:
            require(b["git_blob_sha1"] == p0_tree[b["path"]]["sha"], "ORIGINAL_HISTORICAL_GIT_BLOB_CHANGED")
    calendar = receipt["inputs"]["calendar"]
    require(calendar.get("git_mode") == "100644" and calendar.get("git_blob_sha1") == p0_tree[gh.adapter.CALENDAR_PATH]["sha"],
        "ORIGINAL_CALENDAR_GIT_BLOB_CHANGED")
    import_profile = {"importer_sha256": IMPORTER_SHA, "source_adapter_sha256": gh.ADAPTER_SHA,
        "source_adapter_dependency_sha256": gh.adapter.CODE_PINS}
    if code_profile[IMPORTER_PATH] == PRE_BOUNDARY_IMPORT_PROFILE["importer_sha256"]:
        require(day < "20261028", "PRE_BOUNDARY_IMPORT_DATE_CHANGED")
        import_profile = PRE_BOUNDARY_IMPORT_PROFILE
    if code_profile[IMPORTER_PATH] == HISTORICAL_IMPORT_PROFILE["importer_sha256"]:
        import_profile = HISTORICAL_IMPORT_PROFILE
    require(all(imported.get(key) == expected for key, expected in import_profile.items())
        and imported.get("github_original_byte_bindings_verified") is True
        and imported.get("p0_native_four_file_contract_checked") is True
        and all(imported.get(k) is False for k in ("source_authority_issued", "complete_d_feature_admission_performed",
            "new_prediction_freeze_verified", "production_activation_allowed", "model_training_performed", "model_predictions_computed", "future_outcomes_read")),
        "ORIGINAL_IMPORT_RECEIPT_QUALIFICATIONS_CHANGED")
    require(close <= gh.utc(p0_jobs[1]["completed_at"]) <= generated
        and imported["observed_p0_cas_job_completed_at"] == p0_jobs[1]["completed_at"], "ORIGINAL_P0_CAS_TIME_CHANGED")
    model = gh.parse_json(local_files[MODEL_PATH])["candidate_model"]
    natural.scorer._model(model, natural.MODEL_SHA, day)  # Validation only, not scoring.
    # Re-observe mutable run/job/artifact metadata at the final boundary.
    for suffix in (workflow_path, run_path, run_path + "/attempts/1/jobs?per_page=100", run_path + "/artifacts?per_page=100",
            p0_path, p0_path + "/attempts/1/jobs?per_page=100", p0_path + "/artifacts?per_page=100"):
        reads.json(suffix)
    if continuation_proof is not None:
        # Match both completed API identities after the common immutable checks.
        for original, run_key, job_key, artifact_key in (
                (True, "original_generation_run", "original_generation_jobs", "original_generation_artifact"),
                (False, "successful_reuse_run", "successful_reuse_jobs", "successful_reuse_pages_artifact")):
            expected_run = continuation_proof[run_key]
            identity = str(expected_run["id"])
            route = gh.API_PREFIX + "/actions/runs/" + identity
            actual_run = github_client.get_json(route)
            actual_bound = (continuation.validate_original_run(gh, actual_run, identity, day) if original
                else gh.validate_run(actual_run, identity))
            actual_jobs = continuation._jobs(gh, github_client.get_json(route + "/attempts/1/jobs?per_page=100"),
                actual_run, identity, original=original)
            artifact_document = github_client.get_json(route + "/artifacts?per_page=100")
            actual_artifact = (continuation.select_generation_artifact(gh, artifact_document, actual_run, identity)
                if original else gh.select_artifact(artifact_document, actual_run, identity))
            require(actual_bound == expected_run and actual_jobs == continuation_proof[job_key]
                and actual_artifact == continuation_proof[artifact_key], "CONTINUATION_FINAL_API_IDENTITY_CHANGED")
    current = reads.json("/git/ref/heads/main", immutable=False)
    require(current.get("ref") == "refs/heads/main" and current.get("object", {}).get("type") == "commit",
        "EXACT_CURRENT_MAIN_REF_REQUIRED")
    current_sha = gh.exact_sha(current["object"].get("sha"), 40)
    _, current_tree = reads.tree(current_sha)
    for path, raw in blobs.items():
        _blob_matches(current_tree, path, raw)
    require(code_guard() == (local_files, before), "LOCAL_PUBLICATION_CODE_OR_MODEL_CHANGED")
    actual = reads.actual
    return {"schema_version": SCHEMA, "status": "RESEARCH_PROSPECTIVE_PUBLICATION_OBSERVED" if actual else "SYNTHETIC_PUBLICATION_CHECK_ONLY",
        "research_prospective_publication_observed": actual, "injected_client_for_test": not actual,
        "repository": gh.REPOSITORY, "freeze_run_id": int(run_id), "freeze_code_head_sha": run["head_sha"],
        "signal_date": day, "exec_date": snapshot["exec_date"], "exit_date": snapshot["exit_date"],
        "published_commit_sha": published, "published_tree_sha": ack["tree_sha"], "published_parent_sha": ack["parent_sha"],
        "snapshot_file_sha256": gh.sha256(snapshot_raw), "snapshot_sha256": snapshot["snapshot_sha256"],
        "publication_ack_sha256": gh.sha256(archive["ack_raw"]), "publication_artifact": artifact,
        "published_file_bindings": files, "original_p0_run_id": int(p0_id), "original_p0_commit_sha": p0_commit,
        **({"p0_provenance_kind": "ORIGINAL_GENERATION_REDEPLOYMENT", "p0_continuation": continuation_proof}
            if continuation_proof is not None else {}),
        "original_source_bindings": imported["source_file_bindings"], "independent_critical_jobs": jobs,
        "latest_publication_bound_utc": jobs[1]["completed_at"], "publication_deadline": cutoff.isoformat(),
        "current_main_sha": current_sha, "current_main_four_file_bytes_preserved": True,
        "git_ancestry_verified": False, "current_main_check_kind": "FOUR_ORIGINAL_BLOBS_NOT_COMMIT_ANCESTRY_PROOF",
        "verification_window": "IMMEDIATE_POST_PUBLICATION_REQUIRES_UNEXPIRED_ORIGINAL_P0_PAGES_ARTIFACT",
        "timestamp_basis": "GITHUB_SUCCESSFUL_FREEZE_JOB_COMPLETION_NOT_GIT_AUTHOR_OR_COMMITTER_CLOCK",
        "model_canonical_sha256": natural.MODEL_SHA, "model_evaluation_sha256": natural.EVALUATION_SHA,
        "verified_code_bindings": code_bindings,
        "verifier_sha256": SELF_SHA, "bounded_request_cost": (github_client.calls
            if continuation_proof is not None and actual else reads.cost),
        "network_calls_performed": github_client.calls if actual else None,
        "research_only": True, "source_authority_issued": False, "natural_outcome_admission_issued": False,
        "production_activation_allowed": False, "formal_model_replacement_allowed": False,
        "actual_execution_claimed": False, "actual_capacity_verified": False, "provider_timestamp_semantics_confirmed": False,
        "future_outcomes_read": False, "predictions_recomputed": False, "model_training_performed": False,
        "files_written": 0, "remote_writes_performed": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-run-id", required=True)
    args = parser.parse_args(argv)
    result = verify_publication(expected_freeze_run_id=args.freeze_run_id,
        github_client=gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV, "")))
    print(gh.json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("CANDIDATE_RESEARCH_PUBLICATION_NOT_VERIFIED", file=sys.stderr)
        raise SystemExit(1)
