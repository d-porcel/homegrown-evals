"""Grades replies whose grades you already know: your golden answers, which should get C, and the
replies in the benchmark's judge_tests.yaml, with the grades you expect. No model writes anything; the
judges grade these replies with the same checks and prompts as a real run.

    python -m calibration test-judges [--benchmark FOLDER]
"""

import re
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import EvalLog
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from benchmark import ROOT, Benchmark, load_benchmark, load_yaml, to_sample
from judges import benchmark_judge

LETTERS = {"C", "P", "I"}
# what a judge test can have besides its grades: the case it belongs to, the reply, and why it gets its grades
FIELDS = {"case", "reply", "why"}


@task
def judge_tests(benchmark: str = "."):
    bench = load_benchmark(benchmark)
    return Task(
        name="judge-tests",
        dataset=MemoryDataset(known_replies(bench)),
        solver=give_the_known_reply(),
        scorer=benchmark_judge(benchmark=bench.folder),
        # not "benchmark", so the leaderboard never mistakes these for model runs
        metadata={"judge_tests_of": bench.folder, "benchmark_version": bench.version},
    )


def known_replies(bench: Benchmark) -> list[Sample]:
    cases = {str(case["id"]): case for case in bench.cases}
    golden = [
        {"case": case_id, "reply": case["target"], **dict.fromkeys(bench.grades, "C"), "why": "golden answer"}
        for case_id, case in cases.items()
    ]
    tests = golden + load_tests(ROOT / bench.folder / "judge_tests.yaml", set(cases), bench.grades)
    return [
        to_test_sample(cases[str(test["case"])], test, number, bench.grades)
        for number, test in enumerate(tests, start=1)
    ]


def load_tests(path: Path, case_ids: set[str], grades: tuple[str, ...]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    tests = load_yaml(path) or []
    if not isinstance(tests, list):
        raise ValueError(f"{path.name} should be a list of replies, each with a case, a reply and expected grades")
    for number, test in enumerate(tests, start=1):
        where = f"judge test {number} in {path.name}"
        if not isinstance(test, dict) or not isinstance(test.get("reply"), str):
            raise ValueError(f"{where} needs a reply")
        # a misspelled grade like "decision" would otherwise be skipped, and that grade never checked
        fields = FIELDS | set(grades)
        for field in test.keys() - fields:
            close = get_close_matches(str(field), fields, n=1)
            hint = f' Did you mean "{close[0]}"?' if close else f" Use {', '.join(sorted(fields))}."
            raise ValueError(f'{where} has a field called "{field}", which judge tests don\'t use.{hint}')
        if str(test.get("case")) not in case_ids:
            raise ValueError(
                f"{where} is for case {test.get('case')!r}, which cases.yaml doesn't have. "
                f"Use a case's id: {', '.join(sorted(case_ids))}"
            )
        expected = expected_grades(test, grades)
        if not expected or not set(expected.values()) <= LETTERS:
            raise ValueError(f"{where} needs an expected grade for {' or '.join(grades)}: C, P or I")
    return tests


def expected_grades(test: dict[str, Any], grades: tuple[str, ...]) -> dict[str, str]:
    # a test can expect any of the rubric's grades; the ones it leaves out aren't checked
    return {grade: str(test[grade]).strip().upper() for grade in grades if grade in test}


def to_test_sample(case: dict[str, Any], test: dict[str, Any], number: int, grades: tuple[str, ...]) -> Sample:
    sample = to_sample(case)
    return Sample(
        id=f"{test['case']}#{number}",
        input=sample.input,
        target=sample.target,
        metadata={
            **(sample.metadata or {}),
            "graded_as": str(test["case"]),
            "known_reply": test["reply"].strip(),
            "expected": expected_grades(test, grades),
            "why": test.get("why", ""),
        },
    )


@solver
def give_the_known_reply() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="known-reply", content=state.metadata["known_reply"])
        return state

    return solve


def report(log: EvalLog, log_dir: str) -> tuple[str, bool]:
    """Which known replies got the grades you expected, and which didn't."""
    samples = log.samples or []
    failed: list[str] = []
    for sample in samples:
        metadata = sample.metadata or {}
        score = next(iter((sample.scores or {}).values()), None)
        got = score.value if score is not None and isinstance(score.value, dict) else {}
        wrong = [
            f"{grade} expected {letter}, got {got.get(grade, 'no grade')}"
            for grade, letter in metadata["expected"].items()
            if got.get(grade) != letter
        ]
        if wrong:
            failed.append(f"  {metadata['graded_as']} ({metadata['why'] or 'no reason given'}): {'; '.join(wrong)}")
    lines = [f"Judge tests: {len(samples) - len(failed)} of {len(samples)} got the grades you expected"]
    if log.status != "success":
        # a model error's message starts with the whole request, so show only its last line (its line
        # breaks can be escaped, as \n)
        error = re.split(r"\n|\\n", log.error.message.strip())[-1][:400] if log.error else log.status
        lines.append(f"The run didn't finish: {error}")
    if failed:
        # inspect view on its own opens the folder of your runs, not the judge tests'
        reasoning = f"Read the judges' reasoning with: inspect view --log-dir {log_dir}"
        lines += ["", "Different from what you expected:", *failed, "", reasoning]
    return "\n".join(lines), log.status == "success" and not failed
