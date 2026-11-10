# Daily Brief prototype

A throwaway prototype run on 2026-11-06. It asked whether a Daily Brief built from Pursuits would make the owner act. It wrote three Daily Briefs for one past morning, each choosing and wording its Steps a different way, and checked every Step against what the owner did over the next four days. Vocabulary is defined in [GLOSSARY.md](../../GLOSSARY.md). The Pursuits were built with the rules in [sessions-to-pursuits-prototype.md](sessions-to-pursuits-prototype.md).

The code, the sample, the Pursuits, the briefs, and the backtest lived in a session scratch folder. Every file holding conversation content was deleted at the end. This file is the record.

## How to read this file

Every claim carries one of four marks, as in the first prototype's record.

| Mark | Meaning |
|---|---|
| **(M)** | Measured by a script during the prototype. |
| **(R)** | Reported by an agent in its output, and not checked independently. |
| **(J)** | The judgment of the agent that ran the prototype. |
| **(O)** | Decided by the owner. |

This file holds structure and counts only. It contains no conversation content, no repository, ticket, or people names, and no ids.

## Result in brief

- The three ways of choosing Steps tied. Each brief had 1 Step the owner did, 1 the owner partly did, and 1 on which the owner did other work. (R)
- All three briefs chose the same three Pursuits. Which Pursuit a Step named mattered more than how the brief chose or worded it. (M for the choice, J for the reading)
- The owner chose to combine the best part of each brief. See [What the owner decided](#what-the-owner-decided). (O)
- The rule for Neglected could not be tested on this machine. (M)

## The machine and the sample

- **A different machine.** This prototype ran on a Linux machine whose Claude Code and Codex stores both start on 2026-10-07. The first prototype's September sample does not exist here. (M)
- **Owner-started Sessions on this machine.** 153 Codex threads have no `thread_source`, a `source` of `vscode`, and an `originator` of `T3 Code`. These are threads the owner started through T3 Code. 125 threads are `subagent` and 61 are `guardian_review`, and both were left out as Sessions of their own. (M)
- **Claude Code `entrypoint`.** Sessions on this machine carry `cli`, `sdk-cli`, `sdk-ts`, or `t3-code`. `t3-code` first appears on 2026-11-04, the same day `sdk-ts` last appears. (M) `sdk-ts` is how earlier versions of T3 Code ran Claude Code. (J)
- **As-of date.** Tuesday 2026-10-20. The four days after it are the busiest on the machine, so the briefs had real work to be checked against. (M for the counts, J for the choice)
- **Selection rules.** The same as the first prototype. Codex threads the owner started, with at least 2 assistant messages, largest by prompt and answer text first. Every Claude Code Session with at least 5 assistant text messages. (M)
- **Sample.** 50 Sessions that started before the as-of date: 42 Codex and 8 Claude Code, from a pool of 65 eligible. The 15 smallest Codex threads were left out. (M)
- **Compaction.** The same format and caps as the first prototype. 14 of the 50 Sessions were scaled. The sample keeps 374,207 characters. (M)
- **No look-ahead.** Every Session was cut at midnight local time on the as-of date. Only the backtest agent read anything later. (M for the cut, R for each agent keeping to it)
- **After window.** 2026-10-20 to 2026-10-23 holds 94 Session slices. 2 of them continue Sessions from the sample. (M)

## Pursuits, Focus, and Neglected

The Pursuits were not the thing under test, so one agent built them in one pass.

- **Builder.** Approach B from the first prototype, on Claude Opus 5.5 at medium effort, in 5.5 minutes. (M)
- **Output.** 5 Pursuits: 4 projects and 1 idea, all active. 2 Sessions filed as not a Pursuit. 1 proposed ending, done. 45 Facts and 33 Inferences, every quote found in its Session. (M)
- **Errors found later.**
	- One Pursuit's outcome for the day before was wrong. All three brief agents overruled it from the same Session. (R)
	- One Resume Note said a request had no reply. One brief agent found the reply. (R)
	- The builder split one Pursuit across two folders. That breaks the owner's rules that a rewrite continues the same Pursuit and that work moving to another folder stays one Pursuit. (J)
	- The builder folded tool-setup chores into a project. (J)
- **Focus.** Proposed by a rule: active Pursuits with no proposed ending, most recent activity first, at most five. That gave 4 of the 5 Pursuits. (M) With so few Pursuits, Focus leaves almost nothing out. (J)
- **Neglected.** A 30-day threshold found no Pursuit, because the oldest Session is 13 days before the as-of date. (M) The briefs used a stand-in labelled as one: the quietest active Pursuit outside Focus. That is the Pursuit the builder split by mistake. (M for the choice, J for the split)
- **The two rules collide.** The day-one rule starts anything quiet for 30 days as parked. A 30-day rule for Neglected therefore cannot fire on day one, and it only catches Pursuits that go quiet after Atlas starts. (J) The v1 brief sets no threshold for Neglected. The 30 days comes from the day-one rule. (J)

## The three briefs

Three agents wrote one brief each, on Claude Opus 5.5 at high effort, in parallel. Nothing was merged. (M)

| | How it chose Steps | How it worded them | Words | Facts |
|---|---|---|---|---|
| **V1 Momentum** | The stored next Step of the three Focus Pursuits with the latest activity | A terse checklist, one line per field | 217 | 7 |
| **V2 Unblock first** | The smallest Step that clears a blocker or settles an unknown, under 30 minutes, at most one per Pursuit | A coach, with a short line on why today | 218 | 5 |
| **V3 Close the owner's open loop** | The last thing the owner asked for in each Pursuit that has no confirmed result | A question, with a prompt to paste into a Harness | 518, about 219 without the prompts | 17 |

- Every Fact's quote exists in the Session it cites, in all three briefs. (M)
- Each brief rebuilt the previous day's Steps by applying its own rule to the state at the end of that day, then judged them against the next day's Sessions. (R)
- Each brief marked every statement as a Fact or an Inference, as the v1 brief requires. (M)

## Backtest

One agent, on Claude Opus 5.5 at high effort, judged the 9 Steps in shuffled order without knowing which brief each came from. It read the 94 slices in the after window. (M for the setup)

| | Did | Partly | Same Pursuit, other work | Not touched |
|---|---|---|---|---|
| V1 | 1 | 1 | 1 | 0 |
| V2 | 1 | 1 | 1 | 0 |
| V3 | 1 | 1 | 1 | 0 |

All rows are (R). The agent running the prototype checked the three claims the counts depend on against the Session files and found each one there. (M)

- **First Pursuit.** All three Steps were done on the as-of date. V3's matched most fully. V2's was a status check, which is easy to satisfy. (R for the verdicts, J for the reading)
- **Second Pursuit.** V1 and V2 asked for a commit, which the owner made early that morning, probably before a brief would have arrived. V3 asked for a multi-day build by a route the owner did not take, and the owner built it two days later by another route. (R, with J for "probably")
- **Third Pursuit.** All three missed. The owner's only work on it was different from the suggested Step. If that work belongs to another Pursuit, all three Steps become "not touched". (R)
- **Where the owner's time went.** The first two Pursuits got 83 of the 94 slices. The third got 1. 7 slices went to Pursuits that did not exist on the as-of date, and 3 were one-off questions. (R)
- **No Step was stale.** (R)
- **What the backtest shows.** It shows whether a brief predicted what the owner did. It cannot show that a brief would have made the owner do it, because the owner did the work without one. (J)
- **Marks not collected.** The owner did not mark each Step "i would do this", "wrong", or "stale" before deciding. (O)

## What the owner decided

The owner chose to combine one part from each brief. The owner also decided not to repeat the test on the machine that holds the September Sessions. (O)

The combination, as the agent recommended it and the owner accepted it, reads against the v1 brief like this. The wording is the agent's. (J)

- **Choose Steps as V2 does.** Pick the smallest Step that clears a blocker or settles an unknown, at most one per Pursuit. A brief may carry fewer than three Steps when a third would be filler. This fits the v1 brief's Step, "small enough for one sitting". V3 broke that with a multi-day build, and V1 bundled two actions into one Step.
- **Lay the brief out as V1 does.** Give one line on where the owner left off and one line on how to resume, as the v1 brief says.
- **Resume with V3's paste-in prompt.** Offer a prompt the owner can paste into any Harness, kept out of the message body and reached from the Step. This serves the v1 brief's success measure, picking up any Pursuit in under a minute, and its rule that a Resume Note works in a different Harness. Atlas does not act on the outside world in v1, so handing the owner the prompt is as far as it can go.
- **Leave the tone open.** Checklist, coach, and question made no measurable difference.

The owner accepted the combination without seeing a brief written that way. (O)

## Open points

- The threshold for Neglected. Settled after this record: 14 quiet days, outside Focus only, the quietest first, with the clock reset when the owner answers "continue". See the v1 brief. (O)
- Whether Focus is worth having while the owner has only a handful of active Pursuits.
- How the paste-in prompt reaches the owner. That depends on the Channel, which is a parked "how" question.
- Whether a Step drawn from the owner's own request beats one drawn from the assistant's suggestion. The backtest has one case each way.
- The builder's split of one Pursuit across two folders, which the Neglected stand-in exposed.
