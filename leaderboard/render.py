import html
from pathlib import Path
from string import Template

from benchmark import Benchmark

from .results import Result, with_settings

PAGE = Template((Path(__file__).parent / "page.html").read_text(encoding="utf-8"))
TABLE = Template((Path(__file__).parent / "table.html").read_text(encoding="utf-8"))
GRID = Template((Path(__file__).parent / "grid.html").read_text(encoding="utf-8"))

# score colors: green from 70%, yellow below. Only two, so models a few points apart don't look a tier apart
GOOD = 0.7

EMPTY = (
    "No runs of this version of the benchmark yet. If you changed the brief, style guide, cases, rubric or checks, "
    "or upgraded Inspect or the scoring code, re-run the models to fill the table."
)


def as_text(bench: Benchmark, ranked: list[Result], outdated: list[Result], partial: list[Result]) -> str:
    # one column per grade in the benchmark's rubric
    widths = {grade: max(11, len(grade) + 2) for grade in bench.grades}
    lines = [f"{bench.name}, version {bench.version}. Only runs of this version are ranked.", ""]
    if not ranked:
        lines.append(EMPTY)
    for judge, results in by_judge(ranked).items():
        lines.append(f"Judged by {judge}:")
        width = max([26, *(len(result.name) + 2 for result in results)])
        lines.append(
            f"{'model':<{width}}{bench.name.lower():>14}  |"
            + "".join(f"{grade:>{widths[grade]}}" for grade in bench.grades)
            + f"  |{'$ per 1k':>10}{'$ per run':>11}   {'cases × epochs':<16}run date"
        )
        for result in results:
            scores = "".join(f"{grade_text(result, grade):>{widths[grade]}}" for grade in bench.grades)
            lines.append(
                f"{result.name:<{width}}{score_text(result):>14}  |{scores}  |{money(result.cost_per_1k):>10}"
                f"{money(result.run_cost):>11}   {f'{result.cases} × {result.epochs}':<16}{run_date(result)}"
            )
        grid = text_grid(bench, results)
        lines += ["", *grid, ""] if grid else [""]
    if ranked:
        lines += [
            "± is the 95% range around each score. Both costs are the tested model's replies only, at the prices",
            "in prices.yaml: per 1,000 replies, to compare models, and for the whole run.",
            "",
        ]
    lines = lines[:-1] if lines[-1] == "" else lines
    if outdated:
        lines += ["", "Out of date, re-run them to rank them:"]
        lines += [f"  {r.name}, judged by {r.judge}, run {run_date(r)}" for r in outdated]
    if partial:
        lines += ["", "Not ranked, for the reason on each line:"]
        lines += [f"  {r.name}, {missing_text(r, bench)}, run {run_date(r)}" for r in partial]
    return "\n".join(lines)


def missing_text(result: Result, bench: Benchmark) -> str:
    if result.status == "error":
        return f"stopped with an error: {result.error}" if result.error else "stopped with an error"
    if result.status == "cancelled":
        return "cancelled before it finished"
    if result.status != "success":
        return "didn't finish: it's still running, or stopped without saving its results"
    if result.cases == len(bench.cases) and result.completed < result.total:
        # every case was in the run, so the missing replies stopped with an error, for example under --fail-on-error 0.1
        return f"{result.total - result.completed} of its {result.total} replies stopped with an error"
    if result.other_version:
        return (
            "replies from two versions of the benchmark: run it again from scratch, "
            "or retry it from the version it started on"
        )
    if result.cut_off:
        return f"{result.cut_off} of its {result.total} replies were cut off by a limit, like --token-limit"
    if result.ungraded:
        return (
            f"{result.ungraded} of its {result.cases * result.epochs} replies have no grade: the judge gave none, "
            "or a panel of judges had no majority"
        )
    return f"{result.cases} of {len(bench.cases)} cases (--limit, --sample-id or stopped early)"


def as_html(bench: Benchmark, ranked: list[Result], outdated: list[Result], viewer: bool = False) -> str:
    groups = by_judge(ranked) or {"": []}
    return PAGE.substitute(
        viewer_link='  <p><a href="viewer/">Browse every reply and the judges\' reasoning →</a></p>' if viewer else "",
        name=html.escape(bench.name),
        description=html.escape(bench.description),
        version=bench.version,
        tables="\n".join(html_table(bench, judge, results) for judge, results in groups.items()),
        grade_list=" and ".join(bench.grades),
        outdated=outdated_section(outdated),
    )


def by_judge(ranked: list[Result]) -> dict[str, list[Result]]:
    # scores from different judges can't be compared, so each judge ranks only the runs it graded
    groups: dict[str, list[Result]] = {}
    for result in ranked:
        groups.setdefault(result.judge, []).append(result)
    return groups


def grade_text(result: Result, grade: str) -> str:
    score = result.scores.get(grade)
    return "–" if score is None else f"{score:.0%}"


def grade_title(grade: str) -> str:
    # a grade's file name as a column heading: code-quality.md becomes "Code quality"
    return grade.replace("-", " ").replace("_", " ").capitalize()


def score_text(result: Result) -> str:
    return f"{result.scores['overall']:.0%} ±{result.margin * 100:.0f}"


def money(amount: float | None) -> str:
    return "–" if amount is None else f"${amount:.2f}"


def case_ids(bench: Benchmark, results: list[Result]) -> list[str]:
    # in the order of cases.yaml, so each row is easy to find there
    ids = [str(case["id"]) for case in bench.cases]
    return [case for case in ids if any(case in result.case_scores for result in results)]


def case_text(result: Result, case: str) -> str:
    score = result.case_scores.get(case)
    return "–" if score is None else f"{score:.0%}"


def text_grid(bench: Benchmark, results: list[Result]) -> list[str]:
    cases = case_ids(bench, results)
    if not cases:
        return []
    # the model name without its provider, to fit a terminal
    names = [with_settings(result.model.split("/")[-1], result.settings) for result in results]
    widths = [max(len(name), 4) + 3 for name in names]
    first = max(len(case) for case in cases) + 2
    lines = [
        "Score on each case, one model per column, averaged over its epochs:",
        " " * first + "".join(f"{n:>{w}}" for n, w in zip(names, widths, strict=True)),
    ]
    for case in cases:
        lines.append(
            f"{case:<{first}}" + "".join(f"{case_text(r, case):>{w}}" for r, w in zip(results, widths, strict=True))
        )
    return lines


def html_table(bench: Benchmark, judge: str, results: list[Result]) -> str:
    rows = "\n".join(html_row(rank, result, bench.grades) for rank, result in enumerate(results, start=1))
    return TABLE.substitute(
        caption=f"<h2>Judged by {html.escape(judge)}</h2>" if judge else "",
        name=html.escape(bench.name),
        breakdown_width=len(bench.grades),
        breakdown_headers="".join(f'<th class="num">{html.escape(grade_title(grade))}</th>' for grade in bench.grades),
        rows=rows or f'<tr><td class="empty" colspan="{7 + len(bench.grades)}">{EMPTY}</td></tr>',
        grid=html_grid(bench, results),
    )


def html_row(rank: int, result: Result, grades: tuple[str, ...]) -> str:
    overall = result.scores["overall"]
    breakdown = "".join(f'<td class="num minor">{grade_text(result, grade)}</td>' for grade in grades)
    return (
        f"<tr><td>{rank}</td><th>{html.escape(result.name)}</th>"
        f'<td class="num headline {band(overall)}">{whisker(overall, result.margin)}{overall:.0%}'
        f' <span class="minor">±{result.margin * 100:.0f}</span></td>'
        f'{breakdown}<td class="num">{money(result.cost_per_1k)}</td><td class="num">{money(result.run_cost)}</td>'
        f'<td class="num">{result.cases} × {result.epochs}</td>'
        f"<td>{run_date(result)}</td></tr>"
    )


def html_grid(bench: Benchmark, results: list[Result]) -> str:
    cases = case_ids(bench, results)
    if not cases:
        return ""
    rows = "\n".join(
        f"        <tr><th>{html.escape(case)}</th>" + "".join(html_cell(result, case) for result in results) + "</tr>"
        for case in cases
    )
    return GRID.substitute(models="".join(f'<th class="num">{html.escape(r.name)}</th>' for r in results), rows=rows)


def html_cell(result: Result, case: str) -> str:
    score = result.case_scores.get(case)
    return '<td class="num">–</td>' if score is None else f'<td class="num {band(score)}">{score:.0%}</td>'


def whisker(score: float, margin: float) -> str:
    low, high = max(score - margin, 0.0), min(score + margin, 1.0)
    title = f"95% range: {low:.0%} to {high:.0%}"
    return (
        f'<span class="whisker" title="{title}" style="--score: {score:.1%}; --low: {low:.1%}; --high: {high:.1%}">'
        '<span class="range"></span><span class="dot"></span></span>'
    )


def outdated_section(outdated: list[Result]) -> str:
    if not outdated:
        return ""
    items = "\n".join(
        f"<li>{html.escape(r.name)}, judged by {html.escape(r.judge)}, run {run_date(r)}</li>" for r in outdated
    )
    return (
        '<section class="outdated"><h2>Out of date</h2>'
        "<p>These runs used an earlier version of the brief, style guide, cases, rubric, checks, scoring code "
        "or Inspect, so their scores can't be compared with the table above. Re-run them to rank them.</p>"
        f"<ul>{items}</ul></section>"
    )


def band(score: float) -> str:
    return "high" if score >= GOOD else "low"


def run_date(result: Result) -> str:
    return f"{result.run_date.day} {result.run_date:%b %Y}"
