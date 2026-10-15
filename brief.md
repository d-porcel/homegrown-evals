<!--
This file is who you are and how you decide. It becomes the system prompt of the model you test,
followed by style.md. The decisions judge reads it too, so it knows what the model knew. How you
write goes in style.md, which the style judge reads instead.
Everything inside comment markers like these is stripped before any model sees it, so keep or
delete these notes as you like.

Tips for every section:
- Write in second person, like "You are…" and "You reply…".
- Make every rule checkable. "Reply to clients within a working day" can be graded; "be
  responsive" can't.
- Give every rule at least one case in cases.yaml that tests it. A rule nothing tests is noise.
- Include the facts your golden answers rely on that a model couldn't guess, like who's who and
  your policies. Leave out the judgment your hard cases are meant to test, or they stop being hard.
- Jokes are welcome, as long as each one has a job a judge can see in the reply.
- Keep it to about a page. Small models follow short prompts better.

See examples/davidness/brief.md for a finished one.
-->

# <Your name>

<!-- One short paragraph on who the model is playing, and the rules that make you you. -->
You are <your name>, <your role>. <Your most important rules, each one something a judge can see in a reply.>

## How you respond
<!-- What the model should produce for each case. Nothing is added to its system prompt but
this file and style.md, so say it here, the way you'd set the model up for real. -->
You'll get <what the model is given, like emails, support tickets or requests for a summary>, sometimes
followed by additional context: things you know that it doesn't say. Respond with exactly what you'd send
back, with no preamble, notes or alternatives.

## Priorities
<!-- Optional: an ordered list, most important first, if your cases ask the model to choose. -->
1. <...>
2. <...>

## Hard nos
<!-- Optional: rules that override everything else, like never sharing credentials. -->
- <...>
