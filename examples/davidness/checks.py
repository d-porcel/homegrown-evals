"""Deterministic checks for Davidness: hard rules code can verify, so breaking one is an automatic I.
A check that enforces a whole section of style.md names it with covers=, and the judges don't see it.

pytest also runs the reply checks on the golden answers. Case checks only run in pytest, on the cases
themselves, so they can afford to be fuzzier.
"""

import re
import unicodedata
from pathlib import Path

from benchmark import read_markdown
from judges.checks import Case, case_check, reply_check

# without its <!-- comments -->, as the model and the judges read it
STYLE = read_markdown(Path(__file__).parent / "style.md")

# "it's not X, it's Y", "isn't just X, it's Y" and the like: banned in replies, and the brief's other slop cannon
# trigger, besides "load-bearing". A plain "it's X, it's Y" doesn't count: "if it's monthly, it's worth automating" is fine
NOT_X_BUT_Y = re.compile(r"\b(it's (not|just)|isn't|aren't)( just)? \w+( \w+)?, (it's|they're)", re.I)

# a colon that sets up the point, like "Honest answer: …"
COLON_SETUP = re.compile(r"[A-Za-z?!)]: \w")

# models often write lookalikes of plain characters: curly or modifier apostrophes (it’s), and hyphens and
# dashes in place of "-" (load‑bearing with a non-breaking hyphen). Cyrillic letters that look Latin, like the
# "е" in "dеlve", would slip a phrase past the check too. Each becomes the plain one
LOOKALIKES = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u02bc", "'"),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
        **dict(zip("аеорсухіјѕ", "aeopcyxijs", strict=True)),
    }
)


def never_say_phrases() -> list[str]:
    # the quoted phrases in the "Never say" section of style.md
    section = STYLE.split("### Never say", 1)[1].split("\n## ", 1)[0]
    return re.findall(r'^- "([^"]+)"$', section, re.M)


def is_slop_cannon(case: Case) -> bool:
    return case.category == "slop-cannon"


def plain(text: str) -> str:
    # NFKC turns non-breaking spaces and other compatibility characters into plain ones. Invisible characters, like
    # a zero-width space or a soft hyphen inside a word, go, then the lookalikes
    text = unicodedata.normalize("NFKC", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return text.lower().translate(LOOKALIKES)


def fires_slop_cannon(text: str) -> bool:
    return "load-bearing" in plain(text) or bool(NOT_X_BUT_Y.search(plain(text)))


@reply_check("style", covers="Slop cannon length")
def slop_cannon_is_short(case: Case, reply: str) -> str | None:
    if is_slop_cannon(case) and len(reply) >= 1000:
        return f"The slop cannon reply is {len(reply)} characters; the limit is 1000."


# one AI-ism is a slip for the style judge to weigh; this many in one reply, repeats included, reads like AI slop
AI_SLOP = 4


@reply_check("style")
def doesnt_read_like_ai(case: Case, reply: str) -> str | None:
    # code can't tell a parody from real slop, so this goes by the case, not the reply. A slop cannon fired in
    # any other case trips it too, so it gets I for style as well as decisions. That's on purpose: going by the
    # reply's own "load-bearing" or "it's not X, it's Y" would let real slop that uses one past the gate
    if is_slop_cannon(case):
        return None
    uses = {phrase: plain(reply).count(plain(phrase)) for phrase in never_say_phrases()}
    uses["\"it's not X, it's Y\""] = len(NOT_X_BUT_Y.findall(plain(reply)))
    found = {phrase: count for phrase, count in uses.items() if count}
    if sum(found.values()) >= AI_SLOP:
        listed = ", ".join(f"{phrase} ×{count}" if count > 1 else phrase for phrase, count in found.items())
        return f'Reads like AI slop, with {sum(found.values())} uses of the "Never say" section of style.md: {listed}.'


@case_check
def no_accidental_slop_cannon(case: Case) -> str | None:
    if not is_slop_cannon(case) and fires_slop_cannon(case.input):
        return "This email would fire the slop cannon, but the case isn't a slop cannon case."


@case_check
def slop_cannon_has_a_trigger(case: Case) -> str | None:
    if is_slop_cannon(case) and not fires_slop_cannon(case.input):
        return "This slop cannon case has no trigger in its email."


@case_check
def golden_answer_has_no_colon_setup(case: Case) -> str | None:
    if not is_slop_cannon(case) and COLON_SETUP.search(case.target):
        return "The golden answer sets up its point with a colon."
