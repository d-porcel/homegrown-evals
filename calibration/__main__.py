import argparse
import csv
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path

from inspect_ai.log import list_eval_logs, read_eval_log
from inspect_ai.scorer import SampleScore, Score, krippendorff_alpha, value_to_float

from benchmark import ROOT, load_or_exit

from .judge_tests import LETTERS, known_replies, report
from .replies import Reply, load_replies, normalize_key

LOG_DIR = "logs/calibration"
GRADES_FILE = f"{LOG_DIR}/grades.csv"
RESCORED = "logs/calibration-rescored"
JUDGE_TESTS = "logs/judge-tests"
# the spreadsheet's columns around the grades, which come from the benchmark's rubric
BEFORE_GRADES = ["id", "case", "situation", "reply", "must_haves"]
# how a formula starts in a spreadsheet
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
AFTER_GRADES = ["notes", "key"]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m calibration", description="Check the judge against your own grades."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser(
        "export", help="write the replies to a spreadsheet to grade, with the judge's grades hidden"
    )
    export_cmd.add_argument("log_dir", nargs="?", default=LOG_DIR, help=f"runs to grade (default: {LOG_DIR})")
    export_cmd.add_argument("--out", default=GRADES_FILE, help=f"the spreadsheet to write (default: {GRADES_FILE})")
    compare_cmd = commands.add_parser("compare", help="compare your grades with the benchmark's")
    compare_cmd.add_argument(
        "grades", nargs="?", default=GRADES_FILE, help=f"your graded spreadsheet (default: {GRADES_FILE})"
    )
    compare_cmd.add_argument("log_dir", nargs="?", default=LOG_DIR, help="runs to compare with, e.g. re-scored ones")
    rescore_cmd = commands.add_parser(
        "rescore", help="re-grade the same replies with the current brief, style guide, cases and judges"
    )
    rescore_cmd.add_argument("log_dir", nargs="?", default=LOG_DIR, help=f"runs to re-grade (default: {LOG_DIR})")
    rescore_cmd.add_argument("--out", default=RESCORED, help=f"where to write the re-graded runs (default: {RESCORED})")
    tests_cmd = commands.add_parser(
        "test-judges", help="check the judges give your golden answers and judge_tests.yaml the grades you expect"
    )
    tests_cmd.add_argument(
        "--benchmark",
        default=".",
        metavar="FOLDER",
        help="the benchmark to test, e.g. examples/davidness (default: yours)",
    )
    tests_cmd.add_argument("--log-dir", default=JUDGE_TESTS, help=f"where to write the run (default: {JUDGE_TESTS})")
    args = parser.parse_args()

    if args.command == "export":
        export(args.log_dir, Path(args.out))
    elif args.command == "compare":
        compare(Path(args.grades), args.log_dir)
    elif args.command == "rescore":
        rescore(args.log_dir, Path(args.out))
    else:
        test_judges(args.benchmark, args.log_dir)


def rescore(log_dir: str, out: Path) -> None:
    # re-graded copies replace whatever is in out, so out mustn't hold the runs being re-graded
    source, target = Path(log_dir).resolve(), out.resolve()
    if target == source or target.is_relative_to(source):
        sys.exit(
            f"--out {out} is in {log_dir}, the runs to re-grade. Re-grading replaces the runs in --out, "
            "so it would delete the ones it's about to re-grade. Pick a folder outside it."
        )
    require_folder(log_dir)
    out.mkdir(parents=True, exist_ok=True)
    for info in list_eval_logs(log_dir):
        log = read_eval_log(info, header_only=True)
        benchmark = (log.eval.metadata or {}).get("benchmark", ".")
        # Inspect's own re-scoring: the same replies, graded by a scorer that reads the benchmark's
        # files when it starts, so it uses the current brief, style guide, cases and judges
        run = info.name.removeprefix("file://")
        # inspect score asks before replacing an existing output file, so clear our own copy from last time
        (out / Path(run).name).unlink(missing_ok=True)
        command = [
            "inspect",
            "score",
            run,
            "--scorer",
            "judges/scorer.py@benchmark_judge",
            "-S",
            f"benchmark={benchmark}",
            "--action",
            "overwrite",
            "--output-file",
            str(out / Path(run).name),
        ]
        show(command)
        # PYTHONPATH lets the scorer file import the judges package
        finished = subprocess.run(
            [sys.executable, "-m", "inspect_ai", *command[1:]], env={**os.environ, "PYTHONPATH": str(ROOT)}
        )
        if finished.returncode:
            sys.exit(f"Re-grading {Path(run).name} stopped with an error: Inspect's message is above.")
    print(f"\nRe-graded runs written to {out}. Compare them with: python -m calibration compare {GRADES_FILE} {out}")


def require_folder(log_dir: str) -> None:
    # a mistyped folder would otherwise look like one with no runs, or no matching replies
    if "://" not in log_dir and not Path(log_dir).is_dir():
        sys.exit(f"{log_dir} doesn't exist. Pass the folder your runs are in.")


def show(command: list[str]) -> None:
    # the Inspect command as it runs, so you can see what it does. PYTHONPATH is the repo folder, nothing secret
    print("PYTHONPATH=. " + " ".join(command))


def test_judges(benchmark: str, log_dir: str) -> None:
    # a mistake in the benchmark's files, or in its judge tests, gets a plain message here, instead of a
    # traceback from inside Inspect
    bench = load_or_exit(benchmark)
    try:
        known_replies(bench)
    except ValueError as error:
        sys.exit(str(error))
    before = {info.name for info in list_eval_logs(log_dir)} if Path(log_dir).is_dir() else set()
    # Inspect's own eval of the judge tests task. No model writes anything, so --model is a placeholder;
    # the judges come from --model-role grader=... or INSPECT_EVAL_MODEL_ROLE in .env, as in a real run
    command = [
        "inspect",
        "eval",
        "calibration/judge_tests.py",
        "-T",
        f"benchmark={benchmark}",
        "--model",
        "mockllm/model",
        "--log-dir",
        log_dir,
    ]
    show(command)
    # PYTHONPATH lets the task file import the benchmark and judges code
    finished = subprocess.run(
        [sys.executable, "-m", "inspect_ai", *command[1:]], env={**os.environ, "PYTHONPATH": str(ROOT)}
    )
    new_logs = [info for info in list_eval_logs(log_dir) if info.name not in before]
    if not new_logs:
        sys.exit(f"Inspect stopped before writing a log (exit code {finished.returncode}). Its message is above.")
    # log files start with their date and time, so the newest sorts last
    newest = max(new_logs, key=lambda info: info.name)
    text, passed = report(read_eval_log(newest), log_dir)
    print(f"\n{text}")
    if not passed:
        sys.exit(1)


def export(log_dir: str, out: Path) -> None:
    if out.exists():
        sys.exit(f"{out} already exists. Move it or pass --out, so your grades are never overwritten.")
    require_folder(log_dir)
    replies = one_run_per_model(load_replies(log_dir), log_dir)
    if not replies:
        sys.exit(f"No graded replies in {log_dir}")
    # shuffled, with no model names, so each reply is graded on its own merits
    random.Random(0).shuffle(replies)
    grades = list(dict.fromkeys(grade for reply in replies for grade in reply.grades))
    out.parent.mkdir(parents=True, exist_ok=True)
    # with a byte-order mark, so Excel shows emoji and curly quotes in the replies correctly
    with out.open("w", newline="", encoding="utf-8-sig") as file:
        # by column name, so the grade and notes columns stay blank whatever the grades are
        writer = csv.DictWriter(file, fieldnames=[*BEFORE_GRADES, *grades, *AFTER_GRADES])
        writer.writeheader()
        for number, reply in enumerate(replies, start=1):
            writer.writerow(
                {
                    "id": f"r{number:02}",
                    "case": reply.case,
                    "situation": as_text_cell(reply.situation),
                    "reply": as_text_cell(reply.text),
                    # bullets rather than "- ", which a spreadsheet can read as the start of a formula
                    "must_haves": re.sub(r"^- ", "• ", reply.must_haves, flags=re.M),
                    "key": reply.key,
                }
            )
    print(
        f"Wrote {len(replies)} replies to {out}.\n\n"
        f"Grade each one yourself, before looking at the judge's grades: give {' and '.join(grades)} each a C, P or I,\n"
        "the way that grade's prompt in rubric/ asks its judge to. Rows you leave blank are skipped.\n\n"
        "Then run: python -m calibration compare"
        + ("" if (str(out), log_dir) == (GRADES_FILE, LOG_DIR) else f" {out} {log_dir}")
    )


def compare(grades: Path, log_dir: str) -> None:
    if not grades.exists():
        sys.exit(f"{grades} doesn't exist. Run python -m calibration export first, then grade the replies in it.")
    require_folder(log_dir)
    benchmark = {reply.key: reply for reply in one_run_per_model(load_replies(log_dir), log_dir)}
    # utf-8-sig reads sheets with or without the byte-order mark Excel adds to "CSV UTF-8"
    reader = csv.DictReader(grades.open(encoding="utf-8-sig"))
    # the grade columns are the ones export added from the benchmark's rubric
    names = [column for column in reader.fieldnames or [] if column not in BEFORE_GRADES + AFTER_GRADES]
    if not names:
        sys.exit(f"{grades} has no grade columns. Export a fresh sheet with: python -m calibration export")
    rows = [row for row in reader if any(row[name].strip() for name in names)]
    for row in rows:
        row["key"] = normalize_key(row["key"])
        for name in names:
            if row[name].strip().upper() not in LETTERS:
                sys.exit(f"{row['id']}: {name} must be one of {', '.join(sorted(LETTERS))}, not {row[name]!r}")
    # the same reply, not just the same model, case and epoch: replies that were re-run since don't count
    graded = [row for row in rows if row["key"] in benchmark and same_text(row["reply"], benchmark[row["key"]].text)]

    print(f"You vs the benchmark, on {len(graded)} replies\n")
    width = max(12, *(len(name) + 2 for name in names))
    print(f"{'':{width}}{'agree':>8}{'always C':>11}{'alpha':>8}{'recall':>9}{'false positive rate':>22}")
    disagreements: list[str] = []
    for name in names:
        # only replies the judge graded on this grade: a grade added to the rubric since has no grades from older runs
        with_grade = [row for row in graded if name in benchmark[row["key"]].grades]
        pairs = [(row[name].strip().upper(), benchmark[row["key"]].grades[name]) for row in with_grade]
        agreed = [yours == theirs for yours, theirs in pairs]
        always_c = [yours == "C" for yours, _ in pairs]
        # a reply counts as marked down when it's graded P or I
        caught = [theirs != "C" for yours, theirs in pairs if yours != "C"]
        false_alarms = [theirs != "C" for yours, theirs in pairs if yours == "C"]
        print(
            f"{name:{width}}{ratio(agreed):>8}{ratio(always_c):>11}{format_alpha(pairs):>8}"
            f"{ratio(caught):>9}{ratio(false_alarms):>22}"
        )
        for row, (yours, theirs) in zip(with_grade, pairs, strict=True):
            if yours != theirs:
                note = f"  ({row['notes'].strip()})" if row["notes"].strip() else ""
                disagreements.append(
                    f"  {row['id']} {row['case']}, {benchmark[row['key']].model}: {name} you {yours}, benchmark {theirs}{note}"
                )

    print(
        "\nA reply counts as marked down when it's graded P or I.\n"
        "  always C             how often a judge that grades every reply C would agree with you\n"
        "  alpha                Krippendorff's alpha, agreement corrected for chance: 0.8 or more is strong,\n"
        "                       0.67-0.8 is acceptable, below that the judge needs work\n"
        "  recall               of the replies you marked down, how many the judge also marked down\n"
        "  false positive rate  of the replies you graded C, how many the judge marked down"
    )
    if disagreements:
        print("\nWhere you disagree:")
        print("\n".join(disagreements))
    if len(graded) < len(rows):
        print(
            f"\n{len(rows) - len(graded)} graded rows had no matching reply in {log_dir} and were skipped. "
            "Their reply isn't there, for example because the replies were re-run."
        )


def one_run_per_model(replies: list[Reply], log_dir: str) -> list[Reply]:
    """Your grades are matched to replies by model, case and epoch, so two runs of one model would be ambiguous."""
    seen: set[str] = set()
    for reply in replies:
        if reply.key in seen:
            sys.exit(
                f"{log_dir} has more than one run of {reply.model}, so your grades can't be matched to the right replies. "
                "Keep one run per model in the folder."
            )
        seen.add(reply.key)
    return replies


def as_text_cell(text: str) -> str:
    # a cell that starts like a formula gets an apostrophe, so the spreadsheet shows it as text instead of running
    # it: a reply could otherwise carry a formula that sends the sheet's contents to a website
    return "'" + text if text.startswith(FORMULA_START) else text


def same_text(sheet: str, log: str) -> bool:
    # spreadsheet apps can change line endings and trailing spaces, and export may have added an apostrophe
    if sheet.startswith("'") and sheet[1:].startswith(FORMULA_START):
        sheet = sheet[1:]
    return sheet.split() == log.split()


def ratio(hits: list[bool]) -> str:
    # counts, not percentages: with a few dozen replies, "7/8" says how much the number can be trusted
    return f"{sum(hits)}/{len(hits)}"


def format_alpha(pairs: list[tuple[str, str]]) -> str:
    # ordinal, because C > P > I: mistaking P for C is a smaller disagreement than I for C
    metric = krippendorff_alpha(level="ordinal", to_float=value_to_float())
    scores = [SampleScore(score=Score(value=list(pair)), sample_id=n) for n, pair in enumerate(pairs)]
    # Inspect's type hints say metrics take Scores, but this one takes SampleScores, as its docs say
    alpha = metric(scores)  # type: ignore[arg-type]
    return "n/a" if not isinstance(alpha, float) or math.isnan(alpha) else f"{alpha:.2f}"


if __name__ == "__main__":
    main()
