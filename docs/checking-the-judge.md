# Checking the judge

An AI judge grades every reply, so the scores are only as good as the judge. Test it on replies whose grades you know, then compare it with your own grades. For the short version, see the [Quick start](../README.md#quick-start).

## Testing the judges

Before you pay for model runs, check the judges on replies whose grades you already know. Your golden answers should get C for every grade, and replies you write to fail should get the grades you expect. List those in `judge_tests.yaml`, next to `cases.yaml`: each names a case by its `id`, gives a reply, and the grades you expect, for any of your rubric's grades. The scaffold's `judge_tests.yaml` shows the format in its comments. Start with one reply per case that gets its main decision wrong. Without them, a judge that passes everything would look fine.

When a test gets a grade you didn't expect, read the judge's reasoning with the command it prints. Then fix whichever is wrong: the brief, the style guide, the case's must-haves, the rubric or your expected grade, and run it again.

```bash
python -m calibration test-judges                                  # yours
python -m calibration test-judges --benchmark examples/davidness   # the example
```

No model writes anything; the judges grade the known replies with the same checks and prompts as a real run, so it costs one judge call per grade for each known reply: about 90 for Davidness. Re-run it after every change to your brief, style guide, must-haves or rubric: it's your regression suite. After you calibrate, add the replies you graded yourself, with your grades, so the set grows from real mistakes. In eval terms, this set is the judge's dev set, and the fresh replies you calibrate on below are its test set.

## Comparing the judge with your grades

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

A reasonable bar is recall of at least 0.75, a false positive rate of at most 0.1, and alpha of at least 0.67. The test that matters most, though, is whether the judge's mistakes are small enough not to change which model you'd pick. [Davidness's README](../examples/davidness/README.md) shows a full round: its numbers, and why they're enough.

## Panels of judges

You can also use a panel of judges. Set it in `.env` as a list, with caching off for each judge, as for a single one:

```
INSPECT_EVAL_MODEL_ROLE='grader=[{"model":"<model-a>","cache_prompt":false},{"model":"<model-b>","cache_prompt":false},{"model":"<model-c>","cache_prompt":false}]'
```

A `--model-role` on the command line replaces the one in `.env`, caching setting included. Each judge grades every reply, and a grade needs a majority. Use an odd number to avoid ties, though three judges can still split C, P and I. A reply the panel splits on has no grade, so the run isn't ranked.
