"""Keep reduced default coverage explicit, bounded, and fully recoverable."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests._suite import CORE_CASE_BUDGET, CORE_CASES, EXTENDED_MODULES, selected_suite


ROOT = Path(__file__).resolve().parents[1]
FILES = ["tests/test_git_write_review.py", "tests/test_oneauth_merge_review.py"]


def collect(*args):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", *args],
        cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
    )
    return {line for line in result.stdout.splitlines() if line.startswith("tests/")}


def test_git_integration_selection_preserves_all_cases():
    all_cases = collect(*FILES)
    fast = collect(*FILES, "-m", "not git_integration")
    git = collect(*FILES, "-m", "git_integration")
    plan = "tests/test_git_write_review.py::test_integration_plan_full_normalized_inputs_and_exact_payloads"
    assert fast and git and fast.isdisjoint(git)
    assert fast | git == all_cases
    assert plan + "[memory]" in fast
    assert plan + "[git]" in git
    assert "tests/test_git_write_review.py::test_real_ri_plan_exact_merge_and_reverts_without_checkout_writes" in git
    assert "tests/test_oneauth_merge_review.py::test_compare_and_swap_rejects_race_at_push_without_overwriting_remote" in git
    assert all("test_integration_review_hash_changes_for_every_write_input" not in case for case in git)


def test_daily_suite_is_at_most_half_without_losing_extended_cases():
    full = collect("tests", "--validation-suite=full")
    core = collect("tests")
    extended = collect("tests", "--validation-suite=extended")
    assert core and extended and core.isdisjoint(extended)
    assert core | extended == full
    assert len(core) <= CORE_CASE_BUDGET
    assert len(core) * 2 <= len(full)
    for filename, names in CORE_CASES.items():
        for name in names:
            prefix = f"tests/{filename}::{name}"
            expected = {case for case in full if case.split("[")[0] == prefix}
            assert expected, f"Stale core selector: {prefix}"
            assert expected <= core
    actual_modules = {case.split("::")[0].split("/")[-1] for case in full}
    assert EXTENDED_MODULES <= actual_modules
    assert not any("test_oneauth_merge_review.py::" in case for case in core)
    assert "tests/test_git_write_review.py::test_integration_plan_full_normalized_inputs_and_exact_payloads[git]" not in core
    for filename in ("test_lifecycle_boundaries.py", "test_effects.py", "test_execution.py",
                     "test_approval_checkpoint.py", "test_automation_lifecycle.py",
                     "test_handlers.py", "test_parameter_contracts.py",
                     "test_workflow_revision.py", "test_ui_result_ownership.py"):
        assert {case for case in full if case.startswith(f"tests/{filename}::")} <= core


@pytest.mark.parametrize("args,keyword,markexpr,expected", [
    (["tests"], "", "", "core"),
    (["."], "", "", "core"),
    (["tests/test_oneauth_merge_review.py"], "", "", "full"),
    ([r"TESTS\TEST_CORE.PY"], "", "", "full"),
    (["tests/test_core.py::test_full_flow_replay_completes"], "", "", "full"),
    (["tests"], "oneauth", "", "full"),
    (["tests"], "", "git_integration", "full"),
])
def test_targeted_selectors_are_never_silently_reduced(args, keyword, markexpr, expected):
    config = SimpleNamespace(
        args=args, option=SimpleNamespace(keyword=keyword, markexpr=markexpr),
        getoption=lambda name: "auto",
    )
    assert selected_suite(config) == expected


def test_explicit_suite_overrides_automatic_target_detection():
    config = SimpleNamespace(getoption=lambda name: "core")
    assert selected_suite(config) == "core"
