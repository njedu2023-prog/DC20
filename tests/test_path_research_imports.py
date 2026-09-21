"""Path research utilities must import under the repository's importlib CI mode."""
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module', ['replay','stress','minute_review','score_review'])
def test_path_research_imports_resolve_exact_package(module):
    loaded = importlib.import_module('work.path_reliability_replay_20260921.' + module)
    assert Path(loaded.__file__).resolve() == ROOT/'work/path_reliability_replay_20260921'/f'{module}.py'


@pytest.mark.parametrize('script', ['stress.py','minute_review.py'])
def test_path_research_direct_cli_help_still_works_without_fetching(script):
    result = subprocess.run([sys.executable, str(ROOT/'work/path_reliability_replay_20260921'/script), '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout
