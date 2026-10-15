"""Checks on every benchmark's own files: yours in the repo root and each one in examples/.
No model is called, so these run in about a second."""

import os
import re
import shutil
from pathlib import Path

import pytest
from inspect_ai.dataset import Sample

from benchmark import ROOT, code_only, headings, load_benchmark, remove_sections, system_prompt, to_sample
from calibration.judge_tests import known_replies, load_tests
from judges.checks import Case, load_checks
from judges.scorer import judge_files

# folders that hold no benchmark, only the environment, outputs or caches
NOT_BENCHMARKS = {"logs", "site", "node_modules", "__pycache__"}


def find_benchmarks() -> list[str]:
    """Every folder with a cases.yaml, wherever it is, so a benchmark kept in a private folder gets checked too."""
    found = []
    for folder, subfolders, files in os.walk(ROOT):
        subfolders[:] = sorted(name for name in subfolders if name not in NOT_BENCHMARKS and not name.startswith("."))
        if "cases.yaml" in files:
            found.append(Path(folder).relative_to(ROOT).as_posix())
    return found


BENCHMARKS = find_benchmarks()


def checks_of(benchmark: str):
    return load_checks(benchmark, load_benchmark(benchmark).grades)


def cases(benchmark: str) -> list[Case]:
    return [to_case(to_sample(case)) for case in load_benchmark(benchmark).cases]


def to_case(sample: Sample) -> Case:
    metadata = sample.metadata or {}
    return Case(
        id=str(sample.id),
        category=metadata["category"],
        input=str(sample.input),
        target=str(sample.target),
        metadata=metadata,
    )


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_every_case_has_what_the_judges_need(benchmark: str):
    # loading fails if a case has no input, target or must_haves
    bench = load_benchmark(benchmark)
    assert bench.brief and bench.style and bench.cases
    ids = [case["id"] for case in bench.cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    # a blank id: line leaves the id empty, so the case can never be graded, and every paid run of it is wasted
    blank = [n for n, case_id in enumerate(ids, start=1) if not isinstance(case_id, str) or not case_id.strip()]
    assert not blank, f"case {blank[0]} in cases.yaml has a blank id. Write one, or remove the id: line for a default."


def write_benchmark(folder: Path, cases_yaml: str) -> None:
    (folder / "brief.md").write_text("You are Sam.")
    (folder / "style.md").write_text("Sign off with Sam.")
    (folder / "cases.yaml").write_text(cases_yaml)
    shutil.copytree(ROOT / "rubric", folder / "rubric", dirs_exist_ok=True)


def test_cases_yaml_can_be_just_a_list(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n')
    bench = load_benchmark(str(tmp_path))
    case = bench.cases[0]
    assert bench.name, "the name defaults to the folder's name"
    assert case["id"].startswith("case-")
    assert (case["category"], case["must_haves"]) == ("general", ["Says when"])


def test_default_ids_survive_removing_another_case(tmp_path: Path):
    # re-scoring an old run matches replies to cases by id, so ids mustn't shift when a case goes
    second = '- input: "B?"\n  target: "b"\n  must_haves: y\n'
    write_benchmark(tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n' + second)
    before = load_benchmark(str(tmp_path)).cases[1]["id"]
    write_benchmark(tmp_path, second)
    assert load_benchmark(str(tmp_path)).cases[0]["id"] == before


def test_a_case_without_must_haves_is_rejected(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n')
    with pytest.raises(ValueError, match="no must_haves"):
        load_benchmark(str(tmp_path))


@pytest.mark.parametrize("must_have", ["Says when: a specific time today", "No"])
def test_a_must_have_yaml_misreads_is_rejected(tmp_path: Path, must_have: str):
    # unquoted, YAML reads the first as a key and value, and the second as false
    write_benchmark(
        tmp_path, f'- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves:\n  - {must_have}\n'
    )
    with pytest.raises(ValueError, match="must-have 1 isn't plain text"):
        load_benchmark(str(tmp_path))


def test_a_case_missing_its_dash_is_rejected(tmp_path: Path):
    # without the "-", the second case's fields join the first, and PyYAML would silently keep only the second
    write_benchmark(
        tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n  input: "B?"\n  target: "b"\n  must_haves: y\n'
    )
    with pytest.raises(ValueError, match='cases.yaml, line 4: "input" appears twice'):
        load_benchmark(str(tmp_path))


def test_yaml_merge_keys_still_work(tmp_path: Path):
    # <<: *first brings in the first case's fields, and the case's own fields override them
    write_benchmark(
        tmp_path,
        '- &first\n  input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n'
        '- <<: *first\n  input: "Can you review PR 2?"\n',
    )
    second = load_benchmark(str(tmp_path)).cases[1]
    assert (second["input"], second["target"]) == ("Can you review PR 2?", "Sure, at 3pm.")


def test_a_yaml_mistake_names_its_own_line(tmp_path: Path):
    # must_haves is indented one space too far on line 8: the message points there, not to the case's first line
    write_benchmark(
        tmp_path,
        'name: Sam\ncases:\n- input: "A?"\n  target: "a"\n  must_haves: x\n- input: "B?"\n  target: "b"\n   must_haves: y\n',
    )
    with pytest.raises(ValueError, match=r"cases.yaml, line 8: .* that starts on line 6\)"):
        load_benchmark(str(tmp_path))


@pytest.mark.parametrize(
    ("cases_yaml", "message"),
    [
        # a must-have written as a mapping with a mapping for its key
        ('- input: "A?"\n  target: "a"\n  must_haves:\n  - {Sam}: says when\n', "line 4: found unhashable key"),
        ('name: 2026\ncases:\n- input: "A?"\n  target: "a"\n  must_haves: x\n', "The name in .* isn't plain text"),
    ],
)
def test_odd_yaml_gets_a_plain_message(tmp_path: Path, cases_yaml: str, message: str):
    write_benchmark(tmp_path, cases_yaml)
    with pytest.raises(ValueError, match=message):
        load_benchmark(str(tmp_path))


def test_a_byte_order_mark_changes_nothing(tmp_path: Path):
    # some Windows editors start a file with one
    write_benchmark(tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n')
    (tmp_path / "brief.md").write_text("# Sam\nYou are Sam.", encoding="utf-8")
    (tmp_path / "checks.py").write_text("LIMIT = 1000\n", encoding="utf-8")
    plain = load_benchmark(str(tmp_path))
    (tmp_path / "brief.md").write_text("\ufeff# Sam\nYou are Sam.", encoding="utf-8")
    (tmp_path / "checks.py").write_text("\ufeffLIMIT = 1000\n", encoding="utf-8")
    marked = load_benchmark(str(tmp_path))
    assert marked.version == plain.version and "Sam" in headings(marked.brief)


def test_every_yaml_file_is_read_by_the_strict_loader():
    # load_yaml() refuses duplicate keys and names the file and line, so nothing else may call PyYAML directly
    skip = {".venv", "logs", "site", "tests", ".git"}
    offenders = []
    for folder, subfolders, files in os.walk(ROOT):
        subfolders[:] = [name for name in subfolders if name not in skip]
        for name in files:
            path = Path(folder) / name
            if (
                name.endswith(".py")
                and path != ROOT / "benchmark.py"
                and re.search(r"yaml\.\w*load", path.read_text(encoding="utf-8"))
            ):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"read YAML with benchmark.load_yaml() instead: {offenders}"


def test_a_misspelled_grade_in_judge_tests_is_rejected(tmp_path: Path):
    # "decision" instead of "decisions" would otherwise leave the decisions grade unchecked
    path = tmp_path / "judge_tests.yaml"
    path.write_text('- case: case-1\n  reply: "Maybe next week."\n  decision: I\n  style: C\n', encoding="utf-8")
    with pytest.raises(ValueError, match='"decision", which judge tests don\'t use. Did you mean "decisions"'):
        load_tests(path, {"case-1"}, ("decisions", "style"))


def test_dates_in_custom_fields_become_text(tmp_path: Path):
    write_benchmark(
        tmp_path,
        '- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n  due: 2026-03-08\n',
    )
    assert load_benchmark(str(tmp_path)).cases[0]["due"] == "2026-03-08"


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_cases_pass_their_case_checks(benchmark: str):
    checks = checks_of(benchmark)
    failures = [
        f"{case.id}: {message}" for case in cases(benchmark) for check in checks.case if (message := check.run(case))
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_golden_answers_pass_their_reply_checks(benchmark: str):
    # your reference replies have to follow your own rules; if one fails, the check or the answer is wrong
    checks = checks_of(benchmark)
    failures = [
        f"{case.id}: {message}"
        for case in cases(benchmark)
        for check in checks.reply
        if (message := check.run(case, case.target))
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_judge_prompts_fill_in_for_every_case(benchmark: str):
    # Inspect fills question, answer, criterion and instructions; everything else comes from the case
    bench = load_benchmark(benchmark)
    for prompt in bench.rubric.values():
        template = prompt.replace("{brief}", "the brief").replace("{style}", "the style guide")
        template = template.replace("{reply_tag}", "reply-tag")
        for case in bench.cases:
            metadata = to_sample(case).metadata or {}
            template.format(question="q", answer="a", criterion="c", instructions="i", **metadata)


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_system_prompt_fills_in_for_every_case(benchmark: str):
    # Inspect fills the system prompt from each case's metadata, so the files' braces must come through as written
    bench = load_benchmark(benchmark)
    for case in bench.cases:
        prompt = system_prompt(bench.brief, bench.style).format(**(to_sample(case).metadata or {}))
        assert prompt == f"{bench.brief}\n\n{bench.style}"


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_each_judge_reads_only_the_file_it_needs(benchmark: str):
    # the decisions judge needs to know what the model knew; the style judge, how it writes
    rubric = load_benchmark(benchmark).rubric
    if not {"decisions", "style"} <= rubric.keys():
        pytest.skip("this benchmark's rubric doesn't have the decisions and style grades")
    assert "{style}" in rubric["style"] and "{brief}" not in rubric["style"]
    assert "{brief}" in rubric["decisions"] and "{style}" not in rubric["decisions"]


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_judges_never_see_the_sections_code_checks(benchmark: str):
    # each rule has one grader: a check's covers= must name a real section, which the judges then don't get
    bench = load_benchmark(benchmark)
    checks = checks_of(benchmark)
    files = judge_files(bench, checks)
    assert not checks.covered_sections() & (headings(files["brief"]) | headings(files["style"]))


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_judge_tests_are_for_real_cases(benchmark: str):
    # loading fails if a judge test names a case cases.yaml doesn't have, or expects a grade that isn't C, P or I
    bench = load_benchmark(benchmark)
    assert len(known_replies(bench)) >= len(bench.cases), "every golden answer is a judge test too"


def test_a_check_has_to_name_a_grade_in_the_rubric(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n')
    (tmp_path / "checks.py").write_text(
        'from judges.checks import reply_check\n\n@reply_check("styles")\ndef short(case, reply):\n    return None\n'
    )
    bench = load_benchmark(str(tmp_path))
    with pytest.raises(ValueError, match='short is a check on "styles", which isn\'t a grade in its rubric'):
        load_checks(bench.folder, bench.grades)


def test_a_benchmarks_grades_are_its_rubric_files(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n')
    (tmp_path / "rubric" / "style.md").unlink()
    (tmp_path / "rubric" / "code-quality.md").write_text("Grade how the [Reply] is written.\n\n{answer}\n")
    assert load_benchmark(str(tmp_path)).grades == ("code-quality", "decisions")


@pytest.mark.parametrize(
    ("file", "text", "message"),
    [
        ("notes.txt", None, "has no judge prompts"),
        ("Tone.md", "{answer}", 'can\'t be called "Tone"'),
        ("overall.md", "{answer}", 'can\'t be called "overall"'),
        ("readme.md", "Notes about these prompts.", "has no {answer}"),
    ],
)
def test_rubric_mistakes_get_plain_messages(tmp_path: Path, file: str, text: str | None, message: str):
    write_benchmark(tmp_path, '- input: "A?"\n  target: "a"\n  must_haves: x\n')
    if text is None:
        shutil.rmtree(tmp_path / "rubric")
        (tmp_path / "rubric").mkdir()
    (tmp_path / "rubric" / file).write_text(text or "")
    with pytest.raises((ValueError, FileNotFoundError), match=re.escape(message)):
        load_benchmark(str(tmp_path))


def test_common_mistakes_get_plain_messages(tmp_path: Path):
    (tmp_path / "brief.md").write_text("You are Sam.")
    (tmp_path / "cases.yaml").write_text("name: Sam\ncase:\n- input: hi\n")
    with pytest.raises(FileNotFoundError, match="has no style.md"):
        load_benchmark(str(tmp_path))
    (tmp_path / "style.md").write_text("Be brief.")
    with pytest.raises(ValueError, match="list them under a line that says cases:"):
        load_benchmark(str(tmp_path))


def test_covers_removes_exactly_the_named_section():
    # its subsections go with it; its siblings, and everything around it, stay
    markdown = "# Sam\nIntro.\n\n## Keep\nA.\n\n## Drop\nB.\n\n### Drop's subsection\nC.\n\n## Also keep\nD."
    assert remove_sections(markdown, {"Drop"}) == "# Sam\nIntro.\n\n## Keep\nA.\n\n## Also keep\nD."


def test_notes_in_python_files_dont_change_the_version():
    code = "def check(reply):\n    return len(reply) > 5\n"
    with_notes = '"""Checks."""\n\n# the limit\ndef check(reply):\n    """Too long?"""\n    return len(reply) > 5  # characters\n'
    assert code_only(code) == code_only(with_notes)
    assert code_only(code) != code_only(code.replace("5", "6"))


def test_notes_in_markdown_dont_change_the_version(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n')
    (tmp_path / "style.md").write_text("Sign off with Sam.\nBe brief.")
    version = load_benchmark(str(tmp_path)).version
    # a note on a line of its own, and one over several lines, go with their line breaks
    (tmp_path / "style.md").write_text("<!-- why -->\nSign off with Sam.\n  <!--\n  a longer note\n  -->\nBe brief.")
    assert load_benchmark(str(tmp_path)).version == version
    # a note inside a line takes nothing else with it
    (tmp_path / "style.md").write_text("Sign off <!-- a --> with Sam.\n<!-- b -->\nBe brief.")
    assert load_benchmark(str(tmp_path)).style == "Sign off  with Sam.\nBe brief."


def test_a_mistake_in_checks_py_names_the_file_and_line(tmp_path: Path):
    write_benchmark(tmp_path, '- input: "Can you review my PR?"\n  target: "Sure, at 3pm."\n  must_haves: Says when\n')
    (tmp_path / "checks.py").write_text("def broken(:\n    pass\n")
    with pytest.raises(ValueError, match="checks.py, line 1: "):
        load_benchmark(str(tmp_path))


def test_root_benchmark_is_filled_in():
    if [case["id"] for case in load_benchmark(".").cases] == ["example-case"]:
        pytest.skip(
            "brief.md, style.md, cases.yaml and checks.py in the repo root are still the scaffold. "
            "Fill them in to build your own benchmark (see the README)."
        )
