"""Deterministic checks: hard rules a benchmark verifies with code instead of a judge.

A benchmark declares them in its own checks.py. Each check returns a message saying what's wrong,
or None when everything is fine (or the check doesn't apply to that case).
"""

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from benchmark import ROOT


@dataclass
class Case:
    id: str
    category: str
    input: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplyCheck:
    """A gate on one grade, named after its prompt in rubric/: if the check fails, that grade is I and its
    judge isn't asked. covers names the section of brief.md or style.md the check enforces, which the judges
    then don't see."""

    grade: str
    run: Callable[[Case, str], str | None]
    covers: str | None = None


@dataclass
class CaseCheck:
    """Checks a case itself. Only runs in pytest, before any model is involved."""

    run: Callable[[Case], str | None]


def reply_check(grade: str, covers: str | None = None) -> Callable[[Callable[[Case, str], str | None]], ReplyCheck]:
    # the grade is checked against the benchmark's rubric when its checks load
    def wrap(run: Callable[[Case, str], str | None]) -> ReplyCheck:
        return ReplyCheck(grade, run, covers)

    return wrap


def case_check(run: Callable[[Case], str | None]) -> CaseCheck:
    return CaseCheck(run)


@dataclass
class Checks:
    reply: list[ReplyCheck]
    case: list[CaseCheck]
    grades: tuple[str, ...]

    def failures(self, case: Case, reply: str) -> dict[str, list[str]]:
        """The failed reply checks' messages, by the grade they belong to."""
        failed: dict[str, list[str]] = {grade: [] for grade in self.grades}
        for check in self.reply:
            if message := check.run(case, reply):
                failed[check.grade].append(message)
        return failed

    def covered_sections(self) -> set[str]:
        """The headings of the brief and style guide sections a deterministic check enforces, so the judges don't."""
        return {check.covers for check in self.reply if check.covers}


def load_checks(benchmark: str, grades: tuple[str, ...]) -> Checks:
    """The checks in <benchmark>/checks.py, where benchmark is a folder relative to the repo root, and grades
    are the ones in its rubric."""
    path = ROOT / benchmark / "checks.py"
    if not path.exists():
        return Checks(reply=[], case=[], grades=grades)
    # every benchmark's file is called checks.py, so each gets its own module name
    name = f"checks_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # registered before it runs, as a normal import would be, so things like dataclasses work in checks.py
    sys.modules[name] = module
    spec.loader.exec_module(module)
    found = vars(module).values()
    checks = Checks(
        reply=[check for check in found if isinstance(check, ReplyCheck)],
        case=[check for check in found if isinstance(check, CaseCheck)],
        grades=grades,
    )
    for check in checks.reply:
        if check.grade not in grades:
            raise ValueError(
                f'{benchmark}/checks.py: {check.run.__name__} is a check on "{check.grade}", which isn\'t a grade in its '
                f"rubric ({', '.join(grades)}). Name one of those, or add rubric/{check.grade}.md."
            )
    return checks
