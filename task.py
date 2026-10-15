"""The Inspect task: turns a benchmark folder (brief.md, style.md, cases.yaml, checks.py) into an eval.

inspect eval task.py                                    your benchmark, in the repo root
inspect eval task.py -T benchmark=examples/davidness    the example, Davidness
"""

import re

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset
from inspect_ai.solver import generate, system_message

from benchmark import load_benchmark, system_prompt, to_sample
from judges import benchmark_judge, judge_is_not_the_tested_model


@task
def run_benchmark(benchmark: str = "."):
    bench = load_benchmark(benchmark)
    return Task(
        name=re.sub(r"[^a-z0-9]+", "-", bench.name.lower()).strip("-"),
        dataset=MemoryDataset([to_sample(case) for case in bench.cases], name=bench.name),
        solver=[judge_is_not_the_tested_model(), system_message(system_prompt(bench.brief, bench.style)), generate()],
        scorer=benchmark_judge(benchmark=bench.folder),
        metadata={"benchmark": bench.folder, "benchmark_name": bench.name, "benchmark_version": bench.version},
    )
