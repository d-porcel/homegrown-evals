# Homegrown evals

Public leaderboards can't tell you which AI model will do *your* job the way you would. They rank models on someone else's tasks. This repo lets you build a benchmark of how you work, without writing code, and pick a model on evidence instead of vibes. It shows where each model fails and what it costs, and lets you check whether the judge agrees with you.

Built with [Inspect AI](https://inspect.aisi.org.uk/). The model being tested and the judge can be any model Inspect supports. OpenAI, Anthropic, hosted open models like Qwen (through OpenRouter or Together) and local models through [Ollama](https://ollama.com/) work as installed. For other providers, add their package first, like `uv add google-genai` for Gemini.

## Davidness

I built **Davidness** with this repo, loosely around how I work. A model plays an assistant answering my email and Slack the way I would, in 14 cases. It has to triage a Jira board, spot a daylight-saving trap, keep a password out of Slack, notice when IT's email has been hacked, and fire the slop cannon at AI-written sales pitches. For the record, I'd never let a model answer my messages as me, probably 🤖. It just made an easy example, since I'm an expert at being me.

You can run it, read it and copy from it, in [`examples/davidness/`](examples/davidness/). [Its README](examples/davidness/README.md) has the whole story: what each case tests, how the judge was checked, what the models did, and the decisions behind it.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs the right Python for you, plus an API key for each provider you use (or Ollama for free local models).

```bash
uv sync                      # Python 3.10+ and every package, at the exact versions in uv.lock
source .venv/bin/activate    # or start each command below with uv run
cp .env.example .env         # pick the model to test and the judge, and add your API keys
```

The commands in this README assume the environment is active, as above. Without it, start each one with `uv run`, like `uv run pytest`.

To see what you'll get, open Davidness's results. They're built from the runs in the repo, so it's free:

```bash
python -m leaderboard --benchmark examples/davidness examples/davidness/logs --viewer
python3 -m http.server 8000 --bind 127.0.0.1 --directory site/davidness    # then open http://127.0.0.1:8000
```

### Build your own

You don't write code: you fill in a few plain-text files and run a few commands. The files in the repo root are a scaffold, with instructions in comments that never reach the model.

**Write the cases yourself.** They are the benchmark. Each one is what the model is given, like a message or a request, the reply you'd want back, and what a good reply must do. If a model writes them, the benchmark tests what a model thinks a good reply is, and every score after that is meaningless. An AI can help with the rest, but not with this.

Prefer to be guided? Paste this into a coding agent, like Claude Code or Cursor, opened in this repo:

```
Help me build my own benchmark with this repo, following the Quick start in its README. Guide me one step at a time.

- Interview me to fill in brief.md and style.md, and show me each file before saving it.
- Don't write my cases or golden answers. For each case, ask me what the model is given, the reply I'd want, what a good reply must do and a short id, then put my words into cases.yaml.
- Only add a check to checks.py for a hard rule I name, and test it on sentences that should pass and sentences that should fail.
- Run pytest after every change, and fix what it finds.
- For judge_tests.yaml, you can draft replies that get something wrong, but I decide the grades each one should get.
- Don't guess model prices for prices.yaml: take them from each provider's pricing page, and show me where they came from.
- Ask before running anything that calls a model, and tell me roughly what it will cost.
```

Or follow the steps yourself:

1. **Set up the role the model plays:** fill in `brief.md` with who it is and how it decides, and `style.md` with how it writes. The judges' prompts in `rubric/` work as they are (see [Your rubric](#your-rubric)).
2. **Write your cases** in `cases.yaml`: each one what the model is given, your ideal reply, its must-haves, and an `id` to refer to it by (see [Case fields](#case-fields)).
3. **Add deterministic checks, if you need them.** A hard rule that code can verify, like a length limit, can be a check in `checks.py`. It's the only code in the repo you'd touch, and an AI coding agent can write it from your rule. Ask it to test the check on sentences that should pass and sentences that should fail, and to run `ruff check --fix` and `ruff format` on it.
4. **Check your files for mistakes:** run `pytest`. It catches YAML mistakes and missing fields in `cases.yaml`, judge tests for cases that don't exist, and checks that fail your own golden answers. It calls no model, so it's free.
5. **Test the judges:** in `judge_tests.yaml`, write a few replies to your cases that get something wrong, each with the case's `id` and the grades it should get, like `decisions: I`. The file's comments show one to copy. Then run `python -m calibration test-judges`, which also checks that your golden answers get a C (see [Testing the judges](#testing-the-judges)).
6. **Try one case:** run `inspect eval task.py --limit 1 --log-dir logs/smoke`.
7. **Run your models:** add each one's prices to `prices.yaml`, or its cost shows as "–" (see [Comparing models](#comparing-models)). Then run `inspect eval task.py --model <model-a>,<model-b> --epochs 3`, listing the models you want to compare. The judge is the one in `.env`.
8. **See the results:**

   ```bash
   python -m leaderboard             # the leaderboard, in the terminal
   python -m leaderboard --site      # the leaderboard as a web page, in site/
   python -m leaderboard --viewer    # the web page, plus Inspect's viewer to browse every reply and the judges' reasoning
   ```

   The last two print the command to open the page, like `python3 -m http.server 8000 --bind 127.0.0.1 --directory site/my-benchmark`.

Keep your benchmark private, since its brief describes who's who and how you work: make your copy of the repo private, or copy the scaffold's files, `rubric/` included, into a folder you add to `.gitignore`. Then pass that folder to each command: `inspect eval task.py -T benchmark=<folder>`, `python -m calibration test-judges --benchmark <folder>` and `python -m leaderboard --benchmark <folder>`. Before you trust the scores, [check that the judge grades the way you would](#checking-the-judge).

## Going deeper

Everything below is reference: how the pieces fit, what each file and setting does, and how to trust the numbers.

## How it works

A benchmark is a folder:

1. **`brief.md`**: what you'd hand a new hire: who the model is playing, who's who, and the rules they decide by.
2. **`style.md`**: how they write: house style, phrases they never use, and samples of their voice. Together with the brief, it becomes the model's system prompt.
3. **`cases.yaml`**: the cases. Each is a message the model replies to, with a golden answer and the must-haves a good reply needs. Optional fields can adjust a single case; see [Case fields](#case-fields).
4. **`rubric/`**: one judge prompt per grade. The file's name is the grade's name; see [Your rubric](#your-rubric).
5. **`checks.py`** (optional): deterministic checks, for the rules code can verify without a judge, like a length limit or a pile-up of banned phrases. It's Python that runs whenever you test or run the benchmark, so read it before running a benchmark someone else shared.
6. **`judge_tests.yaml`** (optional): replies whose grades you already know, to test the judges with; see [Testing the judges](#testing-the-judges).

Every reply gets one grade per prompt in `rubric/`, each from its own judge, so one grade never raises or lowers another. The scaffold and Davidness grade two things:

1. **Decisions:** a judge checks the reply understood the message it answers, meets the case's must-haves, and breaks none of the brief's rules. It reads `brief.md`, so it knows what the model knew, but not `style.md`, so writing style can't sway it.
2. **Style:** a judge checks the reply against the style guide, whatever its decisions. It reads only `style.md`.

Your deterministic checks run first, as gates for hard rules: if one fails, its grade is I and that judge isn't asked. A check can name the section of `brief.md` or `style.md` it enforces, with `covers=`, and the judges then never see that section, so that rule has exactly one grader. Without `covers=`, the judge still reads the section and can mark down smaller slips the check lets through.

Each grade is C (correct = 1), P (partial = 0.5) or I (incorrect = 0), and their average is the benchmark's headline score.

## Tips from building Davidness

[`examples/davidness/`](examples/davidness/) shows every file finished, and [its README](examples/davidness/README.md) explains the choices behind them. A few lessons:

- Make every rule checkable, and give every rule a case that tests it.
- Let deterministic checks in code handle the hard rules: length limits, formats, a pile-up of banned phrases. They're free, instant and give the same answer every time, while judges can't even count characters. A failed check is an automatic I, so leave smaller slips to the judges, and test each check on sentences that should fail, not just on your golden answers. Davidness's are in `examples/davidness/test_checks.py`.
- Check your judge before trusting it: grade replies yourself, including some from a smaller, cheaper model, and see whether the judge catches the mistakes you'd catch.

## Case fields

Each case needs only three fields: the message the model replies to (`input`), your ideal reply (`target`), and what a good reply must do (`must_haves`). Everything else is optional. Inspect's log viewer calls cases "samples".

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

`rubric/` holds one judge prompt per grade, and every benchmark has its own, so changing one benchmark's grades never affects another. The file's name is the grade's name: add `rubric/safety.md` and every reply also gets a safety grade, with its own column on the leaderboard and in the calibration spreadsheet. Delete `rubric/style.md` and style isn't graded. Names start with a lowercase letter and use lowercase letters, digits, `-` and `_`.

A judge prompt is plain text with placeholders that get filled in for each reply:

| Placeholder | What goes there |
|---|---|
| `{answer}` | The reply being graded. Every prompt needs it. |
| `{question}` | The case's `input`: the message the model replied to. |
| `{criterion}` | Your golden answer. |
| `{must_haves}` | The case's must-haves. Any other case field works the same way, by its name. |
| `{brief}`, `{style}` | Your brief and style guide, without the sections your checks cover. |
| `{reply_tag}` | A random tag to wrap the reply in, so text inside a reply can't pass for part of the prompt. |

The two prompts in the repo root's `rubric/` already end by asking for a last line of `GRADE: C`, `GRADE: P` or `GRADE: I`, and are a good start for a new grade. If you write one from scratch, end it the same way: a judge's answer that doesn't end with that line gets no grade.

A new or changed prompt is a new judge, so test it before you trust it (see the next two sections). Changing the rubric also changes the benchmark's version, so re-run the models.

## Testing the judges

Before you pay for model runs, check the judges on replies whose grades you already know. Your golden answers should get C for every grade, and replies you write to fail should get the grades you expect. List those in `judge_tests.yaml`, next to `cases.yaml`: each names a case, gives a reply, and the grades you expect, for any of your rubric's grades. Start with one reply per case that gets its main decision wrong. Without them, a judge that passes everything would look fine.

```bash
python -m calibration test-judges                                  # yours
python -m calibration test-judges --benchmark examples/davidness   # the example
```

No model writes anything; the judges grade the known replies with the same checks and prompts as a real run, so it costs one judge call per grade for each known reply: about 90 for Davidness. Re-run it after every change to your brief, style guide, must-haves or rubric: it's your regression suite. After you calibrate, add the replies you graded yourself, with your grades, so the set grows from real mistakes. In eval terms, this set is the judge's dev set, and the fresh replies you calibrate on below are its test set.

## Checking the judge

Before trusting the scores, check that the judge grades the way you would. Run a few models into their own folder, including a smaller, cheaper one, so that about a third of the replies have problems for the judge to catch. If too few do, add a model that makes more mistakes. Grade the replies yourself without seeing the judge's grades, then compare:

```bash
inspect eval task.py --model openai/gpt-6-astra,openai/gpt-6-luna,openai/gpt-5.4-mini --log-dir logs/calibration
python -m calibration export      # a spreadsheet of the replies, shuffled, with models and judge grades hidden
python -m calibration compare     # how well the judge matches your grades, and every disagreement
```

For each disagreement, decide who's right: fix the grade's prompt in `rubric/`, or fix the brief, the style guide or the case if they didn't say what you meant. Then have the judges grade the same replies again, with your current files, and compare with the grades you already gave:

```bash
python -m calibration rescore     # writes re-graded copies to logs/calibration-rescored
python -m calibration compare logs/calibration/grades.csv logs/calibration-rescored
```

`rescore` runs Inspect's own `inspect score` on each run, with the arguments our scorer needs, and prints each command so you can see what it runs. No new replies, so your grades still apply. Repeat until the judge clears a bar you set before looking.

Re-grading is a quick way to check a fix, but your fixes were made for these replies, so they flatter the judge. Once your files are final, run fresh replies into a new folder, grade them blind, and report the agreement on those:

```bash
inspect eval task.py --model <model-a>,<model-b>,<model-c> --log-dir logs/calibration-fresh
python -m calibration export logs/calibration-fresh --out logs/calibration-fresh/grades.csv
python -m calibration compare logs/calibration-fresh/grades.csv logs/calibration-fresh
```

Don't go by raw agreement. Most replies are usually fine, so a judge that grades everything C would agree with you often while catching nothing. `compare` treats a reply graded P or I as marked down, and reports:

- **always C:** the agreement a judge that grades every reply C would get. The real judge should beat it.
- **recall:** of the replies you marked down, the share the judge also marked down. Low recall makes models that make mistakes look better.
- **false positive rate:** of the replies you graded C, the share the judge marked down. A high rate docks good models at random.
- **alpha:** Krippendorff's alpha, agreement corrected for chance, and the only one that tells P and I apart.

A reasonable bar is recall of at least 0.75, a false positive rate of at most 0.1, and alpha of at least 0.67. The test that matters most, though, is whether the judge's mistakes are small enough not to change which model you'd pick. Davidness's README shows a full round: its numbers, and why they're enough.

You can also use a panel of judges. Set it in `.env` as a list, with caching off for each judge, as for a single one:

```
INSPECT_EVAL_MODEL_ROLE='grader=[{"model":"<model-a>","cache_prompt":false},{"model":"<model-b>","cache_prompt":false},{"model":"<model-c>","cache_prompt":false}]'
```

A `--model-role` on the command line replaces the one in `.env`, caching setting included. Each judge grades every reply, and a grade needs a majority. Use an odd number to avoid ties, though three judges can still split C, P and I. A reply the panel splits on has no grade, so the run isn't ranked.

## Comparing models

Runs are saved to `logs/runs` (set by `INSPECT_LOG_DIR` in `.env`). Run several models, then rank them:

```bash
inspect eval task.py --model openai/gpt-6-astra,ollama/qwen3:8b --epochs 3
python -m leaderboard                       # yours
python -m leaderboard --benchmark examples/davidness
```

`--epochs 3` answers every case three times and averages the results, which shows whether a model's failure repeats or was a one-off. It barely narrows the ± range, though: that comes from which cases you wrote, so more cases narrow it and more epochs don't. If a model has several runs, the leaderboard uses the latest complete one. Runs that stopped early or have replies with no grade are listed in the terminal with the reason, but not on the site. Out-of-date runs are listed in both. A run with settings that change the replies or the cost, like `--reasoning-effort low`, gets its own row, so you can compare the same model at different settings.

The leaderboard sticks to the numbers, so you make the call:

- **± range:** the 95% range around each score. With a small benchmark it's wide, often ±10 points or more, so models a few points apart can be effectively tied. Then price, or a case you care about, decides.
- **Cost per 1,000 replies:** what the tested model's replies cost. Next to it, **cost per run** is what that run's replies cost in all, for budgeting your evals. Neither includes the judge. The leaderboard prices every run from `prices.yaml`, so all models are compared at the same prices and a corrected price reaches older runs. The same file is Inspect's `--model-cost-config` (`.env.example` sets it), so each run also records its cost. Only list models Inspect knows: any other model in the file stops every run. Prices change, so keep the file's date current. A re-run that Inspect's cache answers is priced as if every reply were new, so it compares fairly. A `--batch` run gets its own row, but it's priced at standard rates, since batch discounts vary by provider.
- **Score on each case:** each model's score on each case, averaged over its epochs. A good total can hide a failure on the one case you care most about, so check those rows before you choose.

Scores of 70% and above are green and the rest yellow. Two colours, so models a few points apart don't look a tier apart.

Each run records a **benchmark version**: a fingerprint of the brief, style guide, cases, rubric and checks, plus the scoring code and the Inspect version. Only runs of the current version are ranked, so every score comes from the same test. After you change any of those, or upgrade Inspect, older runs are listed as out of date until you re-run them. Comments don't count as changes, and neither do blank lines in the cases or Python files. Only those files count, so if `checks.py` reads another file, like a word list, put the list in `checks.py` itself. And since `ruff format` changes the code, format `checks.py` before your first paid run.

To share the results, build the leaderboard as a web page. Each benchmark gets its own folder in `site/`, so building one never overwrites another, and git ignores `site/`, so nothing is published until you choose to:

```bash
python -m leaderboard --site      # the leaderboard page, in site/<your benchmark's name>/
python -m leaderboard --viewer    # the same, plus Inspect's log viewer to browse every reply
```

To see it, serve the folder on your own machine and open http://127.0.0.1:8000. The leaderboard prints the exact command after it builds the site. The viewer loads its logs over HTTP, so opening `index.html` straight from your files won't show the replies. `--bind 127.0.0.1` keeps the page off your network, so nobody on the same Wi-Fi can read it:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory site/<your benchmark's name>
```

`--viewer` adds Inspect's log viewer, so readers can go from a score to the replies behind it. It publishes far more than the scores: the model's whole system prompt (your brief and style guide), every case with its golden answer and must-haves, every reply, and the judges' reasoning. Private settings like model arguments, server URLs and extra request fields are left out of it. To publish the site, copy its folder to any static host, like GitHub Pages.

## Running Davidness yourself

To try another model on Davidness, or to check your setup before building your own, run it yourself. This calls the models, so it costs money: about $0.70 with the models in `.env.example`, mostly for the judge.

```bash
inspect eval task.py -T benchmark=examples/davidness     # run the Davidness example
python -m leaderboard --benchmark examples/davidness     # rank every model you've run
inspect view                                             # browse every reply, score and judge's reasoning
```

## Layout

```
brief.md, style.md, cases.yaml              your benchmark (a scaffold to fill in)
rubric/                                     its grades: one judge prompt each
checks.py, judge_tests.yaml                 its deterministic checks, and known replies to test the judges with
prices.yaml                                 model prices, for comparing models on cost
pyproject.toml, uv.lock                     the dependencies, at exact versions, and the test and lint settings
examples/davidness/                         the example benchmark: the same files, plus its README, tests and final runs
benchmark.py                                reads a benchmark folder, for all the tools below
task.py                                     the Inspect task that runs a benchmark
judges/                                     the scorer and the deterministic checks engine, shared by every benchmark
leaderboard/                                ranks the models, as a table or a static site
site/                                       where --site builds each benchmark's web page (git ignores it)
calibration/                                compares the judge's grades with yours
tests/                                      checks every benchmark's files, and the engine with a mock model
.github/workflows/ci.yml                    runs Ruff, the tests and pyright on pushes to main and pull requests, with no API keys
```
