import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, find_dotenv
from inspect_ai.log import EvalLog, list_eval_logs, read_eval_log, write_eval_log
from inspect_ai.model import GenerateConfig

from benchmark import ROOT, Benchmark, load_or_exit

from .render import as_html, as_text
from .results import Result, load_results


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m leaderboard",
        description="Rank every model that ran a benchmark, from a folder of Inspect logs.",
    )
    parser.add_argument(
        "--benchmark",
        default=".",
        metavar="FOLDER",
        help="the benchmark to rank, e.g. examples/davidness (default: yours, in the repo root)",
    )
    parser.add_argument(
        "log_dir",
        nargs="?",
        default=default_log_dir(),
        help="folder of Inspect logs (default: INSPECT_LOG_DIR from .env)",
    )
    parser.add_argument(
        "--site",
        nargs="?",
        const="",
        metavar="DIR",
        help="also build the leaderboard as a web page in DIR (default: site/<benchmark>, which git ignores)",
    )
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="build the site with Inspect's log viewer too: every prompt, golden answer, reply and judge's reasoning",
    )
    args = parser.parse_args()
    # the viewer lives in the site, so asking for it builds the site, in the default folder unless --site names one
    if args.viewer and args.site is None:
        args.site = ""

    # only runs made with the benchmark's files as they are now get ranked
    bench = load_or_exit(args.benchmark)
    command = "python -m leaderboard" + ("" if bench.folder == "." else f" --benchmark {bench.folder}")
    # a typo in a local folder's name would otherwise just look like a benchmark with no runs
    if "://" not in args.log_dir and not Path(args.log_dir).is_dir():
        sys.exit(f"{args.log_dir} doesn't exist. Pass the folder your runs are in: {command} <folder>")
    # runs are matched to a benchmark by its folder, so moving the folder would quietly drop them all
    old = moved_from(args.log_dir, bench)
    if old:
        sys.exit(
            f"{args.log_dir} has runs of {old}, which no longer exists, and they match {bench.folder}'s files exactly. "
            f"It looks like the benchmark was moved. Runs only count for the folder they were made from, so move it "
            f"back to {old}, or re-run the models from its new place and move the old runs out of {args.log_dir}."
        )
    site = None if args.site is None else Path(args.site or default_site(bench))
    # --site takes an optional folder, so a log folder after it reads as the site's folder
    if site is not None and holds_run_logs(site):
        sys.exit(
            f"{site} holds run logs, so the site can't go there. To rank the runs in it, put the folder before "
            f"--site: {command} {site} --site"
        )
    ranked, outdated, partial = load_results(args.log_dir, bench.folder, bench.version)
    print(as_text(bench, ranked, outdated, partial))
    if not (ranked or outdated or partial):
        print(f"\n{nothing_to_rank(args.log_dir, bench.folder, command)}")

    if site is not None:
        build_site(bench, ranked, outdated, site, viewer=args.viewer)
        built = "the leaderboard"
        if args.viewer and ranked:
            built += f", plus Inspect's log viewer with {len(ranked)} runs, including every prompt and reply"
        elif args.viewer:
            built += ", without the log viewer, since no run is ranked yet"
        print(
            f"\nSite written to {site}/: {built}.\n"
            f"See it at http://127.0.0.1:8000 after running: python3 -m http.server 8000 --bind 127.0.0.1 --directory {site}"
        )


def moved_from(log_dir: str, bench: Benchmark) -> str | None:
    """The folder this benchmark was moved from, if the log folder has runs of the same files from a folder that's
    gone. Judge tests don't count: they don't record a benchmark."""
    for info in list_eval_logs(log_dir):
        metadata = read_eval_log(info, header_only=True).eval.metadata or {}
        folder = metadata.get("benchmark")
        if not isinstance(folder, str) or folder == bench.folder or metadata.get("benchmark_version") != bench.version:
            continue
        path = Path(folder)
        if not (path if path.is_absolute() else ROOT / path).exists():
            return folder
    return None


def nothing_to_rank(log_dir: str, benchmark: str, command: str) -> str:
    """Why the leaderboard is empty: often the folder holds another benchmark's runs, and --benchmark is missing."""
    headers = (read_eval_log(info, header_only=True) for info in list_eval_logs(log_dir))
    found = {(log.eval.metadata or {}).get("benchmark") for log in headers}
    others = sorted(name for name in found if isinstance(name, str) and name != benchmark)
    if others:
        yours = "your benchmark in the repo root" if benchmark == "." else benchmark
        return (
            f"{log_dir} has runs of {', '.join(others)}, not {yours}. "
            f"Rank them with: python -m leaderboard --benchmark {others[0]} {log_dir}"
        )
    return f"Nothing in {log_dir} to rank or list. If your runs are in another folder: {command} <folder>"


def holds_run_logs(folder: Path) -> bool:
    # a site built with --viewer keeps copies of its runs in viewer/, so rebuilding it is fine
    return folder.is_dir() and any((folder / "viewer") not in path.parents for path in folder.rglob("*.eval"))


def default_site(bench: Benchmark) -> Path:
    """A folder per benchmark, so building one benchmark's site never overwrites another's. Git ignores site/,
    so a site is only published when you choose to."""
    name = bench.name if bench.folder == "." else Path(bench.folder).name
    return Path("site") / (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "benchmark")


def build_site(bench: Benchmark, ranked: list[Result], outdated: list[Result], site: Path, viewer: bool) -> None:
    """The leaderboard page, and with viewer, Inspect's own log viewer, so readers can go from a score to the
    replies behind it."""
    site.mkdir(parents=True, exist_ok=True)
    # with no run ranked, there are no replies to publish, and Inspect can't bundle an empty folder
    viewer = viewer and bool(ranked)
    if viewer:
        with tempfile.TemporaryDirectory() as published:
            copy_logs(ranked, Path(published))
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "inspect_ai",
                    "view",
                    "bundle",
                    "--log-dir",
                    published,
                    "--output-dir",
                    str(site / "viewer"),
                    "--overwrite",
                ],
                check=True,
            )
        strip_local_path(site / "viewer" / "index.html")
    elif (site / "viewer").exists():
        # a viewer from an earlier build would still publish every reply, without the page linking to it
        shutil.rmtree(site / "viewer")
    (site / "index.html").write_text(as_html(bench, ranked, outdated, viewer=viewer), encoding="utf-8")


def copy_logs(ranked: list[Result], folder: Path) -> None:
    """Only the ranked runs get published. Inspect's bundle takes a whole folder, and the log folder can also
    hold test runs, out-of-date runs and other benchmarks' runs, each with its brief and style guide."""
    for result in ranked:
        published = without_private_settings(read_eval_log(result.location))
        write_eval_log(published, str(folder / result.location.rsplit("/", 1)[-1]))


def without_private_settings(log: EvalLog) -> EvalLog:
    """A run as it gets published: without model arguments, server URLs or extra request fields and headers,
    for the tested model or the judge, since they can hold keys, emails or internal addresses. The leaderboard
    only shows them as a fingerprint. Local paths and the git remote go too. The run's own log isn't changed."""
    spec = log.eval
    roles = [
        config
        for value in (spec.model_roles or {}).values()
        for config in (value if isinstance(value, list) else [value])
    ]
    private = bool(spec.model_args or spec.model_base_url or has_extras(spec.model_generate_config))
    private |= any(role.args or role.base_url or has_extras(role.config) for role in roles)
    private |= has_extras(log.plan.config)
    spec.model_args, spec.model_base_url = {}, None
    spec.model_generate_config = without_extras(spec.model_generate_config)
    # the run's plan records its generation settings too
    log.plan.config = without_extras(log.plan.config)
    for role in roles:
        role.args, role.base_url, role.config = {}, None, without_extras(role.config)
    # a benchmark given as an absolute path would publish your home folder's name
    paths: dict[str, str] = {}
    for values in (spec.task_args, spec.metadata or {}):
        if isinstance(values.get("benchmark"), str) and Path(values["benchmark"]).is_absolute():
            path = Path(values["benchmark"])
            paths[str(path)] = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    # the git remote can name a private repo, and failed attempts' tracebacks are full of local paths
    spec.revision = None
    for sample in log.samples or []:
        sample.error_retries = None
    if private:
        # each model call also records the request it sent, which repeats those settings
        for sample in log.samples or []:
            for event in sample.events:
                if event.event == "model":
                    event.call, event.config = None, without_extras(event.config)
    return without_local_paths(log, paths)


def without_local_paths(log: EvalLog, paths: dict[str, str]) -> EvalLog:
    """Local paths turn up all over a log: in the task's arguments, the scorer's settings, the results and the task
    file. Rather than chase each field, every one is replaced: the benchmark's own path, then the repo's, then your
    home folder's."""
    text = log.model_dump_json()
    home = str(Path.home())
    for local, shown in [*paths.items(), (f"{ROOT}/", ""), (str(ROOT), "."), (f"{home}/", "~/"), (home, "~")]:
        # as JSON writes them, so a Windows path's backslashes match too
        text = text.replace(json.dumps(local)[1:-1], json.dumps(shown)[1:-1])
    return EvalLog.model_validate_json(text)


def has_extras(config: GenerateConfig | None) -> bool:
    return bool(config and (config.extra_body or config.extra_headers))


def without_extras(config: GenerateConfig) -> GenerateConfig:
    return config.model_copy(update={"extra_body": None, "extra_headers": None})


def strip_local_path(viewer_page: Path) -> None:
    """The viewer finds its logs by a relative path, but Inspect's bundle also records the absolute one,
    which would publish your home folder's name. Drop it."""
    page = viewer_page.read_text(encoding="utf-8")
    context = re.search(r'<script id="log_dir_context" type="application/json">(.*?)</script>', page, re.S)
    if context:
        settings = json.loads(context.group(1))
        settings.pop("abs_log_dir", None)
        viewer_page.write_text(
            page[: context.start(1)] + json.dumps(settings) + page[context.end(1) :], encoding="utf-8"
        )


def default_log_dir() -> str:
    # the folder inspect eval writes to: INSPECT_LOG_DIR from the environment or .env
    env_file = dotenv_values(find_dotenv(usecwd=True))
    return os.environ.get("INSPECT_LOG_DIR") or env_file.get("INSPECT_LOG_DIR") or "logs"


if __name__ == "__main__":
    main()
