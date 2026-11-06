"""Test setup. By default the suite builds its tables from the invented dashboard in demo/, with the real
build_agent_data.py, so it runs on a clean checkout with no WFP data and no API key.

With WFP_TEST_REAL_DATA=1 it runs on the tables in data/ instead, and adds the checks in test_real_data.py.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "web", REPO / "tests", REPO / "demo", REPO / "evals"):
    sys.path.insert(0, str(p))

REAL = os.environ.get("WFP_TEST_REAL_DATA") == "1"
if not REAL:
    # Before any test imports wfp_agent.tools, which reads the data folder when it is imported.
    import make_demo_data

    _root = Path(tempfile.mkdtemp(prefix="wfp_tests_"))
    atexit.register(shutil.rmtree, _root, ignore_errors=True)
    make_demo_data.build(_root / "data", keep_app=_root / "app")
    os.environ["WFP_AGENT_DATA"] = str(_root / "data")
    os.environ["WFP_TEST_APP"] = str(_root / "app")


@pytest.fixture(autouse=True)
def fresh_question():
    from wfp_agent import tools

    tools.begin_question()


@pytest.fixture
def rows():
    """A tool's table from the result store, and its text."""
    from wfp_agent import tools

    def run(tool, **kw):
        out = tool.call(kw)
        assert out.startswith("result_id:"), out[:200]
        return tools.RESULTS[out.split()[1]]["rows"], out

    return run
