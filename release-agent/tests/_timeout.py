"""Fail-fast per-test and whole-suite timeout guards.

The guard uses a daemon watchdog instead of signal alarms so it works on Windows.
On timeout it prints every Python thread stack and exits the test process with 124.
The suite budget also bounds collections and long sequences of individually fast tests.
"""
from __future__ import annotations

from io import StringIO
import math
import os
import sys
import threading
import traceback

import pytest


DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_SUITE_TIMEOUT_SECONDS = 900.0
TIMEOUT_EXIT_CODE = 124
_SUITE_WATCHDOG = pytest.StashKey()
_LOCATION = pytest.StashKey()


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
    group.addoption(
        "--suite-timeout",
        action="store",
        type=float,
        default=DEFAULT_SUITE_TIMEOUT_SECONDS,
        help=(
            "Maximum seconds for collection and all tests combined "
            f"(default: {DEFAULT_SUITE_TIMEOUT_SECONDS:g}; 0 disables)."
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "timeout(seconds): override the release-agent per-test timeout; 0 disables it",
    )
    for option in ("test_timeout", "suite_timeout"):
        _validate_timeout(config.getoption(option), "--" + option.replace("_", "-"))


def _validate_timeout(value, label):
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(f"Invalid timeout for {label}: {value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise pytest.UsageError(f"Timeout for {label} must be finite and non-negative")
    return seconds


def _timeout_seconds(item) -> float:
    marker = item.get_closest_marker("timeout")
    value = marker.args[0] if marker and marker.args else item.config.getoption("test_timeout")
    return _validate_timeout(value, item.nodeid)


def _diagnostic_writer(config):
    capture = config.pluginmanager.getplugin("capturemanager")
    global_capture = getattr(capture, "_global_capturing", None)
    stream = getattr(global_capture, "err", None)
    if stream is not None:
        return stream.writeorg
    return lambda text: os.write(2, text.encode("utf-8", errors="backslashreplace"))


def _dump_stacks(nodeid: str, seconds: float, kind: str, write) -> None:
    stream = StringIO()
    stream.write(f"\n{kind} TIMEOUT after {seconds:g}s: {nodeid}\n")
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    for thread_id, frame in sorted(sys._current_frames().items()):
        stream.write(f"\n--- thread {thread_id} ({names.get(thread_id, 'unknown')}) ---\n")
        traceback.print_stack(frame, file=stream)
    write(stream.getvalue())


def _watch(done: threading.Event, nodeid, seconds: float, config, kind="TEST") -> None:
    if done.wait(seconds):
        return
    try:
        _dump_stacks(
            nodeid() if callable(nodeid) else nodeid,
            seconds,
            kind,
            _diagnostic_writer(config),
        )
    finally:
        os._exit(TIMEOUT_EXIT_CODE)


def pytest_sessionstart(session):
    config = session.config
    seconds = config.getoption("suite_timeout")
    if seconds == 0:
        return
    config.stash[_LOCATION] = "collection"
    done = threading.Event()
    watchdog = threading.Thread(
        target=_watch,
        args=(done, lambda: config.stash[_LOCATION], seconds, config, "SUITE"),
        name="pytest-suite-timeout",
        daemon=True,
    )
    config.stash[_SUITE_WATCHDOG] = (done, watchdog)
    watchdog.start()


def pytest_sessionfinish(session, exitstatus):
    watchdog = session.config.stash.get(_SUITE_WATCHDOG, None)
    if watchdog is not None:
        done, thread = watchdog
        done.set()
        thread.join(timeout=1)
        del session.config.stash[_SUITE_WATCHDOG]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    item.config.stash[_LOCATION] = item.nodeid
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
