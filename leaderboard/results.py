import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any

from inspect_ai.log import EvalLog, EvalSample, list_eval_logs, read_eval_log
from inspect_ai.model import GenerateConfig, ModelCost, ModelUsage, compute_model_cost

from benchmark import ROOT, load_yaml

# the same file Inspect's --model-cost-config reads
PRICES = ROOT / "prices.yaml"

# settings that change how a run is carried out, not what the model replies or what it costs. Batch and
# fallback models aren't here: batch is billed differently, and a fallback model writes some of the replies
OPERATIONAL = {
    "max_retries",
    "timeout",
    "attempt_timeout",
    "stream_idle_timeout",
    "max_connections",
    "adaptive_connections",
    "fail_on_refusal",
    "cache",
    "parallel_tool_calls",
    "internal_tools",
    "max_tool_output",
    "extra_headers",
}
# settings that can hold URLs, keys or other private details, so they're never shown, only fingerprinted
PRIVATE = {"extra_body"}


@dataclass
class Result:
    model: str
    judge: str
    cases: int
    epochs: int
    run_date: datetime
    version: str
    scores: dict[str, float]
    # half the 95% range around the headline score
    margin: float = 0.0
    cost_per_1k: float | None = None
    # what the tested model's replies cost in this run, for budgeting: cases × epochs of them
    run_cost: float | None = None
    # the headline score of each case, averaged over epochs, for the table of scores on each case
    case_scores: dict[str, float] = field(default_factory=dict)
    # replies the judge couldn't grade: they'd drop out of the average and raise the score
    ungraded: int = 0
    # replies graded under another version of the benchmark, which a retry after an edit keeps
    other_version: int = 0
    # replies cut short by --token-limit, --time-limit or the like: what's graded isn't the model's whole answer
    cut_off: int = 0
    # where the run's log is, so the site can publish just the ranked runs
    location: str = ""
    # settings that change the model's replies or cost, like "temperature: 0", besides the effort
    settings: str = ""
    # the reasoning effort the run asked for, or "default" when it set none and the provider chose
    effort: str = "default"
    # the effort the provider says it used when the run set none, like "medium", if its replies say
    provider_effort: str = ""
    # how the run ended: "success", or "error", "cancelled" or "started" for one that didn't finish
    status: str = "success"
    error: str = ""
    # replies that finished, out of all the run's replies: fewer when some stopped with an error
    completed: int = 0
    total: int = 0

    @property
    def name(self) -> str:
        """The model as the leaderboard shows it, with any settings besides the effort, which has its own column."""
        return with_settings(self.model, self.settings)

    @property
    def effort_text(self) -> str:
        """The effort as the table shows it, like "high", or "medium (default)" when the provider chose."""
        if self.effort == "default" and self.provider_effort:
            return f"{self.provider_effort} (default)"
        return self.effort

    @property
    def label(self) -> str:
        """The name with the effort, for lists that have no effort column."""
        if self.effort == "default":
            return self.name
        return with_settings(self.model, ", ".join(part for part in (f"effort: {self.effort}", self.settings) if part))

    @property
    def contender(self) -> tuple[str, str]:
        """Two runs of one model with different settings or efforts are different contenders, since both change
        quality and cost."""
        return (self.name, self.effort)


def load_results(log_dir: str, benchmark: str, version: str) -> tuple[list[Result], list[Result], list[Result]]:
    """The latest complete run of one benchmark per model and judge, split into runs of this version
    (best first) and runs of an older version that haven't been re-run since, plus this version's
    partial runs, which aren't ranked."""
    prices = load_prices()
    headers = [(info, read_eval_log(info, header_only=True)) for info in list_eval_logs(log_dir)]
    # by model, settings, effort and judge
    current: dict[tuple[str, str, str], Result] = {}
    outdated: dict[tuple[str, str, str], Result] = {}
    partial: list[Result] = []
    for info, log in sorted(headers, key=lambda pair: pair[1].eval.created):
        # runs of other benchmarks, or from before runs recorded their benchmark, don't belong here
        if (log.eval.metadata or {}).get("benchmark") != benchmark:
            continue
        result = to_result(log, prices)
        # a run that crashed, was cancelled or is still going is listed, not ranked, so it doesn't just vanish
        if log.status != "success" or not log.results:
            if result.version == version:
                partial.append(result)
            continue
        # a run with --limit or --sample-id, or one that stopped early, isn't the same test
        if not is_complete(log):
            if result.version == version:
                partial.append(result)
            continue
        if result.version != version:
            outdated[(*result.contender, result.judge)] = result
            continue
        # every reply has to be read: the log's summary misses a reply left ungraded in one epoch of a case
        samples = read_eval_log(info)
        if result.effort == "default":
            result.provider_effort = provider_effort(samples)
        result.other_version = other_version(samples, version)
        result.cut_off = sum(sample.limit is not None for sample in samples.samples or [])
        result.ungraded = ungraded(samples)
        if result.other_version or result.cut_off or result.ungraded:
            partial.append(result)
            continue
        result.case_scores = case_scores(samples)
        result.cost_per_1k = cost_per_1k(samples, prices)
        result.run_cost = run_cost(samples, prices)
        # a newer run of the same model and judge replaces the older one
        current[(*result.contender, result.judge)] = result
    # a partial run made before that model's ranked run has nothing left to say
    partial = [
        result
        for result in partial
        if (*result.contender, result.judge) not in current
        or result.run_date > current[(*result.contender, result.judge)].run_date
    ]
    ranked = sorted(current.values(), key=lambda result: result.scores["overall"], reverse=True)
    stale = [result for key, result in outdated.items() if key not in current]
    return ranked, sorted(stale, key=lambda result: result.run_date, reverse=True), partial


def load_prices() -> dict[str, ModelCost]:
    if not PRICES.exists():
        return {}
    prices: dict[str, ModelCost] = {}
    for model, cost in (load_yaml(PRICES) or {}).items():
        # Inspect needs all four prices, and stops every run with a long traceback without them
        missing = [name for name in ModelCost.model_fields if not isinstance(cost, dict) or name not in cost]
        if missing:
            raise ValueError(
                f"prices.yaml: {model} needs {', '.join(missing)}. If its pricing page lists no cache prices, "
                "use the input price for both."
            )
        prices[model] = ModelCost(**cost)
    return prices


def is_complete(log: EvalLog) -> bool:
    assert log.results
    dataset = log.eval.dataset
    return (
        len(dataset.sample_ids or []) == dataset.samples and log.results.completed_samples == log.results.total_samples
    )


def to_result(log: EvalLog, prices: dict[str, ModelCost]) -> Result:
    # every score is averaged with mean(); runs from before each benchmark had its own rubric used accuracy() for grades
    evaluated = log.results.scores if log.results else []
    scores = {score.name: (score.metrics.get("mean") or score.metrics["accuracy"]).value for score in evaluated}
    # a run made with --no-score, or one that didn't finish, can have no scores at all
    overall = next((score for score in evaluated if score.name == "overall"), None)
    stderr = overall.metrics.get("stderr") if overall else None
    grader = (log.eval.model_roles or {}).get("grader")
    judges = grader if isinstance(grader, list) else [grader] if grader else []
    config = log.eval.model_generate_config
    return Result(
        model=log.eval.model,
        # the judge's bill isn't counted, so caching its prompts (see .env.example) doesn't make it a different judge
        judge=", ".join(
            with_settings(
                judge.model,
                settings_label(judge.config.model_copy(update={"cache_prompt": None}), judge.args, judge.base_url),
            )
            for judge in judges
        )
        or "none",
        cases=len(log.eval.dataset.sample_ids or []),
        epochs=log.eval.config.epochs or 1,
        run_date=datetime.fromisoformat(log.eval.created),
        version=(log.eval.metadata or {}).get("benchmark_version", "unknown"),
        scores=scores,
        margin=1.96 * stderr.value if stderr else 0.0,
        cost_per_1k=cost_per_1k(log, prices),
        run_cost=run_cost(log, prices),
        location=log.location,
        # the effort has its own column, so it stays out of the settings label
        settings=settings_label(
            config.model_copy(update={"reasoning_effort": None}),
            log.eval.model_args,
            log.eval.model_base_url,
            log.eval.config.epochs_reducer,
        ),
        effort=config.reasoning_effort or "default",
        status=log.status,
        completed=log.results.completed_samples if log.results else 0,
        total=log.results.total_samples if log.results else 0,
        error=last_line(log.error.message) if log.error else "",
    )


def last_line(message: str, limit: int = 160) -> str:
    # a model error's message starts with the whole request, so the last line is the one that says what went
    # wrong (its line breaks can be escaped, as \n). When that line is a provider's error object, like
    # {'type': 'error', 'error': {'message': 'Your credit balance is too low…'}}, its message is what to read
    line = re.split(r"\n|\\n", message.strip())[-1]
    provider = re.search(r"""\\?['"]message\\?['"]:\s*\\?['"](.+?)\\?['"]\s*[,}]""", line)
    if provider:
        line = provider.group(1)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def settings_label(
    config: GenerateConfig | None, args: dict[str, Any] | None, base_url: str | None, reducer: list[str] | None = None
) -> str:
    """The settings that change a model's replies, cost or score, as a short label. Model arguments, base URLs
    and other private settings only show as a fingerprint, so the label never publishes their values."""
    chosen = config.model_dump(exclude_none=True) if config else {}
    shown = {key: value for key, value in chosen.items() if key not in OPERATIONAL | PRIVATE}
    parts = [f"{key.replace('_', ' ')}: {shorten(value)}" for key, value in sorted(shown.items())]
    # combining epochs some other way than the mean, like --epochs-reducer max, changes the score
    if reducer is not None and reducer != ["mean"]:
        parts.append(f"epochs reducer: {', '.join(reducer) or 'none'}")
    private = {"args": args or {}, "base_url": base_url, **{key: chosen[key] for key in PRIVATE if key in chosen}}
    if any(private.values()):
        fingerprint = sha256(json.dumps(private, sort_keys=True, default=str).encode()).hexdigest()[:4]
        parts.append(f"custom model args #{fingerprint}")
    return ", ".join(parts)


def shorten(value: Any, limit: int = 24) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def with_settings(model: str, settings: str) -> str:
    return f"{model} ({settings})" if settings else model


def cost_per_1k(log: EvalLog, prices: dict[str, ModelCost]) -> float | None:
    """The run's cost per 1,000 replies, so runs with more cases or epochs compare fairly."""
    cost = run_cost(log, prices)
    if cost is None or not log.results or not log.results.total_samples:
        return None
    return cost / log.results.total_samples * 1000


def run_cost(log: EvalLog, prices: dict[str, ModelCost]) -> float | None:
    """What the tested model's replies in this run cost, at the prices in prices.yaml, so every run is priced
    the same way and a corrected price reaches older runs. A model that isn't in the file uses the cost its
    log recorded, if any. The judge's cost isn't included."""
    recorded = (log.stats.model_usage or {}).get(log.eval.model)
    usage = reply_usage(log) or recorded
    if usage is None:
        return None
    if log.eval.model in prices:
        return compute_model_cost(prices[log.eval.model], usage)
    if recorded is not None and recorded.total_cost is not None:
        return recorded.total_cost
    return None


def reply_usage(log: EvalLog) -> ModelUsage | None:
    """The tested model's usage, added up over its replies. A run's own total leaves out replies Inspect's cache
    answered, but each reply keeps the usage it had when it was first made, so a cached re-run is priced the same."""
    usages = [
        event.output.usage
        for sample in log.samples or []
        for event in sample.events
        if event.event == "model" and event.role is None and event.output.usage is not None
    ]
    return sum(usages[1:], usages[0]) if usages else None


def provider_effort(log: EvalLog) -> str:
    """The reasoning effort the provider used when the run set none. OpenAI's responses say it, like "medium";
    other providers may not, which leaves it blank."""
    for sample in log.samples or []:
        for event in sample.events:
            if event.event == "model" and event.role is None and event.call is not None:
                response = event.call.response
                reasoning = response.get("reasoning") if isinstance(response, dict) else None
                effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
                if isinstance(effort, str):
                    return effort
    return ""


def case_scores(log: EvalLog) -> dict[str, float]:
    per_case: dict[str, list[float]] = defaultdict(list)
    for sample in log.samples or []:
        overall = overall_score(sample)
        if overall is not None:
            per_case[str(sample.id)].append(overall)
    return {case: sum(values) / len(values) for case, values in per_case.items()}


def other_version(log: EvalLog, version: str) -> int:
    """Replies graded under another version of the benchmark. Retrying a run after editing the benchmark re-runs
    only the replies that failed, and the run takes the new version, so the replies it kept are from the old one."""
    return sum(
        (score.metadata or {}).get("benchmark_version") != version
        for sample in log.samples or []
        for score in (sample.scores or {}).values()
    )


def ungraded(log: EvalLog) -> int:
    """Replies with no grade, for example because the judge's answer had none."""
    return sum(overall_score(sample) is None for sample in log.samples or [])


def overall_score(sample: EvalSample) -> float | None:
    score = next(iter((sample.scores or {}).values()), None)
    overall = score.value.get("overall") if score is not None and isinstance(score.value, dict) else None
    return float(overall) if isinstance(overall, (int, float)) else None
