"""Tests for startup profiling hooks."""

import logging
from unittest.mock import patch

from ui_qt.startup_profiler import StartupProfiler


def test_startup_profiler_records_elapsed_times():
    with patch(
        "ui_qt.startup_profiler.time.perf_counter",
        side_effect=[10.25, 10.75],
    ):
        profiler = StartupProfiler(start_time=10.0)
        profiler.mark("first")
        profiler.mark("second")

    assert profiler.events == [("first", 0.25), ("second", 0.75)]


def test_startup_profiler_logs_totals_and_deltas(caplog):
    profiler = StartupProfiler(
        start_time=0.0,
        events=[("first", 0.5), ("second", 0.8)],
    )

    with caplog.at_level(logging.INFO, logger="ui_qt.startup_profiler"):
        profiler.log_summary()

    assert "Startup timing summary:" in caplog.text
    assert "first" in caplog.text
    assert "total=  0.500s" in caplog.text
    assert "delta=  0.500s" in caplog.text
    assert "second" in caplog.text
    assert "total=  0.800s" in caplog.text
    assert "delta=  0.300s" in caplog.text
