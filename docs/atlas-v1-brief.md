# Atlas v1 brief

This file records what the owner decided in the design interview of 2026-11-05, so that a new session can continue from the repository alone. It holds decisions about **what** Atlas is. Questions about **how** to build it were set aside and are listed under [Parked "how" questions](#parked-how-questions).

- Vocabulary is defined in [GLOSSARY.md](../GLOSSARY.md). Capitalised terms here are glossary terms.
- Three decisions also have their reasoning recorded: [ADR 0001](adr/0001-manager-first-memory-second.md), [ADR 0002](adr/0002-open-to-read-guarded-to-write.md) and [ADR 0003](adr/0003-one-machine-catalog-plugin.md).
- The [week-with-Atlas tutorial](tutorial/a-week-with-atlas.md) is the target the build is checked against. Its open questions were settled in the grilling of 2026-11-07, recorded here as `W Qn` after the tutorial's numbering.
- Facts about the Harnesses, T3 Code and Hermes are in [research/harness-and-hermes-findings.md](research/harness-and-hermes-findings.md).
- A prototype of 2026-11-06 that turned real Sessions into Pursuits, and what the owner decided from it, is in [research/sessions-to-pursuits-prototype.md](research/sessions-to-pursuits-prototype.md).
- A second prototype of 2026-11-06 that tested Daily Briefs against what the owner did next, and what the owner decided from it, is in [research/daily-brief-prototype.md](research/daily-brief-prototype.md).
- A third prototype of 2026-11-06 that ran Hermes to test what Atlas needs from it, and what the owner decided from it, is in [research/hermes-prototype.md](research/hermes-prototype.md).

Each decision carries a reference such as `R3 Q2`: round 3, question 2. The interview ran in this order:

| Reference | Round | Outcome |
|---|---|---|
| R1a | First version of round 1, nine questions | Set aside by the owner as too much "how" |
| R1 | Round 1 rewritten, seven questions | All accepted |
| R2 | Round 2, eight questions | All accepted |
| R3 | Round 3, eight questions | All accepted |
| R4 | Round 4, nine questions | All accepted |
| R5a | First version of round 5, ten questions | Withdrawn when the owner said to ignore the old project |
| R5 | Round 5 rewritten, seven questions | Five accepted, one accepted with a change, one questioned |
| R6 | Round 6, three questions | All accepted |
| FA | Follow-up A, one question plus the summary | Accepted |
| FB | Follow-up B, one question | Accepted |
| W | Grilling on the tutorial's open questions Q1 to Q19, 2026-11-07, one question at a time | See [Settled from the tutorial](#settled-from-the-tutorial) |
| D | Design sign-off grilling on the architecture's owner questions Q1 to Q19 and scenes S1 to S3, 2026-11-08, one question at a time | See [Settled from the architecture](#settled-from-the-architecture) |
| G | Grilling on the four design gaps found while slicing the tickets (issue #1), 2026-11-08, one gap at a time | See [Settled from slicing](#settled-from-slicing) |

## What Atlas is

Atlas is a single-user manager, living in Hermes, that always knows the state of everything its owner has started and pushes them to continue it. It is a manager first and a searchable memory second.

It takes in Evidence from Sources, keeps Pursuits current without the owner's effort, writes a Journal, and sends one Daily Brief a day and one Review a week through a Channel.

## The owner's original request

The owner starts projects and brainstorms ideas, pauses, and forgets them. They asked for a Hermes plugin that accumulates everything they work on, explore and plan with AI agents, across their machines and Harnesses, with new Harnesses easy to add. It must not be another database that just sits there: it must maintain itself and push the owner like a project manager or coach.

Agents must be able to answer these five questions, pointing back to the Sessions, Pursuits and other Evidence:

1. What was I doing on a specific date, including months or years ago?
2. Where did I leave off on a particular project, and how do I resume?
3. Which ideas or experiments have I neglected?
4. Find the conversation where I solved a particular problem.
5. What did I decide, change, or finish last week?

The owner also asked for: upkeep done by an LLM acting as a librarian; a board showing everything they are busy with; access for Hermes, Claude Code, Codex and future always-on agents to search and update the record; and, over time, learning about the owner's personal life (health, family, kids) and using email, calendar and messengers. Which of these are in v1 is settled below.

On 2026-11-07, answering W Q5, the owner restated the original idea in their own words. It is kept verbatim because it names wishes the summary above flattens, such as periodic research on the owner's own prompts. The "(delayed for later)" mark is the owner's.

> It should periodically mine my prompts (maybe weekly or monthly?) and research novel ways to solve those problems because every week there is something new in github and internet.
>
> I start projects and brainstorm ideas with AI agents, pause, and forget them. Atlas accumulates everything I work on, explore and plan with AI agents, so I can see what is active, find past work, and resume anything unfinished, even months later. It spans my machines (delayed for later) and my harnesses: Claude Code, Codex, Devin and Hermes itself first, with more added easily later (other sources like email, etc will be added later).
>
> Automatic upkeep: serve as a librarian using LLM which keeps it organised and current without me. I am lazy and forgetful: whatever depends on me remembering to maintain Atlas will fail. Answers should point back to the relevant sessions, projects, and other evidence.
>
> It learns as much about me as it can: projects, ideas, problems and tasks, including personal ones such as health, family and kids. Hermes, Claude Code, Codex and future always-on agents (dot, grokbot, duet, etc) can all search and update it.
>
> It acts like my project manager. It hands me daily tasks on unfinished projects, sharpens ideas I discussed earlier, suggests new ways to solve problems I complained about, reminds me of what I forgot, researches and evaluates on its own with the tools it is given (email, calendar, WhatsApp, Telegram), and pushes me to be better and more productive every day.

## Decisions

### Purpose and audience

- Atlas is first a manager that always knows the state of everything the owner has started, and only second a searchable memory. The product is the list of Pursuits, each with a Status, where the owner left off and a next Step, kept current without the owner's effort. Search supports this and can lean on tools that already exist. (R1 Q1, ADR 0001)
- Atlas is built for the owner alone as a single-user tool. It is published so others can install it, but it is not a multi-user product. (R1 Q3)

### Pursuits

- Projects, ideas, experiments and problems are one thing, a Pursuit. A Pursuit that changes from an idea into a project keeps one history. (R1 Q2)
- Not everything is a Pursuit. A one-off question is Evidence that can be found later, but a Pursuit needs an unfinished intent the owner might return to. (R2 Q5)
- A Pursuit has a Kind from a short fixed list: idea, experiment, project, problem. (R4 Q1)
- A Pursuit separately has an Area of the owner's life: work, health, family, and others the owner adds. (R4 Q1)
- A Pursuit has exactly four Statuses: active, parked, dropped, done. Neglected is a condition of an active Pursuit that has gone quiet, not a Status. (R3 Q1)
- Pursuits are flat: no Pursuit sits inside another. Two can be merged and one can be split when Atlas guessed wrong. (R4 Q2)
- One piece of Evidence can belong to several Pursuits or to none. (R4 Q2)

### Sources and Evidence

- A Source is any place Atlas learns from. A Channel is where Atlas talks to the owner. They are different words, and one service can be both, with separate rules for each role. (R2 Q1)
- A Harness is one kind of Source. (R2 Q1)
- Everything Atlas takes in from a Source is one Evidence item. Every answer and every Pursuit points back to Evidence. (R2 Q2)
- Sources are pluggable from day one, because there may eventually be hundreds: email, messengers, Slack, Teams, posts, comments and posts saved from other people. (owner's addition after R1, R2 Q3)
- v1 ships Harness Sources only. Email is the first Source added afterwards. (R2 Q3)
- The v1 Harnesses are Claude Code, Codex, Devin, Hermes, ChatGPT and claude.ai. R2 Q3 said "the four"; web chats were added later. (R5 Q7 as changed by the owner, R6 Q2, FA summary)
- ChatGPT and claude.ai are Harnesses like the others, and each conversation in them is a Session. (R6 Q2)
- Web chat Sessions may reach Atlas days later than Sessions from Harnesses on the owner's machines. The Journal is corrected when they arrive. (R6 Q3)
- Atlas never takes in other people's messages from a web-chat export, such as group chats. After every conversation in an export the owner hands it has been safely kept, Atlas deletes the export file and says so where it reports the import. An export that fails in whole or in part is kept and the failure reported. (D Q5)
- T3 Code is an Interface, not a Harness. A Session run through it belongs to the underlying Harness and is counted once. Atlas notes which Interface a Session came through. (R5 Q2)
- A conversation between an agent and its subagents is part of the Session that started it, not a Session of its own. (FB)
- The owner's own Sessions are kept in full. (R2 Q4)
- The verbatim copy of each Session keeps any secrets in it. Everything Atlas derives or shows masks the secrets it detects: the readable transcript, Notes, Resume Notes, the Journal, answers, what agents read and what models are sent. Detection is best effort. Only the owner opening the verbatim copy sees a secret again, and Forget removes it. (D Q4)
- From high-volume Sources such as email, chats and feeds, Atlas keeps only what it judges relevant to the owner or to a Pursuit, such as a commitment, a deadline, an idea or something the owner saved. It ignores the rest. (R2 Q4)
- Accepted cost of the previous line: "what was I doing on date X" is complete for AI work and partial for everything else. (R2 Q4)
- When the owner tells Atlas something on purpose, that is a Note, and a Note is Evidence like any other. (R4 Q3)
- The owner's conversations with Atlas itself are Evidence, as Notes, but they are about the work, not part of it. They are not Active Time, they never count as working on a Pursuit (only an explicit "continue" resets quiet days), and they never create a Pursuit. Hermes Sessions that are not with Atlas are ordinary Sessions. (W Q14)

### How Atlas files, and who decides

- Atlas creates Pursuits, attaches Evidence and updates Statuses on its own, without asking. The owner corrects it afterwards and Atlas remembers the correction. (R2 Q6)
- A parked Pursuit becomes active again by itself when a Session advances it. Its quiet days count from that Session, and the change is shown like any other filing. A done or dropped Pursuit is not reopened by Atlas: the new Session is filed to it and Atlas proposes reopening, marked on the Board and asked in the weekly Review. If the new work is a new intent, Atlas creates a new Pursuit, which the owner can merge. Reading a Resume Note, asking Atlas about a Pursuit or finding a Session in it does not count as working on it. (W Q18)
- The owner sees what Atlas filed on its own in three places, never in the Daily Brief: on the Board at once, where a card Atlas created or changed is marked new or changed until the owner has seen it; in the weekly Review, under what Atlas did on its own, correctable by reply; and in the Journal. (W Q2)
- A card counts as seen once it has been on screen on the Board for about two seconds; no click is needed. The weekly Review lists all of Atlas's own filing whatever the marks say, so a card skimmed past is not missed. (D Q17)
- The exception is endings. Only the owner ends a Pursuit as done or dropped, directly or by telling an agent to; Atlas proposes. (R3 Q1, D Q11)
- Atlas proposes an ending where its evidence appears. When yesterday's Step looks like it finished the Pursuit, the proposal rides in that Step's follow-up line in the Daily Brief, so the brief keeps its three things. Every other proposed ending goes in the weekly Review. A proposed ending is also marked on the Board card until the owner answers, and Atlas does not repeat it in later Daily Briefs. (W Q12)
- A Pursuit that ends, or is parked, leaves Focus, because Focus holds only active Pursuits. (GLOSSARY, Focus; W Q12)
- When a Pursuit becomes Neglected, Atlas asks the owner to choose: continue (it stays active), park (it resurfaces rarely), or drop (it is never pushed again but stays findable). Atlas does not nag endlessly. (R2 Q8)
- Atlas raises parked Pursuits again in the weekly Review, at most one per Review: the one quiet longest, counted from the later of its last Session and the last time Atlas raised it, provided that is at least 90 days. It shows where the owner left off and asks "continue, keep parked, or drop?". "Keep parked" and silence both restart the 90 days, so silence never escalates. "Continue" makes it active, outside Focus until the owner adds it, with quiet days counting from the answer. (W Q6)
- An unanswered Neglected question comes back in the next Daily Brief. If the second ask also goes unanswered, Atlas parks the Pursuit itself and says so on the Board and in the weekly Review, where a reply of "continue" undoes it. The decision slot then moves to the next-quietest Neglected Pursuit. This is how Atlas "does not nag endlessly". (W Q11)
- An active Pursuit outside Focus becomes Neglected after 14 quiet days. The Daily Brief offers the quietest one first, one a day, and leaves the slot empty when none qualifies. Quiet days count from the last Session on the Pursuit or the owner's last "continue", whichever is later, so on day one Atlas can offer Pursuits that were already quiet 14 to 30 days. A Pursuit in Focus is never Neglected, because skipped Steps already show that it has stalled. (owner, after the Daily Brief prototype)
- If Atlas sees in the owner's Sessions that a suggested Step was done, it marks the Step done without waiting for a reply. (R4 Q7)
- Atlas always shows whether a statement is a Fact, read from Evidence, or an Inference, concluded by a model. This includes the Daily Brief. (R5 Q5)
- In a Telegram message, each item shows its Fact's source once, as `[F Source, time]`, on its first Fact read from a single Session or Note. An item is a Step, a Pursuit in an answer, one Journal line, one Review line or one Profile change. Every other Fact shows a bare `[F]`: a counted figure; a line whose words already name its source and day; a Fact from Atlas's own record, such as where the work lives, a Status change or filing; a later Fact in an item that already cited; and every Fact in text meant to leave Atlas, such as a paste-in prompt or a status to forward. The time reads `Sat 22:10` within the last seven days, a bare `22:10` in a message about one day (`[F Claude Code 22:10]`), and `30 Aug` when older, with the year when it is not this year. The record keeps every citation in full. (G1)
- A figure Atlas counts from Facts by a fixed rule, such as Active Time per day, Area or Pursuit, quiet days, or a count of Steps or Sessions, is a Fact. An Inference is anything that needs judgment. Per-Pursuit figures stay Facts even though they rest on filing, because filing is shown and correctable on the Board. (D Q9)

### Daily Brief, Steps and Focus

- The Daily Brief contains exactly three things: at most three suggested Steps, each with one line on where the owner left off and a way to resume; a follow-up on yesterday's Steps; and one Neglected Pursuit that needs a decision. (R2 Q7)
- A Step is one concrete action on a Pursuit, small enough for one sitting, that ends as done or skipped. v1 has no subtasks, due dates or priorities. (R3 Q2)
- A Step the owner neither does nor answers stays open as the Pursuit's next Step, and the follow-up says there is no sign of it. It ends as skipped by silence once it has been offered twice without being done, or when Atlas replaces it because the Pursuit moved on. A skip by silence counts toward the three skipped Steps that lead Atlas to propose removing a Pursuit from Focus. If late Evidence, such as a web-chat export, shows the Step was done, Atlas changes it to done and corrects the Journal. When the Pursuit was last worked in a Harness whose Sessions arrive late, the follow-up says Atlas cannot see it yet. The owner's word settles a Step at once. (W Q7)
- "Remind me <day>" holds a Step: it stays open and is not offered before that day, and holding it is neither a skip nor an offer. On that day it comes first among the brief's Steps, which counts as its first offer. A day that falls in a Pause moves to the first brief after it. A hold is not a due date: missing the day has no consequence beyond the rule for an ignored Step. The same words hold a Neglected question until that day. While a Step is held, Atlas offers a different Step on that Pursuit only if the Pursuit has moved on. (W Q8)
- The owner answers the Daily Brief in plain words, for example "done", "skip", "park this" or "remind me Friday". That reply is the main way the owner corrects Atlas. (R3 Q3)
- In a chat Atlas shares with Hermes's own agent, a message is Atlas's when it replies to a brief, starts with "atlas", or comes within 10 minutes of the owner's last exchange with Atlas. Every other message goes to Hermes's agent, which may still read and update Atlas through its tools. The owner may instead give Atlas a chat of its own in settings, where every message is Atlas's. That chat is optional, so a stranger's setup does not need it. (D Q1, D S2)
- The Daily Brief and the Review go by default to the Hermes home channel of the platform chosen at setup, Telegram for this owner. The owner can point them at another chat or topic in settings, for example a new Telegram topic when the home channel or main topic is already used for something else. A chat or topic set this way is Atlas's own chat from D Q1, where every message is Atlas's. (D Q16, closing the Channel half of R1a Q8)
- The owner sets when the Daily Brief and the Review arrive. The defaults are 07:30 every day for the Daily Brief and 18:00 on Sunday for the Review. Atlas may propose a better time in the Review when the Profile shows a different pattern, and never moves the time on its own. If Atlas was down at that time, the message arrives when Atlas is back the same day; a missed day is skipped, not sent late the next day. (W Q4)
- The Review proposes a new Daily Brief time when, on at least 10 of the last 14 days that had Active Time and no Pause, the owner's first Active Time began 60 minutes or more before or after the brief time. It proposes the median of those first starts, rounded down to the quarter hour, marked `[F]` because it is counted. "Move it" changes the time; "keep it" or silence means no new proposal for four weeks. The rule counts Active Time rather than reading the Profile's Inference, so W Q4's "when the Profile shows a different pattern" is met by a fixed rule. Atlas proposes no new time for the Review itself in v1. (G4)
- When Atlas cannot tell whether a Daily Brief or Review reached the owner, it sends it once more the same day, risking a duplicate rather than a missed day. It never sends a third copy, and a reply to either copy counts. (D Q13)
- Atlas starts a conversation with the owner in exactly two ways: the Daily Brief, and a weekly Review. Atlas never messages the owner unprompted otherwise. (W Q5)
- Atlas answers whenever the owner writes to it, including a short acknowledgement of each reply to the Daily Brief. An answer carries only what was asked. (W Q5)
- When an answer takes more than about 25 seconds, Atlas replies at once that it is looking and sends the answer as a second message when ready. That second message is prompted, so W Q5 holds. If the answer fails, Atlas says so rather than going silent. (D Q8)
- The Review arrives once a week, on Sunday. In v1 it carries what the owner decided, changed and finished that week, and what Atlas did or proposes on its own that the three-slot Daily Brief has no room for. (W Q5)
- Focus is a small set of about five Pursuits from which the Daily Brief draws its Steps. Atlas proposes changes to Focus and the owner decides. Other active Pursuits stay on the Board without being pushed. (R3 Q4)
- Until the owner first picks a Focus, the Daily Brief draws its Steps from the most recently active Pursuits instead, at most three and one per Pursuit, under a heading that says no Focus is picked yet. This stops for good once the owner has picked any Focus. It is a stand-in, not a Focus proposal, so W Q10 holds. (D Q10)
- Atlas proposes a Focus change on the Board card at once, and asks in the weekly Review, with a one-line reason the owner answers by reply. A Focus change is never proposed in the Daily Brief. Atlas proposes adding a Pursuit when it is new and worth pushing or when a Focus slot frees up, and proposes removing a Pursuit after three skipped Steps in a row on it. (W Q10)
- Atlas chooses each Step as the smallest action that clears a blocker or settles an unknown, at most one per Pursuit, and may send fewer than three. Each Step carries one line on where the owner left off and one line on how to resume. The way to resume includes a prompt the owner can paste into any Harness, offered from the Step rather than inside the message. (Daily Brief prototype)
- The owner asks for that prompt by replying with the Step's number and "prompt", for example "1 prompt", or "prompt" after Atlas answers where they left off; it arrives as its own message. The Daily Brief carries no buttons. Each Board card has a Copy prompt button. (D Q2, D S1)

### Resuming

- Every Pursuit carries a Resume Note: where the owner left off, what was decided, open questions, the next Step and the Sessions to reopen. (R3 Q5)
- The Resume Note is complete enough to continue in a different Harness than the one the owner stopped in. (R3 Q5)
- The Resume Note says where the work physically is: which machine, folder or repository. (R4 Q5) R4 Q5 also said Atlas shows one picture across all the owner's machines; for v1 that picture covers the one machine Atlas is installed on. (W Q19, ADR 0003)

### Journal and working time

- Atlas writes one Journal entry per day covering what the owner worked on, decided and finished, citing Evidence. (R3 Q7)
- Decisions live inside Journal entries. They are not tracked as separate things. (R3 Q7)
- The Journal records how long the owner actively worked each day, per Area and per Pursuit. The owner's monthly timesheet can come from it. (R6 Q1)
- Accepted limit of the previous line: Atlas only knows time spent with AI agents, not meetings or work done by hand. (R6 Q1)
- Active Time is time the owner was engaged with at least one Session. A stretch runs while the owner's own messages are less than 15 minutes apart and ends 15 minutes after the last one, or sooner at the agent's last reply. An agent working alone does not count. Parallel Sessions count once, and a minute spent on two Pursuits at once is split evenly between them, so Pursuits and Areas add up to the day's total. The 15 minutes is a setting the owner can change. (W Q13)

### Profile

- Atlas keeps a Profile: what it has learned about the owner that belongs to no single Pursuit, such as when they work, what they tend to abandon and why, and recurring complaints. (R3 Q6)
- Everything in the Profile is readable and correctable by the owner. There is no hidden model of the owner. (R3 Q6)
- In v1 the Profile is limited to what makes the Daily Brief better. (R3 Q6)
- Atlas first writes the Profile at the end of the backfill, from the owner's past Sessions, and then updates it once a day, when a Journal day closes. There is no separate weekly pass. (G3)
- A Profile entry Atlas adds on its own is something Atlas did on its own: the weekly Review lists, under what Atlas did on its own, each entry it added that week that still stands as Atlas wrote it. An entry the owner has already corrected is not listed. (G3)

### Board

- The Board shows Pursuits in columns by Status, with Focus marked. Each card shows name, Kind, last activity, where the owner left off and the next Step. (R3 Q8)
- On the Board the owner can change Status and Focus, and nothing else. It is a view, not a place for data entry. (R3 Q8)

### What Atlas may do unasked

- Unasked, Atlas may read, research, update its own records and message only the owner. Anything that reaches other people or changes things outside Atlas needs the owner's approval for each action. (R1 Q5)
- In v1 Atlas does not act on the outside world at all, even with approval. It only reads, records and messages the owner. The approval rule above applies when such actions are added later. (R5 Q7)
- These limits bind Atlas, not Hermes's own agent. A request that goes to Hermes's agent, such as an un-addressed "send Dana the status", is Hermes's to refuse or carry out. (D Q1, D S2)

### Agents in a Harness

- An agent in a Harness may read everything and add Notes on its own initiative. (R4 Q4)
- An agent may change a Pursuit, for example mark a Step done, park it or edit the Resume Note, only when the owner tells it to in that Session. (R4 Q4)
- Atlas applies an agent's change only when it can confirm the owner's words in the calling Session. Where it cannot, as in Codex and Devin until their Sessions can be identified, the change is refused and kept as a Note quoting the owner's words, and Atlas's next answer to the owner offers "apply", which makes the change through Atlas. Confirming this for Codex is a condition of releasing v1; Devin may stay Notes-only in v1. (D Q12, D S3)
- Atlas confirms a Codex agent's Session from the Session id that Codex itself adds to each call, never from what the agent says, and reads the owner's words from that Session's file. With no id, or no matching Session, the update is refused and kept as a Note as before. The release condition stands: the interactive Codex and Codex through T3 Code must be checked before v1 ships. Devin sends no Session id, so it stays Notes-only in v1. (G2, ADR 0010)
- An agent may end a Pursuit as done or dropped when the owner's own words in that Session name the ending, such as "done", "finished", "close it" or "drop it". A Step being done is not enough. This is the owner ending it, carried by the agent, so R3 Q1 holds. The History quotes the words, the Board marks the card changed, and "reopen" undoes it. (D Q11)
- In v1 every agent the owner runs may read everything in Atlas, across all Areas. This is to be revisited when health and family Evidence starts arriving from email and messengers. (R5 Q6)
- In v1 Session text from every Area, with detected secrets masked, may go to whatever model the owner's Hermes is configured with, cloud models included. Atlas says at setup which model it will use. There is no rule by Area, because Atlas learns a Session's Area by having a model read it. This is revisited with R5 Q6. (D Q15)

### The owner's controls

- The owner can Pause Atlas, for a holiday or illness. During a Pause no Daily Brief arrives and nothing becomes Neglected. (R4 Q8)
- Every Pause has an end. A Pause the owner gives no end lasts one week, which Atlas says when it confirms; the owner can extend it or end it early by writing to Atlas. (W Q17)
- A Pause silences Atlas, not the owner's work. Atlas keeps collecting and filing, sends no Daily Brief and no Review, and working in a Harness does not end the Pause. (W Q17)
- Paused days count on no clock: not the 14 quiet days for Neglected, not the 90 days for raising a parked Pursuit, and not the two offers of a Step. (W Q17)
- The first Daily Brief after a Pause follows up the last brief before it, with held Steps whose day fell in the Pause first. The first Review after a Pause covers everything since the previous Review. (W Q17)
- The owner can tell Atlas to Forget something permanently. That removes the Evidence and everything derived from it. This differs from Dropped, which stays findable. (R4 Q9)
- Before it Forgets, Atlas asks once, listing exactly what will be removed: the Evidence and everything derived from it, such as Resume Note lines, Journal lines and Profile entries. Only the reply "forget" goes ahead; any other reply, or none, cancels. Afterwards the record keeps only that something was forgotten, and when, never the content. (W Q16)
- That list includes any Pursuit whose only Evidence is being forgotten, which goes with it. The confirmation also names, in one line, the copies Atlas cannot remove, such as the Session in the Harness's own store (with its path) and chat history about it, and tells the owner to delete them there. Forget never reaches outside Atlas. (D Q3)
- A forgotten Session is never collected again, even while the Harness still holds it. To make that possible, the record keeps a one-way fingerprint of the Session's identity that reveals neither its id nor its content. The same text in a different Session is new Evidence. There is no "unforget". A conversation the owner deletes at ChatGPT or claude.ai, or that a Harness cleans up, stays in Atlas until the owner tells Atlas to Forget it. (D Q14, R2 Q4)

### Where Atlas lives, and its record

- Atlas lives in Hermes and speaks to the owner through it, even though the owner's real work stays in Claude, Codex and Devin. The owner had not used Hermes when this was decided. (R5 Q3)
- Atlas's home is Hermes on the owner's always-on Ubuntu PC. The owner's Windows laptop is only an interface to it, added later. (Hermes prototype)
- v1 is an open-source Hermes plugin that anyone can install on their main or only machine, where Hermes already runs. It is submitted to the [Hermes plugin catalog](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission). Nothing in it may assume the author's own machines, folders or Harnesses. (W Q19, ADR 0003)
- v1 collects only from that one machine. Sessions on the owner's other machines, such as the Windows laptop, are not collected, and Atlas says so when an answer could be missing them. Collecting from other machines is deferred. (W Q19, ADR 0003)
- Atlas finds by itself the Harnesses installed on that machine, Hermes's own Sessions included. A Harness it cannot find, such as ChatGPT or claude.ai in the browser, the owner adds in Atlas's settings. (W Q19)
- The owner can open and read everything Atlas knows without Hermes running. (R5 Q4)
- Anything can read Atlas's record directly, with neither Hermes nor Atlas running. Every change goes through Atlas, so that its rules hold. (FA Q1, ADR 0002)
- When a record file is edited by hand, Atlas puts the file back as it was and keeps the edited text as a Note, so nothing typed is lost. It tells the owner in the weekly Review, or in its next answer if they are talking, and a reply of "apply" makes the change through Atlas with its rules. Atlas cannot tell who made a hand edit, so it treats every one this way. (W Q15)

### Starting state

- On day one Atlas builds Pursuits from the Sessions the owner already has. Pursuits touched in the last month start active, everything older starts parked, and the owner picks the first Focus. (R4 Q6)
- Atlas builds this history from the raw Sessions on the owner's machines, not from the old agent-atlas project. (R4 Q6, owner's instruction before R5)

### What Atlas may see

- Atlas sees everything from the owner's AI Sessions by default, minus exclusions the owner names. The owner has named none so far. (R1 Q6)

## What v1 contains

Exactly three things (R1 Q4):

1. It accumulates what the owner does with AI agents across the Harnesses on the machine where Hermes runs. R1 Q4 said "and machines"; v1 covers one machine. (W Q19)
2. It keeps the Pursuits current and answers the five questions with Evidence.
3. It sends the owner one Daily Brief a day, and one Review a week. (R1 Q4, with the Review added by W Q5)

Personal topics the owner discusses with an agent are captured from day one, because they arrive as Sessions. (R1 Q4)

## What v1 leaves out

Each item is deferred, not rejected. (R1 Q4, R5 Q7)

- **Sources beyond Harnesses**: email, messengers, calendar, saved posts. Email comes first afterwards.
- **Autonomous research** and sharpening of the owner's ideas. When added, it arrives as sections of the weekly Review: mining the owner's prompts for problems and finding new ways to solve them on GitHub and the web; re-running old prompts with the latest models; proposing reusable prompts as skills or AGENTS.md instructions; and new insights from old Sessions. Every finding is filed against a Pursuit or offered as a Step, so research serves the manager (ADR 0001). (W Q5)
- **Coaching** on personal growth.
- **Acting on the outside world**, even with approval.
- **Structure inside a Pursuit**: nested Pursuits, due dates, priorities, separately tracked decisions.
- **Other people**: sharing, teams, multiple owners.
- **Other machines**: collecting Sessions from any machine but the one Atlas is installed on. (W Q19, ADR 0003)

Web chats were on this list as first proposed. The owner removed them: ChatGPT and claude.ai are in v1. (R5 Q7)

## Success measure

After a month, most mornings the owner acts on at least one Step that Atlas suggested, and can pick up any Pursuit in under a minute. Both are about the owner's behaviour changing; a full database that is never opened would be a failure. (R1 Q7)

## Settled from the tutorial

The [tutorial](tutorial/a-week-with-atlas.md) listed 19 places where this brief was silent, ambiguous or in tension. Each was sorted into one of three outcomes: decided by the owner and recorded above under its `W Qn` reference, closed because this brief already answered it, or handed to /architect as a "how" question.

| Q | Question | Outcome |
|---|---|---|
| Q1 | How Fact and Inference marks and citations look in a message | Handed to /architect. The rule is set (R5 Q5): every statement is marked, and a Fact names its Evidence. The notation depends on the Channel. |
| Q2 | How the owner learns what Atlas filed on its own | Decided: Board marks at once, the Review weekly, and the Journal; not the Daily Brief. (W Q2) |
| Q3 | How the owner adds an Area | Closed. The owner adds Areas (R4 Q1), in plain words (R3 Q3), because the Board changes only Status and Focus (R3 Q8). |
| Q4 | When the Daily Brief and the Review arrive | Decided: owner settings, defaulting to 07:30 daily and Sunday 18:00; Atlas may propose a change; a late message the same day beats none. (W Q4) |
| Q5 | Whether Atlas replies to replies, given one Daily Brief a day | Decided: two unprompted messages, the Daily Brief and a weekly Review; Atlas always answers when written to; research comes after v1, in the Review. (W Q5) |
| Q6 | How and where a parked Pursuit is raised again rarely | Decided: in the weekly Review, one per Review, each at most every 90 days; silence changes nothing. (W Q6) |
| Q7 | What happens to a Step the owner neither does nor answers | Decided: it stays open, ends as skipped by silence after two offers or when replaced, turns to done if late Evidence shows it, and the follow-up says when Atlas cannot see yet. (W Q7) |
| Q8 | What "remind me Friday" does to a Step | Decided: a hold, not a due date; the Step is offered first on that day, which counts as its first offer; the same applies to a Neglected question. (W Q8) |
| Q9 | Whether Atlas changes a Kind on its own | Closed. Atlas files on its own and the owner corrects (R2 Q6); only endings are the owner's (R3 Q1), and the glossary says a Kind changes as the Pursuit matures. |
| Q10 | Where Atlas proposes a Focus change | Decided: marked on the Board at once and asked in the Review; never in the Daily Brief; removal proposed after three skipped Steps in a row. (W Q10) |
| Q11 | Whether an unanswered Neglected question returns, and when Atlas stops | Decided: asked twice on consecutive briefs, then Atlas parks the Pursuit by silence and says so; "continue" undoes it. (W Q11) |
| Q12 | Where Atlas proposes an ending, and whether an ended or parked Pursuit leaves Focus | Decided: in the follow-up when yesterday's Step finished it, otherwise in the Review, and marked on the Board; not repeated. Leaving Focus closed by the glossary: Focus holds only active Pursuits. (W Q12) |
| Q13 | What counts as actively worked time | Decided: engaged time, counted once across parallel Sessions, split evenly between Pursuits worked at once; 15-minute gap, owner-adjustable. (W Q13) |
| Q14 | Whether chats with Atlas are Evidence and count as working time | Decided: they are Evidence, as Notes, but not Active Time, not work on a Pursuit, and never a new Pursuit. (W Q14) |
| Q15 | What happens when a record file is edited by hand | Decided: Atlas restores its version, keeps the edit as a Note, and asks; "apply" makes the change through Atlas. (W Q15) |
| Q16 | Whether Forget asks for confirmation first | Decided: it asks once, listing what will go; only "forget" proceeds; the record keeps only that something was forgotten, and when. (W Q16) |
| Q17 | How a Pause ends, whether paused days count, and what the first brief after it follows up | Decided: every Pause has an end, one week by default; Atlas keeps filing; paused days count on no clock; the first brief follows up the last one before the Pause. (W Q17) |
| Q18 | Whether a Pursuit becomes active again when worked on | Decided: parked reopens by itself when a Session advances it; done or dropped are only proposed for reopening, in the Review and on the Board. (W Q18) |
| Q19 | Whether the Windows laptop's Sessions are collected | Decided: one machine only in v1. (W Q19, ADR 0003) |

### Handed to /architect

- **Fact and Inference notation (W Q1).** How marks and citations look in each Channel's messages.
- **Finding installed Harnesses (W Q19).** How Atlas detects the Harnesses on the machine, and what Atlas's settings look like for adding the ones it cannot find.
- **Telling Atlas's own conversations apart (W Q14).** How Atlas separates the owner's chats with Atlas from other Hermes Sessions.
- **Detecting hand edits (W Q15).** How Atlas notices that a record file changed outside it, and restores it.
- **The "seen" mark on the Board (W Q2).** How Atlas knows the owner has seen a new or changed card.
- **Measuring Active Time (W Q13).** How engagement stretches are computed from each Harness's timestamps.
- **Missed and late messages (W Q4, W Q17).** How the Daily Brief and Review are scheduled in Hermes so that a missed time sends the same day and a Pause silences them. The Hermes prototype's recommendation on catch-up is an input.

## Settled from the architecture

The [v1 architecture draft](architecture/atlas-v1.md), section 6, asked the owner 19 questions (Q1 to Q19 there) and named three tutorial scenes it serves differently (S1 to S3 below). On 2026-11-08 each was decided by the owner and recorded above under its `D` reference, closed because this brief already answered it, or handed back to the architect as a "how" question.

- **S1.** The Resume prompt button (scenes 8, 35, 38).
- **S2.** Un-addressed chat lines (scenes 5, 24).
- **S3.** Agent updates from Codex or Devin.

| Item | Question | Outcome | What the architect must change |
|---|---|---|---|
| Q1, S2 | Which chat messages are Atlas's | Decided, deviation accepted: a reply to a brief, a message starting with "atlas", or one within 10 minutes of an exchange with Atlas; everything else is Hermes's. A dedicated Atlas chat is an optional setting. (D Q1) | Add the optional dedicated chat to Settings and to `channel.claim`; setup must not require it. |
| Q12, S3 | Agent updates from Codex and Devin before their Session binding is verified | Decided, deviation accepted: refused and kept as a Note quoting the owner; Atlas's next answer offers "apply". Verifying Codex's binding is a v1 release condition; Devin may stay Notes-only. (D Q12) | Make a refused update an applyable Note, offered in the next answer like a hand edit. Verify the Codex caller binding before release. |
| Q2, S1 | The Resume prompt without a Telegram button | Decided, deviation accepted: reply "1 prompt" (or "prompt" after a resume answer); a Copy prompt button on the Board card. (D Q2) | None beyond the draft: keep the hint line in the brief, add "prompt" after a resume answer. |
| Q15 | Which models may read which Areas | Decided: every Area may go to the configured model, cloud included, secrets masked; revisit with R5 Q6. (D Q15) | Setup names the model Atlas will use. |
| Q11 | Whether an agent may end a Pursuit when told | Decided: yes, when the owner's quoted words name the ending; a Step done is not enough. (D Q11) | Add the ending-words check to `find_instruction` for `SetStatus(done/dropped)` from an agent. |
| Q10 | The first Daily Brief before the owner picks Focus | Decided: Steps come from the most recently active Pursuits under a "no Focus yet" heading, until Focus is first picked. (D Q10) | Add the never-picked branch to `compose_daily`, read from History. |
| Q9 | The mark on Active Time | Decided: `[F]`; a figure counted from Facts by a fixed rule is a Fact. GLOSSARY Fact and Inference sharpened. (D Q9) | Mark Active Time and other counts `[F]`; give the record notation a citation form for a count. |
| Q3 | What Forget cannot reach, and Pursuits resting only on forgotten Evidence | Decided: the confirmation names the copies outside Atlas and says to delete them there; a Pursuit with no other Evidence is removed and listed. (D Q3) | `plan_forget` reports outside copies it knows of (source path per adapter) and lists orphaned Pursuits. |
| Q14a | A one-way hash so a forgotten Session is not collected again | Decided: yes; no "unforget". (D Q14) | None beyond the draft (ADR 0008's consequence). |
| Q13 | A send that fails after the messenger may have accepted it | Decided: at least once; one same-day retry, never a third copy, replies to either count. (D Q13) | Make `ARRIVAL_UNKNOWN` retryable once on a later tick the same day; record both message ids; reply-to lookup accepts either. |
| Q16 | The owner's Channel | Decided: the Hermes home channel by default (Telegram here); the chat or topic is configurable, and a configured one is Atlas's own chat. (D Q16) | One setting for Atlas's chat or topic, used both by `hermes send` targeting and by `channel.claim`; setup offers it. |
| Q17 | When a Board card counts as seen | Decided: about two seconds on screen; the Review is the backstop. (D Q17) | None beyond the draft. |
| Q8 | Answers that need more than 25 s | Decided: a holding reply ("Looking, I'll send it here."), then the answer as its own message; a failure is reported. (D Q8) | Word the holding reply as decided; report a failed late answer. |
| Q5 | Export zips after import | Decided: deleted after a fully successful import, and said so; kept on any failure. (D Q5) | Delete only after the import commit; report deletion and failures on the Board and in the Review. |
| Q4 | Masking secrets in transcripts | Decided: the raw copy stays verbatim (R2 Q4); every derived or shown text masks detected secrets. (D Q4) | Run `privacy.scrub` when rendering every derived text and agent read, not only before model input. |
| Q6 | ChatGPT messages without a timestamp | Handed back. The rule is set: Active Time is engaged time (W Q13). Estimating undated messages is a measurement method, which W Q13 already handed to the architect. | Choose and document the method in `activetime`. |
| Q7 | Whether a replaced Step counts toward the three skips | Closed. W Q7: a Step "ends as skipped by silence … when Atlas replaces it because the Pursuit moved on", and "a skip by silence counts toward the three skipped Steps". | None. |
| Q14b | A later web-chat export that lacks a conversation the owner deleted there | Closed. The owner's Sessions are kept in full (R2 Q4), and Forget happens only at the owner's request (GLOSSARY, Forget). Atlas keeps the conversation. | None. |
| Q18 | A brief when Atlas comes back at 23:40 | Closed. W Q4: the message "arrives when Atlas is back the same day". It goes out. | None. |
| Q19 | Whether the brief skips a Step offered yesterday with no sign of progress | Closed. W Q7: the Step "stays open as the Pursuit's next Step" and is offered again until it has been offered twice; the tutorial's Tuesday brief shows it. | None. |

## Settled from slicing

Slicing the architecture into tickets found four questions the spec did not settle (issue 1 of the source repository, "Design gaps found while slicing"). On 2026-11-08 the owner settled each, one at a time, recorded above under its `G` reference.

| Gap | Question | Outcome | Blocked |
|---|---|---|---|
| G1 | When a Telegram Fact shows its citation | Decided: once per item, on its first Fact read from one Session or Note; bare `[F]` for counts, self-naming lines, record Facts, later Facts and text meant to leave Atlas; time as `Sat 22:10`, `22:10` or `30 Aug`. The tutorial's Monday greenhouse line now follows the time rule. (G1) | #3 |
| G2 | How Atlas binds a Codex agent's `atlas_update` to its Session | Decided: from the `_meta.threadId` Codex adds to each tool call, matched to the rollout file; refused Note when absent; release condition kept; Devin Notes-only. Verified live with `codex exec`; the TUI and T3 Code paths are checked before release. Nothing the owner sees changes. (G2, ADR 0010) | #14 |
| G3 | When the Profile is first written | Decided: at the end of the backfill, then once a day as each Journal day closes; the weekly pass goes. New Atlas entries that still stand are listed in the Review under what Atlas did on its own. The tutorial is unchanged. (G3) | #21 (week mode) |
| G4 | How far the pattern must differ before the Review proposes a new time | Decided: 10 of the last 14 unpaused days with Active Time start 60 minutes or more away from the brief time; propose the median start, rounded down to the quarter hour; "keep it" or silence waits four weeks; Daily Brief only. The tutorial is unchanged: its first Active Time matches 07:30. (G4) | #20 (TIME PROPOSAL) |

## Parked "how" questions

The owner asked for the interview to stay on "what". Nothing in this section is decided.

### Parked from the first version of round 1

Each carries the recommendation made at the time. **The owner has not accepted any of these recommendations.**

- **Hub and collectors (R1a Q3).** Where the record lives when the owner works on several machines. *Recommendation, not accepted:* one always-on Hub, a Hermes install with the gateway running, that owns the record, runs the upkeep and sends the pushes; plus a small Collector on each machine, with no Hermes dependency, that ships Sessions to the Hub over Tailscale. The alternative named was every machine holding a full copy, synced as files. Which machine would be the Hub was asked and not answered. The Hermes prototype later settled where Atlas runs. See [Where Atlas lives, and its record](#where-atlas-lives-and-its-record). The rest of this recommendation is still open. Since W Q19, v1 collects from one machine only, so Collectors on other machines are deferred with the rest of multi-machine collection.
- **Passive capture (R1a Q4).** How Sessions get from a Harness into Atlas. *Recommendation, not accepted:* Collectors read each Harness's transcript files from disk with no cooperation from the agent; MCP is for searching and for deliberate Notes, not the primary capture path.
- **Raw transcripts (R1a Q5).** What exactly is stored. *Recommendation, not accepted:* copy every raw transcript verbatim as immutable Evidence, stored compressed, with everything a model derives regenerable from it. Note that the owner separately accepted that their Sessions are kept in full (R2 Q4); immutability, compression and regenerating derived material were not decided.
- **Push channel (R1a Q8).** Which Channel carries the Daily Brief and where the Board appears. *Recommendation, not accepted:* a Telegram bot through the Hermes gateway for the Daily Brief, and the Board as a tab in the Hermes dashboard, provided Telegram is the messenger the owner opens every day. The owner was asked which messenger they open daily and did not say. Settled by D Q16: Telegram, through the Hermes home channel, with the chat or topic configurable.
- **Privacy and model use (R1a Q9).** Where the record is held and which models may read it. *Recommendation, not accepted:* the record lives only on the owner's Hub; the upkeep may use cloud models, with secrets scrubbed before any call; each Collector has an exclusion list of directories and repositories that are never shipped. The owner was asked whether any employer or client work must never leave its machine and did not name any.

### Recommended after the Hermes prototype

Each comes from [research/hermes-prototype.md](research/hermes-prototype.md). **The owner has not accepted any of these recommendations.**

- **Collecting Hermes Sessions.** *Recommendation, not accepted:* read Hermes's own `state.db`, as Atlas reads any other Harness, and do not rely on Hermes's session hooks. `on_session_end` fires after every turn, and `on_session_finalize` did not fire for cron or Telegram sessions.
- **Missed runs and Pause.** *Recommendation, not accepted:* set catch-up on each Atlas job on purpose, and make Pause pause the Hermes job itself. By default Hermes runs a missed job once when its gateway comes back.
- **Running upkeep.** *Recommendation, not accepted:* run upkeep as a plugin CLI command that calls `ctx.llm`. It needs no agent session and leaves the memory-provider slot free.

### Parked later in the interview

- How Sessions are collected. (FA summary)
- What access ChatGPT and claude.ai allow for taking conversations out. This was not checked and needs research before building. (R6 Q3, FA summary)
- Which Channel carries the Daily Brief. (FA summary)
- What form the record takes. (FA summary)
- Whether the storage layer is abstract in code. The owner raised this; it was set aside as a "how" question, with the note that a swappable storage layer does not by itself make the record readable without Atlas. (FA, ADR 0002)

### Raised by the research and not yet put to the owner

- Orca appears to be another Interface and holds a second copy of most Codex Sessions, so Atlas would need to count those once too. See the research file.
- How a Devin Session run through T3 Code is recorded, since none existed to examine.

## Rejected or ignored

- **The "how" framing of the first round.** After the first version of round 1 the owner said this was their first attempt and asked for the interview to stay as high-level as possible and focus on "what", not "how". Round 1 was rewritten.
- **The old agent-atlas project.** While checking T3 Code, an earlier project of the owner's called agent-atlas was found on this machine, and round 5 was first written to reconcile Atlas with it. The owner said: ignore that project, it was a first attempt, start from scratch. Nothing from it is to be used as a requirement, a vocabulary or a starting record.
- **The first version of round 5.** All ten questions were withdrawn with that instruction. They proposed: Atlas as the successor that inherits the old record; a mapping from the old vocabulary; a rule that a parked, dropped or done Pursuit becomes active again by itself when worked on; carrying the old project types over as tags; carrying over time tracking; reversing the old rule against storing full conversations; the read-versus-concluded distinction; carrying over isolated projects; T3 Code as an Interface; Hermes as home and voice; readable without Hermes. Five of these were asked again on their own merits and accepted (R5 Q2 to Q5 and, for time tracking, R6 Q1). The others were not asked again and are undecided, not rejected on their merits.
- **Leaving web chats out of v1.** Proposed in R5 Q7. The owner rejected it because they use ChatGPT a lot.
- **The claim that the monthly timesheet cannot come from Atlas.** Stated as a consequence in R5 Q1. The owner challenged it, it was withdrawn as wrong, and working time was decided on its own in R6 Q1.
- **Building Atlas as a Hermes memory provider, or stopping at an existing recall plugin.** Rejected in ADR 0001.
- **Keeping the record inside Hermes's own storage, or letting any agent write the record directly.** Rejected in ADR 0002.
- **A pull request for the `docs/domain-model` branch.** Offered after the branch was pushed; the owner said not now.

## Unconfirmed

Items where it is not certain the owner decided. Treat each as open until asked.

- **Build rather than adopt.** R1a Q1 asked whether to build Atlas as a new plugin instead of extending Memory Wiki or adopting a shared memory backend. The round was set aside before it was answered, and the rewritten round did not ask it as a question. ADR 0001, which the owner asked to have committed, rejects adopting an existing recall plugin and stopping there.
- **ADR 0001 itself.** It was offered at the end of round 3 and written because the owner's "Yes to all" was taken to include it. The owner was told to delete it if that was not meant, did not object, and later asked for both ADRs to be committed.
- **The isolated-projects rule.** R5 Q1 proposed that Atlas takes nothing from agent-atlas, including its rule for isolated projects. The owner questioned the time-tracking consequence of that question and said "yes to all remaining" for the others, so R5 Q1 was not accepted as worded. The instruction to ignore the old project implies the rule is not carried over, but the owner was never asked whether Atlas needs such a rule of its own.
- **Exclusions.** The owner was asked twice (R1a Q9, R1 Q6) to name work or personal topics that must never be collected and named none. That means none named so far, not that none exist.
- **"Research" unasked.** R1 Q5 lets Atlas research unasked. R1 Q4 and R5 Q7 defer autonomous research from v1. Settled by W Q5: research stays after v1 and arrives in the weekly Review.
- **Reopening on new activity.** Settled by W Q18: parked reopens by itself; done and dropped are only proposed for reopening.
