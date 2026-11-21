# Skill iteration protocol

How a research workflow skill under `skills/` is improved and when a change is accepted.
Research use only. Not clinical decision support.

## Why this protocol

A skill is a prompt. It can be tuned until it fits the test cases its author has seen and
then fail on new ones (observed: report review rose from .87 to .92 on development reports
across four versions and was −.06 on unseen reports). The protocol keeps three things apart:
the cases the author learns from, the cases that check a change, and the cases that decide.

## Data

| Split | Who sees per-case detail | Used for |
|---|---|---|
| train | author, agents | misses, transcripts, agent feedback; drives edits |
| validation | nobody per case: aggregates only | accept or reject an edit |
| held-out | nobody; written by the benchmark session after the skill is frozen | the one claim made in a PR |

Every dataset is synthetic and de-identified. Before a round uses it, each case is audited
(see "Dataset audit"). A held-out set is used once; after that it is development data.

## Arms and agents

Each round runs every train case in two arms, with the skill (SKILL.md plus the one
reference for the case type, as a host would load it) and without, on at least two agents:

- a weaker or cheaper model (e.g. Gemini Flash or gpt-oss through the `agy` CLI), where a
  skill has most to add and most to lose (budget, long text);
- a strong model (e.g. Claude Opus or Sonnet), the realistic ceiling.

All agents use the live GeneFoundry MCP, run from an empty working directory, and read no
local files. Paid hosted runs need the owner's approval.

## Measures

Per case: recall of planted errors, unplanted Critical/Major findings (adjudicated: a real
error that was not planted is a fixture defect, not a false alarm), controls passed (no
Critical/Major on a clean report), tool calls, failed calls, tokens. Per arm: means and a
paired difference with a 95% bootstrap interval.

With-skill runs also return structured feedback: usefulness and clarity (1–5), how much
was followed, instructions that changed the result, instructions skipped or confusing,
what was missing, one proposed edit.

## One round

1. Run train × arms × agents.
2. Collect misses, unplanted findings, failed calls, and the feedback.
3. Write the change log entry before editing: each edit names the general rule it adds or
   removes and the evidence (feedback from at least two cases or agents, or a miss class
   seen on at least two cases). An edit that only names one case's answer is rejected.
4. Prefer deleting or shortening text that agents report as skipped or confusing.
5. Edit, lint (`make lint-skills`), commit, record the new SKILL.md sha.
6. Run validation × both arms × both agents. Accept the version if, on both agents, it is
   not worse than the previous version on recall and controls and not more expensive by
   more than 20% tokens without a recall gain. Otherwise revert.
7. Stop after three rounds or when a round changes nothing measurable.

## Dataset audit

Before a dataset is used, an independent agent checks each case against the live tools:
every planted error is a real error with a correct "true" value; every fact the report
states that is not planted is correct (or the case gets a new key row); the error can be
found with the reading or the tools available; the report is plausible for its type. Fixture
defects go back to the dataset owner before the round runs.

## Freeze and claim

The version accepted after the last round is frozen. The benchmark session writes a fresh
held-out set the author has not seen, runs it once (no skill against the frozen skill,
paired), and reports aggregates. The PR states that result, whatever it is.
