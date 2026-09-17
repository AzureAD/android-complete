import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests._timeout import TIMEOUT_EXIT_CODE


ROOT = Path(__file__).resolve().parents[1]


def test_default_suite_budget_is_fifteen_minutes():
    from types import SimpleNamespace
    from tests import _timeout

    options = {}
    group = SimpleNamespace(addoption=lambda name, **kwargs: options.update({name: kwargs}))
    _timeout.pytest_addoption(SimpleNamespace(getgroup=lambda name: group))
    assert options["--suite-timeout"]["default"] == 15 * 60


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


@pytest.mark.parametrize("phase", ["collection", "tests"])
def test_suite_budget_bounds_total_runtime_not_only_one_test(tmp_path, phase):
    probe = tmp_path / "test_budget.py"
    probe.write_text(
        "import time\n"
        + ("time.sleep(30)\n" if phase == "collection" else "")
        + "\n".join(f"def test_part_{i}():\n    time.sleep(0.15)\n" for i in range(20)),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests._timeout",
         "-p", "no:cacheprovider", "--suite-timeout=0.6", "--test-timeout=5", str(probe)],
        cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, timeout=10,
    )
    output = result.stdout + result.stderr
    assert result.returncode == TIMEOUT_EXIT_CODE
    assert "SUITE TIMEOUT after 0.6s" in output
    assert ("collection" if phase == "collection" else "test_budget.py::test_part_") in output


def test_suite_budget_can_be_disabled(tmp_path):
    probe = tmp_path / "test_budget.py"
    probe.write_text("def test_passes():\n    pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests._timeout",
         "-p", "no:cacheprovider", "--suite-timeout=0", str(probe)],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_timeout_rejects_invalid_budgets(value):
    from tests._timeout import _validate_timeout

    with pytest.raises(pytest.UsageError, match="finite and non-negative"):
        _validate_timeout(value, "test")


@pytest.mark.parametrize("failure", ["capture", "stack_dump"])
def test_diagnostics_failure_cannot_disable_timeout_exit(monkeypatch, failure):
    from types import SimpleNamespace
    from tests import _timeout

    exits = []
    dumps = []

    def fail():
        raise OSError("diagnostic stream is closed")

    def dump(*args):
        dumps.append(args)
        if failure == "stack_dump":
            fail()
        args[3]("diagnostic")

    capture = SimpleNamespace(
        _global_capturing=SimpleNamespace(
            err=SimpleNamespace(writeorg=lambda text: fail() if failure == "capture" else None)
        )
    )
    config = SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda name: capture))
    monkeypatch.setattr(_timeout, "_dump_stacks", dump)
    monkeypatch.setattr(_timeout.os, "_exit", exits.append)
    with pytest.raises(OSError, match="diagnostic stream"):
        _timeout._watch(SimpleNamespace(wait=lambda seconds: False), "probe", 0.1, config)
    assert dumps == [("probe", 0.1, "TEST", capture._global_capturing.err.writeorg)]
    assert exits == [TIMEOUT_EXIT_CODE]
