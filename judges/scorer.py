import re
import secrets

from inspect_ai.model import ModelRole, get_model, model_roles
from inspect_ai.scorer import (
    INCORRECT,
    Score,
    Scorer,
    Target,
    mean,
    model_graded_qa,
    scorer,
    stderr,
    value_to_float,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from typing_extensions import TypeIs

from benchmark import Benchmark, headings, load_benchmark, remove_sections, to_sample
from judges.checks import Case, Checks, load_checks

# the judge's grade, on the last line of its answer
GRADE_LINE = r"GRADE\s*:\s*([CPI])\s*$"

# the judge, set with --model-role grader=... It can't be the model being tested: see judge_is_not_the_tested_model()
GRADER = ModelRole("grader", required=True)


# each benchmark has its own grades, so the metrics apply to every key of the score: overall and each grade.
# mean() reads letter grades as C = 1, P = 0.5 and I = 0
@scorer(metrics={"*": [mean(), stderr()]})
def benchmark_judge(benchmark: str):
    # read the benchmark's files now, not when the run started, so re-grading an old run with
    # `inspect score` uses the current brief, style guide, must-haves and rubric (see docs/checking-the-judge.md)
    bench = load_benchmark(benchmark)
    current_cases = {str(case["id"]): to_sample(case) for case in bench.cases}
    checks = load_checks(benchmark, bench.grades)
    values = {
        **judge_files(bench, checks),
        # marks where each reply starts and ends, so text inside a reply can't pass for part of the prompt
        "reply_tag": f"reply-{secrets.token_hex(4)}",
    }
    # one judge per grade in the rubric, each on its own: one grade never raises or lowers another
    judges = {grade: judge(prompt, values) for grade, prompt in bench.rubric.items()}
    to_float = value_to_float()
    # each grade records the version it was given under: retrying a run after editing the benchmark keeps the
    # replies it already had, so the leaderboard needs this to tell a run that mixes two versions
    version = {"benchmark_version": bench.version}

    async def score(state: TaskState, target: Target) -> Score | None:
        reply = state.output.completion
        # judge tests grade several known replies to the same case, so they name the case they belong to
        case_id = str(state.metadata.get("graded_as", state.sample_id))
        current = current_cases.get(case_id)
        if current is None:
            # re-grading an old run, and this case has since been removed from the benchmark
            return Score.unscored(reason="case_removed", answer=reply, metadata=version)
        if state.input_text != current.input:
            # re-grading an old run, and this case's input has since changed: the reply answers the old one
            return Score.unscored(reason="case_changed", answer=reply, metadata=version)
        # grade against the case as it is now: its must-haves and golden answer
        state.metadata.update(current.metadata or {})
        target = Target(current.target)

        case = Case(
            id=case_id,
            category=state.metadata["category"],
            input=state.input_text,
            target=target.text,
            metadata=state.metadata,
        )
        failed = checks.failures(case, reply)
        grades: dict[str, str] = {}
        explanations: list[str] = []
        for grade, grade_judge in judges.items():
            if failed[grade]:
                # a check is a gate: when one fails, the grade is I and the judge isn't asked
                grades[grade] = INCORRECT
                explanations.append(f"{grade.upper()}\nFailed checks:\n" + "\n".join(f"- {m}" for m in failed[grade]))
                continue
            result = await grade_judge(state, target)
            if not has_grade(result):
                return Score.unscored(reason="grader_failed", answer=reply, metadata=version)
            grades[grade] = str(result.value)
            explanations.append(f"{grade.upper()}\n{result.explanation}")

        # the headline score: the grades averaged, where C = 1, P = 0.5 and I = 0
        overall = sum(to_float(grades[grade]) for grade in bench.grades) / len(bench.grades)
        return Score(
            value={"overall": overall, **grades},
            answer=reply,
            explanation="\n\n".join(explanations),
            metadata=version,
        )

    return score


def judge_files(bench: Benchmark, checks: Checks) -> dict[str, str]:
    """The brief and style guide as the judges see them: without the sections deterministic checks enforce, so every
    rule has exactly one grader."""
    covered = checks.covered_sections()
    unknown = covered - headings(bench.brief) - headings(bench.style)
    if unknown:
        raise ValueError(
            f"checks.py covers sections that brief.md and style.md don't have: {', '.join(sorted(unknown))}. "
            "Check the headings' spelling."
        )
    return {"brief": remove_sections(bench.brief, covered), "style": remove_sections(bench.style, covered)}


def judge(prompt: str, values: dict[str, str]) -> Scorer:
    # each prompt asks for what its judge needs, like {brief} or {style}. Inspect fills in the rest
    # with str.format, so literal braces in these values must be doubled
    prompt = re.sub(
        r"\{(" + "|".join(values) + r")\}",
        lambda match: values[match.group(1)].replace("{", "{{").replace("}", "}}"),
        prompt,
    )
    # partial_credit=True lets the judge answer P (partly right) as well as C or I. The grade must be the answer's
    # last line, as every rubric asks: an answer cut off mid-way could end on a GRADE line quoted from the reply
    return model_graded_qa(template=prompt, partial_credit=True, model_role=GRADER, grade_pattern=GRADE_LINE)


def has_grade(score: Score | None) -> TypeIs[Score]:
    # model_graded_qa returns a letter grade, or NaN when the judge gave no grade
    return score is not None and isinstance(score.value, str)


@solver
def judge_is_not_the_tested_model() -> Solver:
    """Stops the run before any reply is written if the judge is the model being tested: judges tend
    to favour their own writing, so it would be grading itself generously."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # a panel of judges, like --model-role grader=a,b, has every one of them grade
        panel = model_roles().get(GRADER.name)
        judges = panel if isinstance(panel, list) else [get_model(role=GRADER.name, required=True)]
        for judge_model in judges:
            if same_model(str(judge_model), str(state.model)):
                raise ValueError(
                    f"The judge, {judge_model}, is the model being tested, {state.model}, so it would grade its own "
                    "replies. Pick a different judge with --model-role grader=<model>, or INSPECT_EVAL_MODEL_ROLE in .env."
                )
        return state

    return solve


def same_model(a: str, b: str) -> bool:
    # one model can come through different providers, like anthropic/claude-opus-5-5 and
    # openrouter/anthropic/claude-opus-5-5, so they're compared without them
    return a.rsplit("/", 1)[-1].lower() == b.rsplit("/", 1)[-1].lower()
