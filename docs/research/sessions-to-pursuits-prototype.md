# Sessions to Pursuits prototype

A throwaway prototype run on 2026-11-06. It asked whether a model can read the owner's real Sessions and produce Pursuits the owner would trust, and it compared three ways of doing that. Vocabulary is defined in [GLOSSARY.md](../../GLOSSARY.md). Where each Harness keeps Sessions is in [harness-and-hermes-findings.md](harness-and-hermes-findings.md).

The code, the sample, and the three outputs lived in a session scratch folder and were not kept. This file is the record.

## How to read this file

Every claim carries one of four marks.

| Mark | Meaning |
|---|---|
| **(M)** | Measured by a script during the prototype. |
| **(R)** | Reported by an approach in its own log or notes, and not checked independently. |
| **(J)** | The judgment of the agent that ran the prototype. |
| **(O)** | Decided by the owner. |

Like the findings file, this file holds structure and counts only. It contains no conversation content, no repository or ticket names, and no ids.

## Result in brief

- A model can do this. All three approaches produced Pursuits that name real work, each with a next Step specific enough to act on. (J)
- No approach is right as it stands. They agree on the simpler two thirds of the sample and divide the two busiest repositories differently. (M for the agreement figures, J for the reading)
- The owner chose the division that approach B produced. See [What the owner decided](#what-the-owner-decided). (O)
- Status was not tested, because no Session in the sample shows the owner setting work aside. (M)

## The sample

The sample was built once, read-only, and all three approaches read the same files.

- **Size:** 50 Sessions. 39 are Codex Sessions that started in September 2026. 11 are Claude Code Sessions: 1 from August and 10 from September. (M)
- **Why not August for Claude Code:** the owner asked for August. August holds 3 Claude Code Sessions, and only 1 has any assistant text. September was the nearest month with enough. (M)
- **Selection rule, Codex:** of 170 September rollouts, 105 are threads the owner started. The 39 largest by prompt and answer text were taken, among those with at least 2 assistant messages. Subagent threads and threads an agent created were left out as Sessions of their own. (M)
- **Selection rule, Claude Code:** every August or September Session with at least 5 assistant text messages. (M)
- **Spread:** the 50 Sessions cover 7 repositories, and 45 of them sit in 3. (M)

Each Session became one compact Markdown file:

- A header gives the Harness, the folder, the git branch, the start and last-message times, and the counts.
- The body holds the owner's prompts and the assistant's text in time order. Tool calls and tool output are removed.
- A subagent conversation is folded into its parent as one entry with its name, its brief, and its last report. 22 Sessions had subagent conversations, 50 in total. (M)
- Long messages are cut with a visible marker. The caps are 1,700 characters for an owner prompt and 1,800 for the assistant's last answer in a turn. Earlier assistant messages in a turn are reduced to a line each, or dropped.
- A Session over 11,000 characters has its caps scaled down until it fits. The last turn is never scaled, because the Resume Note depends on how the Session ended. 14 of the 50 Sessions were scaled. The largest keeps 16,231 of 115,854 characters. (M)

The compact sample keeps 381,049 of 899,684 characters of prompt and answer text, in 415 KB of files. (M)

## What each approach had to produce

Each approach wrote one JSON file of Pursuits. Every Pursuit has a name, a Kind, a Status, its Sessions, a Resume Note, and a next Step. The Resume Note has four lists of statements (where the owner left off, what was decided, open questions, where the work is) and one to three Sessions to reopen.

- **Fact and Inference.** Every statement is marked. A Fact names one Session and carries a quote of 15 to 200 characters copied from that Session's file. An Inference names the Sessions it rests on. A shared validator checked that each quote exists in the cited file, ignoring whitespace.
- **Status.** The approaches were told that today is 2026-10-02, the day after the last Session. They applied the day-one rule from the brief: a Pursuit touched in the last 30 days starts active and anything older starts parked. An approach could not write done or dropped as a Status. It could propose an ending.
- **Kind.** The brief names the four Kinds and does not define them. The prototype used working definitions written by the agent, which the owner has not reviewed. (J)
- **Coverage.** Every Session had to appear in a Pursuit or in a list of Sessions that are not Pursuits.

## The three approaches

All three ran on Claude Opus 5.5 at high effort, each as one agent conversation, in parallel, with its own output directory. Nothing was merged between them. The transcripts confirm that each followed its procedure. (M)

| | Procedure | What it stands for in a real build |
|---|---|---|
| **A. Cards, then cluster** | Read each Session alone and write a card: intent, outcome, unfinished business, location, quotes. Form Pursuits from the 50 cards only. | 50 independent model calls, then one call to cluster. |
| **B. Group by folder, then read each group** | Group Sessions by repository from metadata, with no model reading. Read one group's Sessions together and decide what Pursuits are in it. Merge across groups from the drafts. | A free first step in code, then one model call per group. |
| **C. Ledger, one Session at a time** | Take Sessions in start order. File each against a running ledger of Pursuits and update the Resume Note and next Step. Never read ahead, never reopen, no final reorganising pass. | Daily upkeep: one call per new Session, which sees the ledger and that Session. |

## Results

| | A | B | C |
|---|---|---|---|
| Pursuits | 11 | 9 | 8 |
| Sessions filed as not a Pursuit | 1 | 1 | 1 |
| Kinds | 8 project, 1 experiment, 1 idea, 1 problem | 7 project, 1 experiment, 1 idea | 5 project, 2 experiment, 1 problem |
| Parked Pursuits | 1 | 1 | 1 |
| Proposed endings | 2 done | 2 done | none |
| Facts and Inferences | 91 and 35 | 99 and 28 | 83 and 21 |
| Facts with a quote not found, first validator run | 0 | 1 | 2 |
| Facts with a quote not found, final file | 0 | 0 | 0 |
| Wall time, minutes | 14.3 | 11.2 | 22.9 |
| Output tokens | 86,795 | 68,597 | 147,096 |
| Fresh input tokens | 334,931 | 326,860 | 422,702 |
| Cached re-read tokens | 10,615,882 | 6,981,441 | 17,461,163 |
| Model calls | 59 | 39 | 70 |

All rows are (M), from the output files and the agent transcripts.

How alike the groupings are, counted over pairs of Sessions that at least one approach puts in the same Pursuit: A and B agree on 162 of 243 pairs (67%), A and C on 189 of 317 (60%), B and C on 205 of 322 (64%). (M)

### Where the approaches agreed

- The same single Session is a one-off question and not a Pursuit. (M)
- The one August Session is its own parked Pursuit. A and B call it an idea, C a project. (M)
- One project holds the same 11 Sessions in all three. (M)
- One experiment, one cleanup effort, and one compatibility check each stand alone in all three, with small differences in which Sessions they hold. (M)

### Where the approaches differed

All three differences are about how finely to divide a busy repository. (M for the divisions)

1. **An API project of 14 Sessions.** C keeps one Pursuit. A separates building and deploying from a later feature. B separates the feature from a staging deployment that has its own leftover chores.
2. **A service whose specification was rewritten part-way.** A treats the original and the rewrite as two Pursuits and a related agent as a third. B joins the original and the rewrite and keeps the related agent apart. C puts all of it in one Pursuit of 18 Sessions.
3. **A time-logging task found inside one Session.** A and C made it a Pursuit. B judged it a one-off chore.

## What the owner decided

The owner compared the three results and chose B's division of the two busy repositories. (O)

Stated as rules, B's division is the following. The wording is the agent's reading of B's reconciliation notes, and the owner has not reviewed the wording. (J)

- A Pursuit is a place the owner would resume from. It is not a repository, a ticket, or a specification.
- A rewrite continues the same Pursuit.
- A deployment with its own leftover work is a separate Pursuit from the feature it deploys.
- Work that moves to another folder stays one Pursuit.

The owner's choice covers the first two differences above. The third, whether a chore found inside a Session is a Pursuit, was not put to the owner and is open.

The choice settles how finely to divide. It does not settle B's procedure, which has the weaknesses listed next.

## What each procedure did to its result

- **A cannot see what only shows when two Sessions are compared.** It could not tell that a rewrite replaced an earlier specification, so it split them and wrote a next Step that asks the owner a question. (R) It chose its quotes before it knew which Pursuit a Session would join. (R) Its first validator run was clean. (M)
- **B's free grouping step did little where it mattered.** Grouping by repository put 26 of the 50 Sessions in one group. (M) B caught links between folders only because one agent had read every group. Separate model calls per group would not have that memory. (R)
- **C lumps, and a lump stays.** It made one merge and one split in 50 Sessions. (R) The caps on statements forced it to drop older facts, so its two largest Pursuits show only the latest slice. (R) Sessions are ordered by start time, and a long Session can end after later ones start, so C sometimes filed a newer state before an older one. (R)
- **C is the only approach that tracks Steps.** C logged that the Step it had suggested was carried out by the next Session in 20 of 44 cases. C says that count is low, because a worker Session arrives after the Session that directed it. (R)

## Fact and Inference

- In the final files, every Fact's quote exists in the Session it cites: 273 of 273. (M)
- Every next Step and every Status reason is an Inference in all three. Every "decided" statement is a Fact. (M)
- The agent read 21 Facts, 7 drawn at random from each approach. In 20 the quote supports the main claim. One states the owner's objection as a decision and should be an Inference. In 8 the sentence adds a detail that its quote does not carry. (J)
- A quote check therefore proves that the anchor is real. It does not prove the whole sentence. Limiting each Fact to one claim would close most of that gap. (J)

## Cost

- C took about twice as long as B and wrote about twice the output. It rewrote its ledger 133 times over 50 Sessions. (M)
- A and B differ by less than a second run could change. Each approach ran once. (J)
- The cached re-read figure comes from running each approach as one conversation that grew to between 327,000 and 423,000 tokens. It says little about a build with separate calls. (M for the sizes, J for the reading)
- Estimated from file sizes at four characters per token, a build with separate calls reads the sample once, about 104,000 tokens. A then writes about 27,000 tokens, and B about 20,000. C also re-reads its ledger on every Session, about 190,000 more input tokens at this size, and that cost grows with the number of Pursuits. (J)
- At 50 Sessions, cost does not separate the approaches. Elapsed time does: A's cards and B's groups can run in parallel, and C cannot. (J)

## What the sample could not test

- **Status.** No Session shows the owner setting work aside. Every Status came from the 30-day rule, and every proposed ending is an inference from silence or from a date that had passed. (M)
- **Whether the next Steps are right.** The sample ends on 2026-10-01. Only the owner knows what happened next. (J)
- **Deciding what is not a Pursuit.** One Session in 50 tested it. A month with more small unrelated Sessions would test it harder. (J)
- **Long Sessions in full.** See the scaling figures under [The sample](#the-sample).
- **The Harness mix.** 39 of the 50 Sessions are Codex. Devin, Hermes, ChatGPT, and claude.ai were not sampled.
- **Repeatability.** One run per approach, one model.

## Facts about the Harness stores found on the way

These add to section 2 of the findings file. All are (M).

- Codex rollouts from September are zstd-compressed and not encrypted. Python's `zstandard` package reads them.
- In a Codex rollout, the owner's typed prompts are the `event_msg` records whose item type is `UserMessage`. The `response_item` user messages also include injected instructions and skill text. The assistant's text is in items of type `AgentMessage`, with a `phase` of `commentary` or `final_answer`.
- Codex encrypts the brief it sends to a subagent, inside the parent's `spawn_agent` call. The child rollouts that were examined start as a copy of the parent's messages, so the brief cannot be recovered from the child either. The child's name and its final answers are readable.
- `state_5.sqlite` records a rollout path that ends in `.jsonl` for files that are `.jsonl.zst` on disk.
- A Claude Code subagent transcript has a `.meta.json` file beside it whose `description` field is the name the parent gave it.
- The 50 compact Sessions contain 4 key-like values that the owner pasted into a Harness. None appeared in any approach's output. This bears on the parked question about scrubbing secrets (R1a Q9 in the brief).

## Suggested direction

This is the agent's recommendation. The owner has accepted B's division and nothing else here. (J)

- For the first build over old Sessions, read related Sessions together, as B does, and find a first grouping signal that is better than the folder.
- For daily upkeep, file each new Session against a ledger, as C does, and add a regrouping pass at intervals.
- Keep A's per-Session cards as a cheap layer under either. Do not use cards as the only input to grouping.
- Treat how finely to divide a busy repository as a correction the owner gives once and Atlas remembers. The brief already allows this through merge and split.

## Open points

- Whether a chore found inside a Session is a Pursuit.
- A sample in which the owner parks or drops work, to test Status and proposed endings.
- Whether the next Steps were right, which needs the owner to mark them against what happened after 2026-10-01.
- A second run of each approach, to learn how much the results vary.
