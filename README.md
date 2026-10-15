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

1. **Set up the role the model plays:** fill in `brief.md` with who it is and how it decides, and `style.md` with how it writes. The judges' prompts in `rubric/` work as they are (see [Your rubric](docs/writing-a-benchmark.md#your-rubric)).
2. **Write your cases** in `cases.yaml`: each one what the model is given, your ideal reply, its must-haves, and an `id` to refer to it by (see [Case fields](docs/writing-a-benchmark.md#case-fields)).
3. **Add deterministic checks, if you need them.** A hard rule that code can verify, like a length limit, can be a check in `checks.py`. It's the only code in the repo you'd touch, and an AI coding agent can write it from your rule. Ask it to test the check on sentences that should pass and sentences that should fail, and to run `ruff check --fix` and `ruff format` on it.
4. **Check your files for mistakes:** run `pytest`. It catches YAML mistakes and missing fields in `cases.yaml`, judge tests for cases that don't exist, and checks that fail your own golden answers. It calls no model, so it's free.
5. **Test the judges:** in `judge_tests.yaml`, write a few replies to your cases that get something wrong, each with the case's `id` and the grades it should get, like `decisions: I`. The file's comments show one to copy. Then run `python -m calibration test-judges`, which also checks that your golden answers get a C (see [Testing the judges](docs/checking-the-judge.md#testing-the-judges)).
6. **Try one case:** run `inspect eval task.py --limit 1 --log-dir logs/smoke`.
7. **Run your models:** add each one's prices to `prices.yaml`, or its cost shows as "–" (see [Comparing models](docs/comparing-models.md#reading-the-leaderboard)). Then run `inspect eval task.py --model <model-a>,<model-b> --epochs 3`, listing the models you want to compare. The judge is the one in `.env`.
8. **See the results:**

   ```bash
   python -m leaderboard             # the leaderboard, in the terminal
   python -m leaderboard --site      # the leaderboard as a web page, in site/
   python -m leaderboard --viewer    # the web page, plus Inspect's viewer to browse every reply and the judges' reasoning
   ```

   The last two print the command to open the page, like `python3 -m http.server 8000 --bind 127.0.0.1 --directory site/my-benchmark`.

Keep your benchmark private, since its brief describes who's who and how you work: make your copy of the repo private, or copy the scaffold's files, `rubric/` included, into a folder you add to `.gitignore`. Then pass that folder to each command: `inspect eval task.py -T benchmark=<folder>`, `python -m calibration test-judges --benchmark <folder>` and `python -m leaderboard --benchmark <folder>`. Before you trust the scores, [check that the judge grades the way you would](docs/checking-the-judge.md#comparing-the-judge-with-your-grades).

## Going deeper

- [Writing a benchmark](docs/writing-a-benchmark.md): how the pieces fit, every case field, and writing your own grades.
- [Checking the judge](docs/checking-the-judge.md): testing the judges, comparing them with your own grades, and panels of judges.
- [Comparing models](docs/comparing-models.md): reading the leaderboard, costs, the benchmark version, publishing the results, and running Davidness yourself.
