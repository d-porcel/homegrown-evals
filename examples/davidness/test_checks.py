"""Davidness's style checks on sentences that should and shouldn't trip them. The golden answers only
show that good replies pass; a check can also miss what it's for, or fail an innocent sentence."""

from pathlib import Path

import pytest

from benchmark import ROOT, load_benchmark
from judges.checks import Case, load_checks

# this test lives in its benchmark's folder, so it goes wherever the folder goes, and goes away with it
BENCHMARK = Path(__file__).parent.relative_to(ROOT).as_posix()
CHECKS = load_checks(BENCHMARK, load_benchmark(BENCHMARK).grades)
CASE = Case(id="test", category="general", input="", target="", metadata={})

# one short of the AI slop gate, so each sentence below decides whether it fires
THREE_AI_ISMS = "Great question! What a game-changer, truly pivotal."


def style_failures(sentence: str) -> list[str]:
    reply = f"{THREE_AI_ISMS} {sentence}\n\nDavid"
    return [message for check in CHECKS.reply if check.grade == "style" and (message := check.run(CASE, reply))]


def test_three_ai_isms_are_left_to_the_judge():
    assert not style_failures("See you Monday.")


def test_repeats_count_toward_the_gate():
    assert not style_failures_alone("Let's delve in, delve deeper and delve again.")
    assert style_failures_alone("Let's delve in, delve deeper, delve again and delve once more.")


def style_failures_alone(reply: str) -> list[str]:
    return [
        message
        for check in CHECKS.reply
        if check.grade == "style" and (message := check.run(CASE, f"{reply}\n\nDavid"))
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "It's not a tool, it's a testament to what's possible.",
        "It’s not a tool, it’s an opportunity.",
        "Your email isn't just an email, it's a masterclass.",
        "Our beets aren't just beets, they're a partner for growth.",
        "Let's delve into the details.",
        "That really resonates.",
        # the same phrases with characters models use in place of plain ones
        "A truly load\u2011bearing idea.",
        "An ever\u2010evolving landscape.",
        "And\u00a0honestly, it works.",
        "It\u02bcs not a tool, it\u02bcs a mindset.",
        # and with characters that hide a phrase: a zero-width space, a soft hyphen, a Cyrillic "е"
        "Let's del\u200bve into the details.",
        "Let's del\u00adve into the details.",
        "Let's d\u0435lve into the details.",
    ],
)
def test_a_fourth_ai_ism_trips_the_gate(sentence: str):
    assert style_failures(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "If it's monthly, it's worth automating.",
        "Since it's only dev, it's a Monday job.",
        "It's not urgent, so I'll look at it Monday at 9am.",
    ],
)
def test_ordinary_sentences_dont_count(sentence: str):
    assert not style_failures(sentence)
