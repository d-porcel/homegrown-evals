# Writing a benchmark

How a benchmark folder fits together, what each case field does, and how to write your own grades. For the short version, see the [Quick start](../README.md#quick-start).

## How it works

A benchmark is a folder:

1. **`brief.md`**: what you'd hand a new hire, covering who the model is playing, who's who, and the rules they decide by.
2. **`style.md`**: how they write, with their house style, phrases they never use, and samples of their voice. Together with the brief, it becomes the model's system prompt.
3. **`cases.yaml`**: the cases. Each is what the model is given, like a message or a request, with a golden answer and the must-haves a good reply needs. Optional fields can adjust a single case; see [Case fields](#case-fields).
4. **`rubric/`**: one judge prompt per grade. The file's name is the grade's name; see [Your rubric](#your-rubric).
5. **`checks.py`** (optional): deterministic checks, for the rules code can verify without a judge, like a length limit or a pile-up of banned phrases. It's Python that runs whenever you test or run the benchmark, so read it before running a benchmark someone else shared.
6. **`judge_tests.yaml`** (optional): replies whose grades you already know, to test the judges with; see [Testing the judges](checking-the-judge.md#testing-the-judges).

Every reply gets one grade per prompt in `rubric/`, each from its own judge, so one grade never raises or lowers another. The scaffold and Davidness grade two things:

1. **Decisions:** a judge checks the reply understood the message it answers, meets the case's must-haves, and breaks none of the brief's rules. It reads `brief.md`, so it knows what the model knew, but not `style.md`, so writing style can't sway it.
2. **Style:** a judge checks the reply against the style guide, whatever its decisions. It reads only `style.md`.

Your deterministic checks run first, as gates for hard rules. If one fails, its grade is I and that judge isn't asked. A check can name the section of `brief.md` or `style.md` it enforces, with `covers=`, and the judges then never see that section, so that rule has exactly one grader. Without `covers=`, the judge still reads the section and can mark down smaller slips the check lets through.

Each grade is C (correct = 1), P (partial = 0.5) or I (incorrect = 0), and their average is the benchmark's headline score.

After every change, run `pytest`. It checks a benchmark's files without calling any model, so it's free. It catches YAML mistakes and missing fields in `cases.yaml`, judge tests for cases that don't exist, and checks that fail your own golden answers.

## Case fields

A case needs only three fields. `input` is what the model is given, `target` is your ideal reply, and `must_haves` is what a good reply must do. Everything else is optional. Inspect's log viewer calls cases "samples".

| Field | | What it does |
|---|---|---|
| `input` | required | What the model replies to: the message as it arrives, then, under "Additional context:", anything the person would know that the message doesn't say. |
| `target` | required | Your golden answer. The judges compare replies with it. |
| `must_haves` | required | What a good reply must do, as a list or a single sentence. The decisions judge checks each one, and treats the first as the core decision: a reply that gets it wrong fails. Where a must-have is more specific than the brief, the must-have wins. |
| `id` | optional | A unique name shown in the logs. Defaults to a short code made from the case's `input`, like `case-3f9a2c`, so it doesn't change when you add or remove other cases, but editing the case's `input` does. Set your own `id` to keep it fixed. Either way, `python -m calibration rescore` leaves replies to an earlier `input` ungraded. |
| `category` | optional | Groups similar cases, like `priorities` or `judgment`. Defaults to `general`. |
| anything else | optional | Reaches your checks in `checks.py` as `case.metadata`, like `channel: slack`. |

`cases.yaml` can be just a list of cases. To show a name and description on the leaderboard, put `name` and `description` at the top and the list under `cases`. Without a `name`, the benchmark is named after its folder.

Don't name your own fields `question`, `answer`, `criterion` or `instructions`. Inspect fills those itself in the judge prompts and drops case fields with the same names.

## Your rubric

The scaffold's two grades, decisions and style, work as they are. Change them only if you need something else graded.

`rubric/` holds one judge prompt per grade, and every benchmark has its own, so changing one benchmark's grades never affects another. The file's name is the grade's name, so adding `rubric/safety.md` gives every reply a safety grade too, with its own column on the leaderboard and in the calibration spreadsheet. Delete `rubric/style.md` and style isn't graded. Names start with a lowercase letter and use lowercase letters, digits, `-` and `_`.

A judge prompt is plain text with placeholders that get filled in for each reply:

| Placeholder | What goes there |
|---|---|
| `{answer}` | The reply being graded. Every prompt needs it. |
| `{question}` | The case's `input`: the message the model replied to. |
| `{criterion}` | Your golden answer. |
| `{must_haves}` | The case's must-haves. Any other case field works the same way, by its name. |
| `{brief}`, `{style}` | Your brief and style guide, without the sections your checks cover. |
| `{reply_tag}` | A random tag to wrap the reply in, so text inside a reply can't pass for part of the prompt. |

The two prompts in the repo root's `rubric/` already end by asking for a last line of `GRADE: C`, `GRADE: P` or `GRADE: I`, and are a good start for a new grade. If you write one from scratch, end it the same way, because a judge's answer that doesn't end with that line gets no grade.

A new or changed prompt is a new judge, so test it before you trust it (see [Checking the judge](checking-the-judge.md)). Changing the rubric also changes the benchmark's version, so re-run the models.

## Keeping a benchmark out of a public repo

If your copy of the repo is public, for example a fork you use to send changes back to the template, keep your benchmark out of it. Copy the scaffold's files, `rubric/` included, into a folder you add to `.gitignore`, and pass that folder to each command:

```bash
inspect eval task.py -T benchmark=<folder>
python -m calibration test-judges --benchmark <folder>
python -m leaderboard --benchmark <folder>
```

## Tips from building Davidness

[`examples/davidness/`](../examples/davidness/) shows every file finished, and [its README](../examples/davidness/README.md) explains the choices behind them. A few lessons:

- Make every rule checkable, and give every rule a case that tests it.
- Let deterministic checks in code handle the hard rules, like length limits, formats and a pile-up of banned phrases. They're free, instant and give the same answer every time, while judges can't even count characters. A failed check is an automatic I, so leave smaller slips to the judges, and test each check on sentences that should fail, not just on your golden answers. Davidness's are in `examples/davidness/test_checks.py`.
- Check your judge before trusting it. Grade replies yourself, including some from a smaller, cheaper model, and see whether the judge catches the mistakes you'd catch.

## What's in the repo

```
brief.md, style.md, cases.yaml              your benchmark (a scaffold to fill in)
rubric/                                     its grades: one judge prompt each
checks.py, judge_tests.yaml                 its deterministic checks, and known replies to test the judges with
prices.yaml                                 model prices, for comparing models on cost
pyproject.toml, uv.lock                     the dependencies, at exact versions, and the test and lint settings
examples/davidness/                         the example benchmark: the same files, plus its README, tests and final runs
docs/                                       these pages: writing a benchmark, checking the judge, comparing models
benchmark.py                                reads a benchmark folder, for all the tools below
task.py                                     the Inspect task that runs a benchmark
judges/                                     the scorer and the deterministic checks engine, shared by every benchmark
leaderboard/                                ranks the models, as a table or a static site
site/                                       where --site builds each benchmark's web page (git ignores it)
calibration/                                compares the judge's grades with yours
tests/                                      checks every benchmark's files, and the engine with a mock model
.github/workflows/ci.yml                    runs Ruff, the tests and pyright on pushes to main and pull requests, with no API keys
```
