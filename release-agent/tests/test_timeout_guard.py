import os
from pathlib import Path
import subprocess
import sys

from tests._timeout import TIMEOUT_EXIT_CODE


ROOT = Path(__file__).resolve().parents[1]


def test_timeout_guard_terminates_stalled_test(tmp_path):
    stalled = tmp_path / "test_stalled.py"
    stalled.write_text(
        "import time\n\n"
        "def test_stalls():\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests._timeout",
            "-p",
            "no:cacheprovider",
            "--test-timeout=0.2",
            str(stalled),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == TIMEOUT_EXIT_CODE
    assert "TEST TIMEOUT after 0.2s" in output
    assert "test_stalled.py::test_stalls" in output
    assert "time.sleep(30)" in output
