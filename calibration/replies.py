import hashlib
from dataclasses import dataclass

from inspect_ai.log import list_eval_logs, read_eval_log


@dataclass
class Reply:
    key: str
    model: str
    case: str
    situation: str
    text: str
    must_haves: str
    grades: dict[str, str]


def load_replies(log_dir: str) -> list[Reply]:
    """Every graded reply in log_dir, with the benchmark's grades (from the judges and the checks)."""
    replies: list[Reply] = []
    for info in list_eval_logs(log_dir):
        log = read_eval_log(info)
        if log.status != "success":
            continue
        for sample in log.samples or []:
            score = next(iter((sample.scores or {}).values()), None)
            if score is None or not isinstance(score.value, dict):
                continue  # unscored: the judge gave no grade
            replies.append(
                Reply(
                    key=reply_key(log.eval.model, str(sample.id), sample.epoch),
                    model=log.eval.model,
                    case=str(sample.id),
                    situation=sample.input if isinstance(sample.input, str) else "",
                    text=sample.output.completion,
                    must_haves=(sample.metadata or {}).get("must_haves", ""),
                    # every key but the average is a grade from the benchmark's rubric
                    grades={grade: str(value) for grade, value in score.value.items() if grade != "overall"},
                )
            )
    return replies


# keys start with a letter that isn't a hex digit, so a spreadsheet app never reads one as a number,
# like 0123456789 losing its leading zero, or 1234567e89 turning into 1.23E+95
KEY_PREFIX = "k"


def reply_key(model: str, case: str, epoch: int) -> str:
    # an opaque ID, so the spreadsheet doesn't reveal which model wrote each reply
    return KEY_PREFIX + hashlib.sha256(f"{model}|{case}|{epoch}".encode()).hexdigest()[:10]


def normalize_key(key: str) -> str:
    """A key from a graded sheet. Sheets exported before keys started with the prefix still match."""
    key = key.strip()
    return key if key.startswith(KEY_PREFIX) else KEY_PREFIX + key
