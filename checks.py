"""Deterministic checks: the hard rules in brief.md and style.md that code can verify without a judge.

Code is free, instant and consistent, so use it for rules whose breach is an automatic fail: banned
phrases, length limits, formats. Leave judgment, and smaller slips, to the judges.

There are two kinds:
- Reply checks are gates on a grade, named after its prompt in rubric/ (like "decisions" or "style"):
  if one fails, that grade is I and its judge isn't asked. When code alone can grade a whole section
  of brief.md or style.md, like a length limit in its own section, name it with covers= and the
  judges won't see it, so that rule has one grader. pytest also runs reply checks on your golden
  answers, so your own reference replies have to pass your own rules. Test them on sentences that
  should fail too, as examples/davidness/test_checks.py does for Davidness.
- Case checks only run in pytest, on the cases themselves, to catch a case that would test the
  wrong thing.

Each check returns a message saying what's wrong, or None when it's fine or doesn't apply.
Uncomment the examples to start, or see examples/davidness/checks.py for real ones.
"""

from judges.checks import Case, case_check, reply_check  # noqa: F401

# One banned phrase is a slip for the style judge to weigh, since it reads the "Never say" list too.
# Three or more is an automatic I. Pick your own phrases and limit.
# @reply_check("style")
# def doesnt_pile_up_banned_phrases(case: Case, reply: str) -> str | None:
#     used = [phrase for phrase in ["<a phrase you never use>", "<another>"] if phrase.lower() in reply.lower()]
#     if len(used) >= 3:
#         return f"Says {', '.join(used)}."


# @case_check
# def says_when_it_happens(case: Case) -> str | None:
#     if not case.input.startswith("It is"):
#         return "The case doesn't say when it happens."
