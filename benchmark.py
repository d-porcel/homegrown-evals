"""Reads a benchmark folder: brief.md, style.md, cases.yaml, its rubric and checks.py. The task, the
judges, calibration and the leaderboard all load benchmarks through here."""

import ast
import hashlib
import io
import json
import re
import sys
import tokenize
from collections.abc import Hashable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml
from inspect_ai.dataset import Sample

ROOT = Path(__file__).parent

# the shared files that can change a score, on top of each benchmark's own brief, style guide, cases, rubric and checks
ENGINE_FILES = [
    ROOT / "benchmark.py",
    ROOT / "task.py",
    *sorted((ROOT / "judges").glob("*.py")),
]

REQUIRED_FILES = ("brief.md", "style.md", "cases.yaml")

# a grade's name becomes a score, a spreadsheet column and a field in judge_tests.yaml, so it can't be one of these
# UTF-8, read the same with or without the byte-order mark some Windows editors add at the start of a file
TEXT = "utf-8-sig"
RESERVED_NAMES = {"overall", "id", "case", "situation", "reply", "must_haves", "notes", "key", "why"}


@dataclass
class Benchmark:
    folder: str
    name: str
    description: str
    brief: str
    style: str
    cases: list[dict[str, Any]]
    # each grade's judge prompt, from rubric/<grade>.md
    rubric: dict[str, str]
    version: str

    @property
    def grades(self) -> tuple[str, ...]:
        return tuple(self.rubric)


def load_benchmark(benchmark: str = ".") -> Benchmark:
    """Read a benchmark folder, given relative to the repo root."""
    folder = ROOT / benchmark
    missing = [name for name in REQUIRED_FILES if not (folder / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"{shown(folder)} has no {' or '.join(missing)}. A benchmark needs brief.md, style.md and cases.yaml: "
            "copy the ones in the repo root to start."
        )
    brief, style = read_markdown(folder / "brief.md"), read_markdown(folder / "style.md")
    header, cases = read_cases(folder / "cases.yaml")
    rubric = read_rubric(folder / "rubric")
    name, description = header.get("name"), header.get("description")
    for field_name, value in (("name", name), ("description", description)):
        if value is not None:
            require_text(value, f"The {field_name} in {shown(folder / 'cases.yaml')}")
    return Benchmark(
        folder=Path(benchmark).as_posix(),
        name=name or folder.resolve().name.replace("-", " ").capitalize(),
        description=description or "",
        brief=brief,
        style=style,
        cases=cases,
        rubric=rubric,
        version=fingerprint(brief, style, cases, rubric, folder / "checks.py"),
    )


class UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML's safe loader, plus a YAML rule it doesn't enforce: keys in one block must be unique. PyYAML
    silently keeps the last of two, so a case missing its "-" would merge into the one above and disappear."""


def construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        # a merge (<<: *defaults) isn't a key: PyYAML adds the keys it brings, which the block's own keys override
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue
        key = loader.construct_object(key_node, deep=deep)
        # a key that isn't text or a number, like {Sam}, gets PyYAML's own error from construct_mapping below
        if not isinstance(key, Hashable):
            continue
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f'"{key}" appears twice in the same block, so one of them would be lost. '
                'A missing "-" before a new item is the usual cause.',
                key_node.start_mark,
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping)


def load_yaml(path: Path) -> Any:
    """Every YAML file the repo reads goes through here, so they all follow the same rules, and an error
    names the file and the line."""
    try:
        return yaml.load(path.read_text(encoding=TEXT), Loader=UniqueKeyLoader)
    except yaml.MarkedYAMLError as error:
        # the line of the mistake, and where the block around it starts: an indentation slip shows up at the
        # mistake, and a quote that's never closed at the end of the file, long after where it opened
        mark, starts = error.problem_mark or error.context_mark, error.context_mark
        line = f", line {mark.line + 1}" if mark else ""
        context = f" ({error.context}" if error.context else ""
        if context and starts and mark and starts.line != mark.line:
            context += f" that starts on line {starts.line + 1}"
        raise ValueError(f"{shown(path)}{line}: {error.problem}{context + ')' if context else ''}") from None
    except yaml.YAMLError as error:
        raise ValueError(f"{shown(path)} isn't valid YAML. {error}") from None


def load_or_exit(benchmark: str) -> Benchmark:
    """load_benchmark for the command-line tools: a mistake in the benchmark's files gets a plain message,
    not a traceback."""
    try:
        return load_benchmark(benchmark)
    except (FileNotFoundError, ValueError) as error:
        sys.exit(str(error))


def read_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """cases.yaml is either just a list of cases, or a name and description with the cases below."""
    data = load_yaml(path)
    header = data if isinstance(data, dict) else {}
    if header and "cases" not in header:
        raise ValueError(f"{shown(path)} has no cases: list them under a line that says cases:")
    cases = header.get("cases", data)
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{shown(path)} has no cases. The cases.yaml in the repo root shows the format.")
    for number, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(
                f"case {number} in {shown(path)} isn't a set of fields like input, target and must_haves. "
                "Check its indentation."
            )
    return header, [with_defaults(case, number) for number, case in enumerate(cases, start=1)]


def read_rubric(folder: Path) -> dict[str, str]:
    """One judge prompt per grade: rubric/decisions.md is the prompt for a grade called decisions."""
    prompts = sorted(folder.glob("*.md")) if folder.is_dir() else []
    if not prompts:
        raise FileNotFoundError(
            f"{shown(folder)} has no judge prompts. A benchmark needs a rubric folder with one prompt per grade, "
            "like rubric/decisions.md: copy the one in the repo root to start."
        )
    rubric: dict[str, str] = {}
    for path in prompts:
        grade = path.stem
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", grade) or grade in RESERVED_NAMES:
            raise ValueError(
                f'{shown(path)}: a grade can\'t be called "{grade}". Start with a lowercase letter, then use '
                f"lowercase letters, digits, - and _, and none of {', '.join(sorted(RESERVED_NAMES))}."
            )
        prompt = read_markdown(path)
        if "{answer}" not in prompt:
            raise ValueError(
                f"{shown(path)} has no {{answer}}, where the reply to grade goes, so it can't be a judge prompt. "
                "Every .md file in rubric/ is one grade's judge prompt."
            )
        rubric[grade] = prompt
    return rubric


def read_markdown(path: Path) -> str:
    # notes in <!-- comments --> are for you, not the model or the judges. One on lines of its own goes
    # with its line break, so adding or removing it leaves the text as it was
    text = path.read_text(encoding=TEXT)
    text = re.sub(r"^[ \t]*<!--(?:(?!-->).)*-->[ \t]*(?:\n|\Z)", "", text, flags=re.S | re.M)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def with_defaults(case: dict[str, Any], number: int) -> dict[str, Any]:
    """A case needs only input, target and must_haves; everything else gets a default."""
    where = f"case {case.get('id', number)} in cases.yaml"
    missing = [field for field in ("input", "target", "must_haves") if not case.get(field)]
    if missing:
        raise ValueError(f"{where} has no {' or '.join(missing)}")
    # YAML reads some values as dates, numbers and so on; anything that isn't plain JSON becomes text, as written
    case = json.loads(json.dumps(case, default=str))
    # a single must-have can be written as plain text instead of a list
    must_haves = case["must_haves"] if isinstance(case["must_haves"], list) else [case["must_haves"]]
    for field in ("input", "target"):
        require_text(case[field], f"{where}: {field}")
    for item_number, item in enumerate(must_haves, start=1):
        require_text(item, f"{where}: must-have {item_number}")
    return {
        # made from the input, not the position, so removing a case doesn't renumber the others
        "id": "case-" + hashlib.sha256(case["input"].strip().encode()).hexdigest()[:6],
        "category": "general",
        **case,
        "must_haves": must_haves,
    }


def require_text(value: Any, where: str) -> None:
    # YAML turns "Says when: today" into a key and value, and a bare "No" into false
    if not isinstance(value, str):
        raise ValueError(
            f"{where} isn't plain text: YAML read it as {value!r}. "
            "Put it in quotes, especially if it contains ': ' or is a word like Yes or No."
        )


def system_prompt(brief: str, style: str) -> str:
    """The brief, then the style guide: nothing else, so the model gets exactly what you wrote."""
    # Inspect fills the prompt from the case's metadata with str.format, so literal braces must be doubled
    return f"{brief}\n\n{style}".replace("{", "{{").replace("}", "}}")


def to_sample(case: dict[str, Any]) -> Sample:
    # any extra fields a case has (like channel: slack) reach your checks as case.metadata
    extra = {key: value for key, value in case.items() if key not in ("id", "input", "target", "must_haves")}
    return Sample(
        id=case["id"],
        input=case["input"].strip(),
        target=case["target"].strip(),
        metadata={
            **extra,
            "category": case["category"],
            "must_haves": "\n".join(f"- {item}" for item in case["must_haves"]),
        },
    )


def headings(markdown: str) -> set[str]:
    return {match.group(1) for match in re.finditer(r"^#+\s+(.+?)\s*$", markdown, re.M)}


def remove_sections(markdown: str, names: set[str]) -> str:
    """The markdown without the sections whose headings are in names, subsections included."""
    kept: list[str] = []
    removing: int | None = None  # the level of the heading being removed, while inside its section
    for line in markdown.split("\n"):
        heading = re.match(r"(#+)\s+(.+?)\s*$", line)
        if heading and removing is not None and len(heading.group(1)) <= removing:
            removing = None
        if heading and removing is None and heading.group(2) in names:
            removing = len(heading.group(1))
        if removing is None:
            kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def fingerprint(brief: str, style: str, cases: list[dict[str, Any]], rubric: dict[str, str], checks_file: Path) -> str:
    """A short hash of everything that can change a score, so runs are only compared with runs of
    the same benchmark. That includes the Inspect version, since an upgrade can change how judges'
    answers are read. Notes don't count: the brief, style guide and judge prompts are hashed without
    their <!-- comments -->, the cases as data, and Python files without comments, docstrings or blank lines."""
    parts = [f"inspect_ai {version('inspect_ai')}", brief, style, json.dumps(cases, sort_keys=True)]
    parts += [f"{grade}\n{prompt}" for grade, prompt in rubric.items()]
    for path in [*ENGINE_FILES, checks_file]:
        if path.exists():
            text = path.read_text(encoding=TEXT)
            if path.suffix == ".py":
                must_parse(text, path)
                text = code_only(text)
            parts.append(text)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:8]


def must_parse(source: str, path: Path) -> None:
    # a Python file with a mistake would otherwise fail in code_only() with no file name
    try:
        compile(source, str(path), "exec")
    except SyntaxError as error:
        raise ValueError(f"{shown(path)}, line {error.lineno}: {error.msg}") from None


def code_only(source: str) -> str:
    """A Python file without its comments, docstrings and blank lines. Plain text rather than a parsed
    tree, so the same file gives the same benchmark version on every Python version."""
    lines = source.split("\n")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]
    for node in ast.walk(ast.parse(source)):
        is_documented = isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if is_documented and ast.get_docstring(node, clean=False) is not None:
            docstring = node.body[0]
            for row in range(docstring.lineno, (docstring.end_lineno or docstring.lineno) + 1):
                lines[row - 1] = ""
    return "\n".join(line.rstrip() for line in lines if line.strip())


def shown(path: Path) -> str:
    # paths in messages, relative to the repo root when they're inside it
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
