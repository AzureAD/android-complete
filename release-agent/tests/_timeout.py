"""Fail-fast timeout guard for pytest tests.

The guard uses a daemon watchdog instead of signal alarms so it works on Windows.
On timeout it prints every Python thread stack and exits the test process with 124.
"""
from __future__ import annotations

from io import StringIO
import os
import sys
import threading
import traceback

import pytest


DEFAULT_TIMEOUT_SECONDS = 180.0
TIMEOUT_EXIT_CODE = 124


def pytest_addoption(parser):
    group = parser.getgroup("release-agent")
    group.addoption(
        "--test-timeout",
        action="store",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Maximum seconds for each test, including setup and teardown "
            f"(default: {DEFAULT_TIMEOUT_SECONDS:g}; 0 disables)."
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "timeout(seconds): override the release-agent per-test timeout; 0 disables it",
    )


def _timeout_seconds(item) -> float:
    marker = item.get_closest_marker("timeout")
    value = marker.args[0] if marker and marker.args else item.config.getoption("test_timeout")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"Invalid timeout for {item.nodeid}: {value!r}") from exc
    if seconds < 0:
        raise pytest.UsageError(f"Timeout for {item.nodeid} must be non-negative")
    return seconds


def _dump_stacks(nodeid: str, seconds: float) -> None:
    stream = StringIO()
    stream.write(f"\nTEST TIMEOUT after {seconds:g}s: {nodeid}\n")
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    for thread_id, frame in sorted(sys._current_frames().items()):
        stream.write(f"\n--- thread {thread_id} ({names.get(thread_id, 'unknown')}) ---\n")
        traceback.print_stack(frame, file=stream)
    os.write(2, stream.getvalue().encode("utf-8", errors="backslashreplace"))


def _watch(done: threading.Event, nodeid: str, seconds: float, config) -> None:
    if done.wait(seconds):
        return
    capture = config.pluginmanager.getplugin("capturemanager")
    if capture is not None:
        capture.suspend_global_capture(in_=True)
    _dump_stacks(nodeid, seconds)
    os._exit(TIMEOUT_EXIT_CODE)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    seconds = _timeout_seconds(item)
    if seconds == 0:
        yield
        return

    done = threading.Event()
    watchdog = threading.Thread(
        target=_watch,
        args=(done, item.nodeid, seconds, item.config),
        name=f"pytest-timeout:{item.nodeid}",
        daemon=True,
    )
    watchdog.start()
    try:
        yield
    finally:
        done.set()
