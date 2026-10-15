# Comparing models

Running models on your benchmark, reading the leaderboard, and sharing the results. For the short version, see the [Quick start](../README.md#quick-start).

## Running models

Runs are saved to `logs/runs` (set by `INSPECT_LOG_DIR` in `.env`). Run several models, then rank them:

```bash
inspect eval task.py --model openai/gpt-6-astra,ollama/qwen3:8b --epochs 3
python -m leaderboard                       # yours
python -m leaderboard --benchmark examples/davidness
```

`--epochs 3` answers every case three times and averages the results, which shows whether a model's failure repeats or was a one-off. It barely narrows the ± range, though: that comes from which cases you wrote, so more cases narrow it and more epochs don't. If a model has several runs, the leaderboard uses the latest complete one. Runs that stopped early or have replies with no grade are listed in the terminal with the reason, but not on the site. Out-of-date runs are listed in both. A run with settings that change the replies or the cost, like `--reasoning-effort low`, gets its own row, so you can compare the same model at different settings.

## Reading the leaderboard

The leaderboard sticks to the numbers, so you make the call:

- **± range:** the 95% range around each score. With a small benchmark it's wide, often ±10 points or more, so models a few points apart can be effectively tied. Then price, or a case you care about, decides.
- **Cost per 1,000 replies:** what the tested model's replies cost. Next to it, **cost per run** is what that run's replies cost in all, for budgeting your evals. Neither includes the judge. The leaderboard prices every run from `prices.yaml`, so all models are compared at the same prices and a corrected price reaches older runs. The same file is Inspect's `--model-cost-config` (`.env.example` sets it), so each run also records its cost. A model missing from the file has no cost on the leaderboard. Only list models Inspect knows: any other model in the file stops every run. Prices change, so keep the file's date current. A re-run that Inspect's cache answers is priced as if every reply were new, so it compares fairly. A `--batch` run gets its own row, but it's priced at standard rates, since batch discounts vary by provider.
- **Score on each case:** each model's score on each case, averaged over its epochs. A good total can hide a failure on the one case you care most about, so check those rows before you choose.

Scores of 70% and above are green and the rest yellow. Two colours, so models a few points apart don't look a tier apart.

## The benchmark version

Each run records a **benchmark version**: a fingerprint of the brief, style guide, cases, rubric and checks, plus the scoring code and the Inspect version. Only runs of the current version are ranked, so every score comes from the same test. After you change any of those, or upgrade Inspect, older runs are listed as out of date until you re-run them. Comments don't count as changes, and neither do blank lines in the cases or Python files. Only those files count, so if `checks.py` reads another file, like a word list, put the list in `checks.py` itself. And since `ruff format` changes the code, format `checks.py` before your first paid run.

## Sharing the results

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
