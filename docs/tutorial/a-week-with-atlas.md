# A week with Atlas

In this tutorial you install Atlas on your always-on Ubuntu PC and let it build your Pursuits from the Sessions you already have. Then you live through seven mornings with it. By Sunday you will have acted on Steps from five Daily Briefs and answered every Neglected question Atlas asked. You will also have resumed a Pursuit you last touched in August, in under a minute.

Atlas does not exist yet. This tutorial is the target that its architecture, spec, and tests are checked against, so every message and screen here is something Atlas must produce. The behaviour comes from [the v1 brief](../atlas-v1-brief.md). The Pursuits, Sessions, folders, and people are invented.

A box marked **Assumption** shows where this tutorial goes beyond the brief. It stands in for a parked "how" question and is a placeholder, not a decision. All of them are listed in [Assumptions](#assumptions) at the end.

In every message from Atlas, `[F]` marks a Fact, read from Evidence or counted from it by a fixed rule, and `[I]` marks an Inference, a judgment. Each item names its source once, on its first Fact read from a Session, such as `[F Claude Code, Sat 22:10]`; the rest show a bare `[F]`, and the record keeps every citation.

> **Assumption A9 (marks).** Settled (brief, G1): when a Telegram Fact shows `[F Harness, time]` and when a bare `[F]`.

## Before you start

You need these on the Ubuntu PC:

- Hermes, with its gateway running as a service.
- A Telegram bot paired with your account in Hermes.
- The Harnesses you already use: Claude Code, Codex, and Devin. You run some of their Sessions through T3 Code. You also use ChatGPT and claude.ai in the browser.

Atlas talks to you in Telegram, through your Hermes home channel. Run `/sethome` in a direct chat with your bot, or in a group's General topic: Hermes sends the brief to the home chat without its topic, so a home set in another topic would get the brief in General. If you already use that chat or its main topic for something else, point Atlas at a topic of its own in settings. Every message you type there is then Atlas's.

## Sunday evening: install Atlas and build your Pursuits

### Install the plugin

Atlas is an open-source plugin from the Hermes plugin catalog. You install it on the one machine where your Hermes runs, here the Ubuntu PC:

```
$ hermes plugins install atlas
$ hermes plugins enable atlas
$ hermes atlas setup
```

The setup command prints where Atlas lives and which Sources it found:

```
Atlas 0.1 on ubuntu-pc, inside Hermes
Record:   ~/atlas   (plain files, readable without Hermes or Atlas)
Channel:  Telegram, through the Hermes gateway
Model:    the one your Hermes uses, for every Area; secrets masked before it reads

Found on this machine
  Claude Code  ~/.claude/projects     412 Sessions since 2 Jun
  Codex        ~/.codex/sessions      538 Sessions since 9 Jun
  Devin        ~/.local/share/devin    21 Sessions since 15 Jul
  Hermes       ~/.hermes/state.db       6 Sessions since 6 Nov
Interfaces    T3 Code: its Sessions count once, under their Harness
Not found     Harnesses in the browser, such as ChatGPT and claude.ai.
              Add them in Atlas's settings.
Exclusions    none named
Machines      this one only. Sessions on your other machines are not collected.
```

Atlas found the four Harnesses installed on this PC by itself. Only Harnesses appear. Email and other Sources come after v1.

Atlas reads only the machine it is installed on. Your Windows laptop also holds Claude Code and Codex Sessions, and Atlas does not see them. When an answer could be missing laptop work, Atlas says so.

> **Assumption A2 (install).** Hermes is installed natively, not in Docker. The plugin is named `atlas` and adds `hermes atlas` commands.

> **Assumption A3 (record form).** The record is a folder of Markdown files under `~/atlas/`, with the raw Sessions under `~/atlas/evidence/`.

> **Assumption A4 (collecting Sessions).** Atlas reads each Harness's own store on this PC from disk, Hermes's `state.db` included.

### Add the web chats in settings

Open Atlas's settings and add ChatGPT and claude.ai. Atlas cannot find them on disk, because they live in the browser. Each one now shows as waiting for an export:

```
Added in settings
  ChatGPT      waiting for an export in ~/atlas/inbox
  claude.ai    waiting for an export in ~/atlas/inbox
```

> **Assumption A10 (settings).** Atlas's settings are a page beside the Board in the Hermes dashboard. How Atlas finds installed Harnesses, and how settings add the others, are parked questions.

### Build Pursuits from your history

Run the backfill. It reads every Session on the PC:

```
$ hermes atlas backfill
Read 977 Sessions. 186 subagent conversations were folded into the Sessions that started them.
301 Sessions came through T3 Code. Each counts once, under Claude Code or Codex.

Pursuits        11
  active         8   touched since 15 Oct
  parked         3   quiet for more than a month
Not a Pursuit   61 one-off Sessions, kept as Evidence
Journal        131 days, 2 Jun to 15 Nov
Board          http://127.0.0.1:9119/atlas
```

### Read the Board

Open the Board in a browser. It has one column per Status. Here the columns are stacked to fit the page:

```
ACTIVE (8)
Billing export service · project · Fri 13 Nov
  Left off  CSV totals match the ledger; the PDF rounds VAT per line [F]
  Next      Compare October's totals with per-line and per-invoice rounding [I]
Payments CI flakes · problem · Sat 14 Nov
  Left off  test_retry fails only when two workers share Redis db 0 [F]
  Next      Run test_retry.py with one worker, ten times [I]
Greenhouse logger firmware · project · Wed 11 Nov
  Left off  Logger moved to a new SD card; not checked since [F]
  Next      Read the last 20 log lines over ssh [I]
Greenhouse dashboard · project · Mon 9 Nov
  Left off  The chart shows humidity only [F]
  Next      Add the temperature series [I]
Reading tracker for the kids · idea · Fri 6 Nov
  Left off  Listed what the kids would log: book, minutes, one sentence [F]
  Next      Decide between a paper chart and a tablet app [I]
Agent-tooling newsletter · idea · Mon 19 Oct
  Left off  Three issue topics drafted, none written [F]
  Next      Write the first 200 words of issue one [I]
Bike route planner · experiment · Mon 26 Oct
  Left off  Elevation smoothing works on one GPX file [F]
  Next      Try it on a ride that goes through a tunnel [I]
Home server backups · project · Thu 29 Oct
  Left off  restic runs nightly; no restore tested [F]
  Next      Restore one folder into /tmp and compare it [I]

PARKED (3)
Keyboard firmware · experiment · Sun 30 Aug
  Left off  The split layout compiles; the right half drops keys [F]
  Next      Flash the right half with a 10 ms debounce [I]
Static site migration · project · Thu 3 Sep
  Left off  12 of 40 posts moved [F]
  Next      Move the posts that have images [I]
Pi-hole DNS loop · problem · Fri 14 Aug
  Left off  Found the loop between Pi-hole and the router; not fixed [F]
  Next      Turn off DNS forwarding on the router [I]

DROPPED (0)     DONE (0)
```

> **Assumption A6 (Board).** The Board is a tab in the Hermes dashboard.

Atlas created all eleven Pursuits on its own. It tells you about its own filing in three places, never in the Daily Brief: here on the Board, where a card it created or changed is marked until you have seen it; in the Sunday Review; and in the Journal. Anything touched in the last month started active and everything older started parked. Nothing is done or dropped, because only you end a Pursuit.

### Correct what Atlas got wrong

Atlas split the greenhouse work in two because it lives in two folders, and it put the staging deploy chores inside the billing work. Tell it in Telegram:

```
You    atlas, merge the two greenhouse ones. two folders, one thing
Atlas  Merged into "Greenhouse logger" (project). It keeps both histories, 23 Sessions.
You    split the staging deploy chores out of Billing export service
Atlas  Split. "Billing export: staging deploy" (project) has 4 Sessions.
       Next Step: rotate the staging database password [I]
       "Billing export service" keeps 15 Sessions.
You    put the greenhouse logger in a new Area, home
Atlas  Added the Area "home". Your Areas: work, health, family, home.
```

Your Telegram chat is shared with Hermes's own agent, so you open a conversation with Atlas by starting with "atlas". For the next 10 minutes everything you type is Atlas's, which is why the second and third lines need no prefix. A reply to a brief is always Atlas's. Anything else goes to Hermes's agent. If you'd rather not type the prefix, give Atlas a chat of its own in settings, where every message is Atlas's.

Atlas remembers both corrections, and you see it file new work by them on Wednesday. You add an Area the same way you correct anything, in plain words. The Board cannot do it, because there you change only Status and Focus.

### Pick your first Focus

On the Board, click the star on four cards: **Billing export service**, **Payments CI flakes**, **Greenhouse logger**, and **Reading tracker for the kids**. The star marks Focus. The Daily Brief draws its Steps from Focus only. Had you skipped this, Monday's brief would still carry Steps, from your most recent work, under the heading "no Focus yet", until you starred something.

The four other active Pursuits stay on the Board, and Atlas pushes none of them. Three have been quiet for 14 days or more outside Focus, so they are Neglected: the newsletter, the bike route planner, and the home server backups. Atlas asks about one a day, the quietest first.

## Monday: act on your first Daily Brief

At 07:30, the time you kept from setup's default, the first Daily Brief arrives in Telegram. If the PC had been down at 07:30, the brief would arrive when Atlas came back that day.

```
Atlas · Daily Brief · Monday 16 November

STEPS
1 · Payments CI flakes
    Run tests/test_retry.py with one worker, ten times in a row. [I]
    Left off: it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]
    Resume: Claude Code on ubuntu-pc, ~/work/payments-api, branch fix/retry-flake. [F]
2 · Billing export service
    Export October twice, with per-line and per-invoice VAT rounding, and compare the totals. [I]
    Left off: CSV totals match the ledger; the PDF rounds VAT per line. [F Claude Code via T3 Code, Fri 18:02]
    Resume: Claude Code on ubuntu-pc, ~/work/billing-export, branch pdf-vat. [F]
3 · Greenhouse logger
    Read the last 20 lines of the logger's log over ssh. [I]
    Left off: the logger moved to a new SD card and has not been checked since. [F Claude Code, Wed 17:25]
    Resume: Claude Code on ubuntu-pc, ~/home/greenhouse/firmware. [F]

    Reply "1 prompt", "2 prompt" or "3 prompt" for a paste-in prompt.

YESTERDAY'S STEPS
    None. This is your first brief.

NEEDS A DECISION
    Agent-tooling newsletter · idea · quiet 28 days [F]
    Last: you drafted three issue topics in Claude Code on Mon 19 Oct. [F]
    Continue, park, or drop?
```

Each Step is the smallest action that settles an unknown, one per Pursuit.

### Resume in a different Harness

You last worked on the CI flakes in Claude Code. Resume in Codex instead:

1. Reply `1 prompt` to the brief. Atlas sends the prompt as its own message:

    ```
    Resuming "Payments CI flakes".
    Where: ubuntu-pc, ~/work/payments-api, branch fix/retry-flake
    Left off: test_retry fails only when two pytest-xdist workers share Redis db 0. [F]
    Decided: keep xdist; no sleeps in tests. [F]
    Open question: does the conftest fixture flush Redis between tests? [I]
    Sessions to reopen: Claude Code "retry flake" (Sat 14 Nov), Codex "ci matrix" (Thu 12 Nov)
    Next Step: run tests/test_retry.py -n 1 ten times and report how many runs pass.
    ```

2. Copy it, open Codex in `~/work/payments-api`, and paste it.

Codex starts the first test run about 40 seconds after you replied.

The brief carries no buttons, so you ask for the prompt by replying with the Step's number and "prompt". On the Board, each card has a **Copy prompt** button that does the same.

### Answer the brief in plain words

Reply to the brief:

```
You    1 done, 10/10 pass with one worker. park the newsletter
Atlas  Payments CI flakes: Step done.
       Agent-tooling newsletter: parked.
```

Atlas answers whenever you write to it, and acknowledges each reply. It never starts a conversation on its own except for two messages: the Daily Brief each morning and the Review on Sunday. It will raise the parked newsletter again only rarely: in a Sunday Review, no sooner than 90 days after its last Session, and only one parked Pursuit per Review.

That evening you do Step 2 in Claude Code through T3 Code, and you don't reply about it. You don't do Step 3. Later you ask ChatGPT about your knee, then request data exports from ChatGPT and claude.ai. Each export takes a couple of days.

## Tuesday: let Atlas notice what you did

The Tuesday brief opens its follow-up with Monday's Steps:

```
YESTERDAY'S STEPS
    ✓ Payments CI flakes: done, you said so. [F]
    ✓ Billing export service: done. Seen in Claude Code via T3 Code, Mon 21:40:
      per-invoice rounding matches the ledger. [F]
    · Greenhouse logger: no sign of it in your Sessions. [F] It is still the next Step. [I]
```

You never replied about Step 2. Atlas saw it done in your Session and marked it done. You also never answered about Step 3, and it is still open: Atlas offers it once more today. A Step offered twice and still not done ends as skipped, by silence.

Today's Steps are the greenhouse log check again, a choice for the reading tracker, and a billing Step:

```
STEPS
1 · Greenhouse logger            Read the last 20 lines of the logger's log over ssh. [I]
2 · Reading tracker for the kids Write down paper chart or tablet app, and why. [I]
3 · Billing export service       Switch the PDF export to per-invoice rounding and rerun October. [I]
    (each Step has its Left off and Resume lines, and the brief ends with the "prompt" hint, as on Monday)

NEEDS A DECISION
    Bike route planner · experiment · quiet 22 days [F]
    Last: elevation smoothing worked on one GPX file in Codex on Mon 26 Oct. [F]
    Continue, park, or drop?
```

Reply in the morning, and again in the evening:

```
You    3 remind me friday. drop the bike planner
Atlas  Billing export service: I'll suggest this Step again on Friday.
       Bike route planner: dropped. I won't push it again. You can still find it.
...
You    2 done. tablet app, nothing fancy
Atlas  Reading tracker for the kids: Step done.
```

Atlas holds the billing Step until Friday. It is not a due date: the Step stays open, is not offered before Friday, and comes first in Friday's brief. Holding it is not a skip. You can hold a Neglected question the same way.

### Use Atlas from inside a Harness

On Tuesday evening you open Claude Code in `~/home/greenhouse`. Ask the agent:

```
You     where did I leave off on the greenhouse logger?
Claude  (atlas_read) Resume Note for "Greenhouse logger":
        Where: ubuntu-pc, ~/home/greenhouse (firmware/ and dashboard/)
        Left off: the logger moved to a new SD card; not checked since. [F]
        Decided: log to the SD card and sync to the PC hourly. [F]
        Open question: does the 32 GB card fill up before spring? [I]
        Next Step: read the last 20 log lines over ssh. [I]
        Sessions to reopen: Claude Code "sd card swap" (Wed 11 Nov),
        Claude Code "humidity chart" (Mon 9 Nov)
```

The agent reads the log with you. It shows that the logger writes every minute, and you decide to log every five minutes. The agent records the decision on its own:

```
Claude  (atlas_note) Added a Note to Greenhouse logger: "Decided to log every 5 minutes
        instead of every minute, to save the SD card."
```

The agent may add Notes unasked. It changes a Pursuit only when you tell it to in this Session:

```
You     tell atlas the open question is settled: at 5-minute logging the card lasts about 3 years
Claude  (atlas_update) Updated the Resume Note of Greenhouse logger. Removed the open question
        about the card filling up and added that decision.
```

Atlas made the change because it found your words in this Claude Code Session. Where it cannot check that, as in Devin, it keeps the request as a Note with your words and offers "apply" the next time you talk to it.

> **Assumption A8 (agent access).** Agents reach Atlas through an Atlas MCP server added to Claude Code and Codex, with the tools `atlas_read`, `atlas_note`, and `atlas_update`.

## Wednesday: a short brief, late web chats, and an idea that becomes a project

The Wednesday brief carries two Steps, not three:

```
YESTERDAY'S STEPS
    ✓ Greenhouse logger: done. Seen in Claude Code, Tue 21:15. [F]
    ✓ Reading tracker for the kids: done, you said so: tablet app. [F]
    · Billing export service: held until Friday, as you asked. [F]

STEPS
1 · Payments CI flakes            Run the suite 50 times with a separate Redis db per xdist worker. [I]
2 · Reading tracker for the kids  Create the app and get one screen that logs a book and minutes. [I]

NEEDS A DECISION
    Home server backups · project · quiet 20 days [F]
    Last: you set up the nightly restic job in Devin on Thu 29 Oct. [F]
    Continue, park, or drop?
```

You do both Steps during the day and leave the backups question unanswered.

The reading tracker now has its own repository and code, so Atlas changes its Kind on its own, as it files everything else. You can correct it, as with any filing. Its card keeps one history:

```
Reading tracker for the kids · project (an idea until Wed 18 Nov) · Wed 18 Nov  ★  CHANGED
  Left off  The app logs a book and minutes on one screen [F]
  Next      Add the "one sentence" field [I]
```

Two Sessions that rotate the staging database password are filed under **Billing export: staging deploy**, not under the billing service. Atlas applied Sunday's split by itself.

### Add your web chats

Your ChatGPT and claude.ai exports arrived by email. Move both zip files into `~/atlas/inbox/`.

> **Assumption A5 (web chats).** Atlas gets ChatGPT and claude.ai Sessions from each service's data export, which you drop into `~/atlas/inbox/`. What access these services allow is unchecked.

Atlas reads 143 ChatGPT and 38 claude.ai Sessions, then deletes the two zip files, which also held other people's group chats it never reads. Most are one-off questions, kept as Evidence with no Pursuit. Several Sessions from the last month are about your knee, so Atlas creates a new Pursuit in the health Area. The Board shows it with a proposal:

```
Knee rehab plan · problem · Mon 16 Nov   NEW        Atlas proposes: add to Focus [I]
  Left off  Physio exercises listed for weeks 1 and 2; week 3 not planned [F ChatGPT, Mon 19:10]
  Next      Ask for a week-3 progression based on how week 2 felt [I]
```

Atlas marks a Focus proposal on the card the day it has one, and asks about any you have not answered in the Sunday Review. It never puts one in the Daily Brief. Click its star. Focus now holds five Pursuits.

The exports also fill gaps in past Journal entries. Monday's entry gains a ChatGPT Session, and you read the correction on Friday.

## Thursday: close a Pursuit and ask Atlas what you used to dig for

The follow-up proposes an ending:

```
YESTERDAY'S STEPS
    ✓ Payments CI flakes: done. Seen in Codex, Wed 16:05: test_retry passed 50 of 50 runs
      with a Redis db per worker. [F]
      This Pursuit looks finished. [I] Reply "close it" to mark it done.
    ✓ Reading tracker for the kids: done. Seen in Codex, Wed 14:30. [F]

STEPS
    (three Steps: Knee rehab plan, Reading tracker for the kids, Greenhouse logger)

NEEDS A DECISION
    Home server backups · project · quiet 21 days [F]
    Last: you set up the nightly restic job in Devin on Thu 29 Oct. [F]
    Continue, park, or drop?
```

The backups question is back because you did not answer it yesterday. This is its second and last ask: if you ignored it again, Atlas would park the backups itself, say so on the Board and in the Review, and let "continue" undo it. Reply:

```
You    close it. continue the backups
Atlas  Payments CI flakes: done. It leaves Focus.
       Home server backups: stays active. Its quiet days count from today.
```

The finished Pursuit leaves Focus, because Focus holds only active Pursuits. Atlas proposed the ending here because yesterday's Step finished it. An ending it infers any other way waits for the Sunday Review, and every proposed ending is marked on the Board card until you answer. If you had ignored it, Atlas would not have asked again in Friday's brief. You are busy all day and act on none of today's Steps.

### Ask the questions you used to dig for

Ask Atlas in Telegram which ideas or experiments you have neglected:

```
You    atlas, what have I neglected?
Atlas  Nothing is Neglected today. [F]
       You parked Agent-tooling newsletter on Monday and dropped Bike route planner on Tuesday.
       Home server backups was Neglected until you said "continue" this morning. [F]
```

Find a Session from a Pursuit you dropped:

```
You    find the conversation where I got the GPX elevation smoothing working
Atlas  Codex, Sun 25 Oct 20:14, ~/code/bike-routes [F]
       Pursuit: Bike route planner (dropped) [F]
       You used a 30-point rolling median, then removed spikes over 8 m. [F]
```

Every agent can read all of Atlas, across all Areas. Ask Codex about a day in August:

```
You    what was I doing on 14 August?
Codex  (atlas_read) Journal for Friday 14 August 2026:
       - Pi-hole DNS loop (home): found the loop between Pi-hole and the router; not fixed.
         [F Claude Code 09:40]
       - Static site migration (work): moved 12 of 40 posts. [F Codex 13:05]
       - One-off question, no Pursuit (family): a birthday party plan for a seven-year-old.
         [F ChatGPT 21:30]
       Active time: 3 h 10 min. [F]
       Covers AI Sessions on ubuntu-pc only. Work on other machines is not collected. [F]
```

The answer covers only your Sessions with AI agents on this PC. A meeting that day is not in it, and neither is anything you did on the laptop.

### Ask Atlas to reach someone else

```
You    atlas, send Dana the billing export status
Atlas  I can't send anything to other people. I only read, record, and message you.
       Here is the status to forward yourself:
       "Billing export: CSV totals match the ledger. The PDF moves to per-invoice VAT rounding." [F]
```

Atlas refuses because the message is addressed to it. Without "atlas" and outside the 10 minutes, the message would go to Hermes's own agent, and whether Dana hears from you would be Hermes's decision, not Atlas's.

## Friday: read the record without Hermes, forget a Session, and pause

The Friday brief brings back the billing Step you held on Tuesday. The decision slot is empty, because no Pursuit qualifies:

```
YESTERDAY'S STEPS
    · Reading tracker for the kids, Greenhouse logger: no sign of them in your Sessions. [F]
      Both stay open; I'll offer each once more. [I]
    · Knee rehab plan: no sign yet. You work on it in ChatGPT, whose Sessions reach me late. [F]

STEPS
1 · Billing export service  Switch the PDF export to per-invoice rounding and rerun October. [I]
2 · Knee rehab plan         Ask for a week-3 progression based on how week 2 felt. [I]

NEEDS A DECISION
    Nothing today.
```

You do the billing Step at 09:15 in Claude Code, and you won't get to the knee today:

```
You    skip 2, not today
Atlas  Knee rehab plan: Step skipped.
```

### Read the record with Hermes stopped

At lunch you stop Hermes to upgrade it. While it is down, open the record:

```
$ ls ~/atlas
evidence/  inbox/  journal/  notes/  profile.md  pursuits/
$ less ~/atlas/journal/2026-11-16.md
```

```
# Monday 16 November 2026

Active time 3 h 55 min [F]
  work    3 h 15 min  Payments CI flakes 1 h 10, Billing export service 1 h 55, one-off 10 min
  health    40 min    Knee rehab plan 40 min

Worked on
- Payments CI flakes: ran test_retry with one worker; 10 of 10 passed. [F Codex 08:05]
- Billing export service: per-invoice VAT rounding matches the ledger. [F Claude Code via T3 Code 21:40]
- Knee rehab plan: listed physio exercises for weeks 1 and 2. [F ChatGPT 19:10]

Decided
- Billing export service: round VAT per invoice, not per line. [F Claude Code via T3 Code 21:52]

Finished
- Steps on Payments CI flakes and Billing export service. [F]

Evidence: 5 Sessions. Codex 12:30 belongs to both Payments CI flakes and Billing export service. [F]
Corrected Wed 18 Nov: added ChatGPT 19:10 (40 min) when your ChatGPT export arrived.
```

Each entry gives active working time per Area and per Pursuit, so your monthly timesheet can come from the Journal. Active time is time you were engaged: a stretch runs while your own messages are less than 15 minutes apart, an agent working alone does not count, and parallel Sessions count once. A minute on two Pursuits at once is split between them, so the lines add up to the day. Your chats with Atlas in Telegram are not in these figures: what you tell Atlas is kept as Notes, but talking about the work is not the work.

Next, read what Atlas has learned about you:

```
$ cat ~/atlas/profile.md
# Profile
- You work in two blocks, 07:30 to 10:00 and 21:00 to 23:30. [I from 131 days of Sessions]
- You skip Steps that need hardware. [I from Greenhouse logger, Mon 16 Nov]
- You park hardware experiments when parts arrive late. [I from Keyboard firmware, Pi-hole DNS loop]
```

Every entry is there to make the Daily Brief better. One entry is wrong. When Hermes is back, correct it through Atlas, not by editing the file. If you edited the file, Atlas would put it back, keep what you typed as a Note, and ask whether to apply it.

```
You    atlas, profile: I don't skip hardware steps. I skip greenhouse steps in the morning because it's dark. evenings are fine
Atlas  Changed: "You do Greenhouse logger Steps in the evening, not in the morning." [F your Note, Fri 13:02]
```

### Forget a Session

On Thursday you pasted the staging database password into a Codex Session. Tell Atlas to forget it:

```
You    atlas, forget the Codex Session from Thu 19 Nov 14:02. I pasted a password in it
Atlas  Forgetting removes these for good:
       - the Session itself
       - 1 Fact in the Resume Note of Billing export: staging deploy
       - 1 line in Thursday's Journal
       Not removed: the Session in Codex itself (~/.codex/sessions/2026/11/19/…).
       Delete it there if the password must go.
       Reply "forget" to go ahead.
You    forget
Atlas  Forgotten.
```

Atlas had already masked the password everywhere you or an agent could read it. Forget removes the raw copy too. Atlas asked once before it acted, because Forget is the one thing it cannot undo. Any reply but "forget" would have cancelled it. Afterwards the record keeps only that a Session was forgotten on Friday, not what was in it. A dropped Pursuit stays findable, as the bike route planner did on Thursday. Forgotten Evidence does not.

### Pause for the weekend

Before you leave, open the Board and move **Billing export: staging deploy** from Active to Parked. The password is rotated and the rest can wait. Then tell Atlas:

```
You    atlas, pause until sunday morning, we're at my parents'
Atlas  Paused until Sun 22 Nov 07:00. No Daily Brief on Saturday, and no Pursuit becomes
       Neglected while Atlas is paused. Write to me to come back early.
```

Every Pause has an end. Had you said only "pause", Atlas would have paused for one week and said so.

On Saturday nothing arrives. Atlas still files anything you do in your Harnesses, but it sends nothing, and Saturday counts on none of its clocks: not toward Neglected, not toward raising a parked Pursuit, and not toward a Step's two offers.

## Sunday: see the week and resume anything

The Sunday brief follows up on Friday, because Saturday had no brief:

```
YESTERDAY'S STEPS (from Friday)
    ✓ Billing export service: done. Seen in Claude Code, Fri 09:15. [F]
    · Knee rehab plan: skipped, you said so. [F]

STEPS
1 · Knee rehab plan               Ask for a week-3 progression based on how week 2 felt. [I]
    Left off: exercises listed for weeks 1 and 2; week 3 not planned. [F ChatGPT, Mon 19:10]
    Resume: ChatGPT, Session "knee weeks 1 and 2" (Mon 16 Nov). [F]
2 · Reading tracker for the kids  Add the "one sentence" field. [I]

    Reply "1 prompt" or "2 prompt" for a paste-in prompt.

NEEDS A DECISION
    Nothing today.
```

Reply `1 prompt` and paste the prompt into claude.ai, not ChatGPT where you started. The week-3 plan is on screen in under a minute.

Reply `1 done`. Atlas cannot see a claude.ai Session until your next export arrives, so it needs your word.

### Read the weekly Review

At 18:00 on Sunday the weekly Review arrives. It is the only message besides the Daily Brief that Atlas sends without being asked:

```
Atlas · Review · week of 16 to 22 November

FINISHED
- Payments CI flakes: you marked it done on Thursday. [F]
- 8 Steps done: 3 you told me about, 5 I saw in your Sessions. [F]

DECIDED
- Billing export service: round VAT per invoice. [F Claude Code via T3 Code, Mon 21:52]
- Greenhouse logger: log every 5 minutes. [F Note from Claude Code, Tue 21:20]
- Reading tracker for the kids: a tablet app. [F your reply, Tue 20:45]

CHANGED
- Parked Agent-tooling newsletter and Billing export: staging deploy. [F]
- Dropped Bike route planner and continued Home server backups. [F]
- Knee rehab plan added to Focus on Wednesday. [F]

WHAT I DID ON MY OWN
- Created Knee rehab plan (health) from your ChatGPT Sessions. [F]
- Changed Reading tracker for the kids from idea to project: it has its own repository now. [I]
- Filed 2 Sessions under Billing export: staging deploy, by Sunday's split. [F]
  Reply to correct any of these.

FOCUS
- Payments CI flakes left Focus when you closed it, so there is a free slot.
  Proposed: Home server backups. You said "continue" on Thursday, and its next Step
  takes ten minutes. [I] Reply "add backups" or "leave it".

PARKED
- Pi-hole DNS loop · problem · quiet 100 days [F]
  Left off: found the loop between Pi-hole and the router; not fixed. [F Claude Code, 14 Aug]
  Continue, keep parked, or drop?
```

The Review answers the fifth of your original questions, what you decided, changed or finished, without your asking. It also lists what Atlas filed on its own, because the Daily Brief has no room for that. You saw each of these on the Board the day it happened. The FOCUS proposal was on the backups card from Thursday. The PARKED section raises one parked Pursuit a week, the one quiet longest, and only once it has been quiet 90 days; the keyboard firmware, at 84 days, waits. If you say nothing, it stays parked and comes up again in 90 days. Atlas would also propose taking a Pursuit out of Focus after three skipped Steps in a row; the knee plan has one. Once research is added after v1, its findings arrive here too.

Ask for your working time by Area:

```
You    atlas, how long did I work this week, by Area?
Atlas  Active time with AI agents, Mon 16 to Sun 22 Nov [F]
       work     13 h 40   Billing export service 6 h 30, Payments CI flakes 4 h 05,
                          Billing export: staging deploy 1 h 15, one-off 1 h 50
       home      2 h 10   Greenhouse logger 2 h 10
       family    2 h 25   Reading tracker for the kids 2 h 25
       health   40 min    Knee rehab plan 40 min
```

Today's claude.ai Session is missing until your next export arrives. Atlas corrects the Journal when it does.

### Resume a Pursuit you parked in August

```
You    atlas, where did I leave off on the keyboard firmware?
Atlas  Keyboard firmware · experiment · parked · last Session Sun 30 Aug
       Where: ubuntu-pc, ~/code/kb-firmware, branch split-right [F]
       Left off: the split layout compiles; the right half drops keys when you type fast. [F Claude Code, 30 Aug]
       Decided: keep QMK; no custom matrix scanning. [F]
       Open question: is it the debounce or the TRRS cable? [I]
       Next Step: flash the right half with a 10 ms debounce and type the test paragraph. [I]
       Sessions to reopen: Claude Code "right half drops" (30 Aug), Codex "qmk build" (28 Aug)
       Reply "prompt" for a paste-in prompt.
```

Reply `prompt`, open Codex in `~/code/kb-firmware`, and paste. Codex starts the build 50 seconds later. By evening Atlas has seen the build Session and moved the card from Parked to Active by itself, marked as changed. Asking where you left off did not do that; the Session that advanced it did. A done or dropped Pursuit would not reopen this way: Atlas would only propose reopening it, on the card and in the Review.

## What the week showed

You acted on at least one suggested Step on five of six brief mornings: Monday, Tuesday, Wednesday, Friday, and Sunday. Thursday was the miss.

You resumed three Pursuits in under a minute each. You picked up the CI flakes in Codex after stopping in Claude Code, and the knee plan in claude.ai after starting in ChatGPT. The keyboard firmware had been parked since August.

## Assumptions

The assumptions stand in for parked "how" questions in the brief. None is decided unless it is marked Settled.

- **A1.** Settled (brief, D Q16): the Channel is the Hermes home channel, Telegram here, and can be pointed at another chat or topic in settings.
- **A2.** Hermes runs natively, the plugin is named `atlas`, and setup and backfill are `hermes atlas` commands. That Atlas comes from the catalog is decided (brief, W Q19).
- **A3.** The record is Markdown files under `~/atlas/`, with raw Sessions under `~/atlas/evidence/`.
- **A4.** Atlas collects Sessions by reading each Harness's store on the PC from disk, Hermes's `state.db` included.
- **A5.** ChatGPT and claude.ai Sessions come from data exports dropped into `~/atlas/inbox/`.
- **A6.** The Board is a tab in the Hermes dashboard.
- **A7.** Settled (brief, D Q2): the paste-in prompt is asked for by replying "1 prompt", arrives as its own message, and has a Copy button on the Board card.
- **A8.** Agents reach Atlas through an Atlas MCP server with `atlas_read`, `atlas_note`, and `atlas_update`.
- **A9.** Settled (brief, G1): a Fact's citation shows once per item, on its first Fact read from a Session; other Facts show `[F]`, and an Inference `[I]`.
- **A10.** Atlas's settings are a page beside the Board in the Hermes dashboard, where you add Harnesses Atlas cannot find.

Earlier drafts also listed open questions Q1 to Q19, where the brief was silent or ambiguous. Each is now decided, closed, or handed to /architect; see [Settled from the tutorial](../atlas-v1-brief.md#settled-from-the-tutorial) in the brief. None is open.
