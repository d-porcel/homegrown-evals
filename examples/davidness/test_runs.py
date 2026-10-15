"""The README's Quick start opens Davidness's results from the runs in logs/. A change to the engine or to
Davidness's files puts them out of date, and the example's leaderboard would then be empty until its models
are re-run."""

from importlib.metadata import version
from pathlib import Path

import pytest
from inspect_ai.log import list_eval_logs, read_eval_log

from benchmark import ROOT, load_benchmark
from leaderboard.results import load_results

# this test lives in its benchmark's folder, so it goes wherever the folder goes, and goes away with it
BENCHMARK = Path(__file__).parent.relative_to(ROOT).as_posix()
LOGS = str(Path(__file__).parent / "logs")


def test_the_example_runs_match_davidness_as_it_is_now():
    runs = list_eval_logs(LOGS)
    made_with = {read_eval_log(info, header_only=True).eval.packages.get("inspect_ai") for info in runs}
    # the version includes Inspect's, so after an upgrade every run is out of date until it's re-run
    if made_with != {version("inspect_ai")}:
        pytest.skip(f"the runs were made with Inspect {', '.join(sorted(map(str, made_with)))}")
    bench = load_benchmark(BENCHMARK)
    ranked, outdated, partial = load_results(LOGS, bench.folder, bench.version)
    assert len(ranked) == len(runs) and not outdated and not partial, (
        "Davidness's runs are out of date: re-run its models (see examples/davidness/README.md)"
    )
