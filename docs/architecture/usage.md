# Atlas v1, candidate 1: usage (the caller's view, written first)

Shape in one line: **the Markdown files are the record.** Every record file is Markdown with YAML front matter, one file per Pursuit, per Journal day, per sent message, per conversation-day of Notes, per Session; plus `profile.md`, `settings.md`, `pauses.md`, `forgotten.md`. Each process that loads the plugin can write, but only through `record.commit()`. That call takes an OS file lock, checks the base revision of every file it touches, stages the new bytes and applies them with a roll-forward manifest. Everything under `~/atlas/.atlas/` is machinery: the lock, the last-written mirror used to restore hand edits, a disposable SQLite index, and the LLM cache.

## The surface every caller sees

```python
from atlas.app import Atlas                          # the only public entry to the core

atlas = Atlas.open()                                 # finds the record dir; rolls forward any half-done commit
atlas.setup() -> SetupReport                         # idempotent
atlas.backfill() -> BackfillReport                   # resumable
atlas.tick(now) -> TickReport                        # every 10 min from cron: collect, file, send what is due
atlas.handle(OwnerMessage) -> Reply                  # owner wrote in a Channel (claimed message, /atlas, CLI)
atlas.act(Command, Authority) -> Outcome             # structured change: Board, agent tools, settings page
atlas.read(Query) -> Answer                          # every question; Answer carries marked Statements and Coverage
```

Thin shells call nothing else:

| Shell (Hermes surface) | Calls |
|---|---|
| `hermes atlas setup / backfill / tick / mcp / reindex / status` (`register_cli_command`) | `Atlas.setup/backfill/tick`, `agentapi.serve_mcp()` |
| cron no_agent job every 10 min, script `$HERMES_HOME/scripts/atlas_tick.sh` → `hermes atlas tick --quiet` | `Atlas.tick(now)` |
| `pre_gateway_dispatch` hook | `channel.Listener.note_inbound(event, gateway)` (records the home channel's owner and the reply-to; no LLM) |
| `post_gateway_admission` hook | `channel.Listener.claim(...)` → `Atlas.handle(msg)` → `{"action":"handled","reply":text}` |
| `/atlas <text>` slash command | `Atlas.handle(msg)` |
| Hermes tools `atlas_read`, `atlas_note`, `atlas_update` (`register_tool`) and the same three over Atlas's own stdio MCP server | `agentapi.call(tool, args, caller)` → `Atlas.read` / `Atlas.act` |
| Dashboard tab `/atlas` (`dashboard/plugin_api.py`) with inner tabs Board and Settings | `Atlas.read(BoardQ())`, `Atlas.act(SetStatus/SetFocus/MarkSeen/ChangeSettings, Owner(via="board"))` |

Notation in this file: `→` is a return value, `⇒` a commit, `[F]`/`[I]` as rendered in Telegram.

---

## Sunday evening

### 1. Install and setup

```
$ hermes plugins install atlas        # Hermes asks dependency consent (zstandard, mcp, pyyaml)
$ hermes plugins enable atlas
$ hermes atlas setup
```

```python
Atlas.open()                                   # record dir = settings key plugins.entries.atlas.settings.record_dir, default ~/atlas
atlas.setup()
  sources.probe(settings)  → [Found(claude_code, root=~/.claude/projects, sessions=412, since=2026-06-02),
                              Found(codex, ~/.codex/sessions, 538, 2026-06-09),
                              Found(devin, ~/.local/share/devin/cli/sessions.db, 21, 2026-07-15),
                              Found(hermes, $HERMES_HOME/state.db, 6, 2026-11-06),
                              Interface(t3_code, ~/.t3/userdata/statev2.sqlite)]
  ⇒ README.md, settings.md (harnesses found, defaults 07:30 / Sun 18:00 / 15 min gap), profile.md (areas: work, health, family),
    pauses.md, forgotten.md                    # each sealed, mirrored
  channel.Telegram.check_home()  → HomeChannel ok        # `hermes send -l --json` lists the telegram home channel
  schedule.install_tick()                      # writes $HERMES_HOME/scripts/atlas_tick.sh; `hermes cron create "*/10 * * * *"
                                               #   --name atlas-tick --no-agent --script atlas_tick.sh --catch-up off --deliver local`
                                               #   (skips if `hermes cron list --all` already shows atlas-tick)
→ SetupReport(record=~/atlas, channel="Telegram, through the Hermes gateway",
              model="the one your Hermes uses, for every Area; secrets masked before it reads",   # D Q15
              found=[...], not_found=["ChatGPT","claude.ai"],
              mcp_commands=["claude mcp add atlas -- hermes atlas mcp", "codex mcp add atlas -- hermes atlas mcp"],
              disclosures=["reads ~/.claude, ~/.codex, devin, ~/.t3, state.db read-only", "writes ~/atlas", "cron job atlas-tick"])
```

Printed exactly as in the tutorial, plus the two MCP lines to paste. Atlas never edits Claude Code or Codex config itself (the catalog scanner blocks that). Setup also offers, never requires, a chat or topic of Atlas's own (`settings.channel.atlas_chat`, D Q16): the briefs then go there and every message there is Atlas's. The owner runs `/sethome` in a direct chat with the bot or in a group's General topic: `hermes send --to telegram` drops the home's topic, so a brief to a home set in another topic lands in General, and messages typed in that topic are not Atlas's (§9).

### 2. Settings: add the web chats

Dashboard → Atlas tab → Settings inner tab → "Add ChatGPT", "Add claude.ai".

```python
atlas.act(ChangeSettings({"add_harness": "chatgpt"}), Owner(via="board", note))
atlas.act(ChangeSettings({"add_harness": "claude_ai"}), Owner(via="board", note))
  ⇒ settings.md  harnesses.chatgpt: {enabled: true, mode: export, found_by: owner, roots: ["~/atlas/inbox"]}
  ⇒ notes/…-board.md  "Added ChatGPT in Settings."
→ Outcome(ok) ; Settings page shows "ChatGPT waiting for an export in ~/atlas/inbox"
```

The tab's backend is `settings_api.py` (`GET /settings`, `PATCH /settings` with one change), mounted with the Board's routes. `settings_rules.change` checks each change, pure: a web chat (ChatGPT, claude.ai), a store Harness's extra folder or an exclusion (a full path, `/…` or `~/…`, in UTF-8, with no `.` or `..` part), an `HH:MM` time, a weekday, a gap of 1 to 120 minutes, or `telegram:<chat>[:<topic>]` (ASCII digits). A refused change answers 422 with its reason and writes nothing. A folder reached through a link (any part below home, or below `/` outside it), or whose store folder (`sessions/` and `archived_sessions/` for Codex, `projects/` for Claude Code) is a link, is refused when added (`sources.linked`). Each time collection reads a folder, the folder named in settings and the store folder in it, the default location's included, are skipped with a `LinkedElsewhere` log line when they are links. A value in `settings.md` the system cannot be given (written by hand) is skipped by collection, still shown in the tab, and can be removed there. The folder a Harness always reads (`sources.defaults`: its env override or default) shows as found with no remove control, and removing it is refused (`ALWAYS_READ`): an exclusion leaves part of it out. A typed folder is never logged, echoed in a refusal or written into the Note; the Note names only the kind of change. The timezone is not a setting: changing it would shift every stored time (`ChangeSet.put_settings`).

### 3. Backfill

```
$ hermes atlas backfill
```

```python
atlas.backfill(now)
  sources.collect(settings, snapshot.held(), every=True)    # every adapter, every Session, read 50 at a time
    → [CollectedSession(...)]  977 groups; 186 subagent parts folded; 301 with interface="T3 Code"
  ⇒ evidence/<harness>/<yyyy>/<mm>/<id>/{session.md, raw/*.zst}  (batched commits of 50)
  librarian.card(session) for each Session with no card  ⇒ card.md (Inference, cached; resumable, commits of 50)
  librarian.group_backfill(snapshot, ids)                 # approach B, while the record holds no Pursuit:
                                                          # group by cwd from metadata, one ctx.llm call per
                                                          # group reading its cards and how each Session ended,
                                                          # then one cross-group merge call over the drafts
    → Grouping(read=[every id], drafts=[PursuitDraft] 11, one_offs={id: Area} 61)
  rules.start_state(snapshot, grouping, now)   # last turn in last 30 days → active, else parked; never done/dropped;
                                               # Conflict when a Session read is removed → group again without it
  ⇒ pursuits/*.md (11), history line "Created from N Sessions [I · ...]" each, one-offs marked in card.md
  for each of 131 days with Active Time:          # rules.workdays over rules.timed(snapshot, one-offs)
    activetime.day(sessions, day, tz, gap) → DayTime
    librarian.journal_lines(snapshot, day, rules.uncited(...))   # one call per new Session a Pursuit lists
    rules.journal_day(snapshot, day, time, lines)   # Conflict when a Session it counts or cites is removed
  ⇒ journal/*.md (131)
→ BackfillReport(sessions=977, folded=186, via_interface={"T3 Code": {...}},
                 filed=Filed(pursuits=11, active=8, parked=3, one_offs=61, active_since=15 Oct,
                             board="http://127.0.0.1:9119/atlas"),
                 journal=Journaled(days=131, first=2 Jun, last=15 Nov))
```

Re-running backfill re-uses cards and committed evidence (idempotent by Evidence id = hash(harness, native id)).

### 4. Read the Board

```python
# dashboard/plugin_api.py: GET /api/plugins/atlas/board
atlas.read(BoardQ()) → BoardView(columns={active:[Card...], parked:[...], dropped:[], done:[]})
  Card(name, kind, last_worked, left_off: Statement, next_step: Statement, focus, marks={new|changed|proposal})
```

All eleven cards carry `new` because each Pursuit's History has an Atlas line the owner has not seen.

### 5. Corrections by chat (merge, split, add Area)

The owner types `atlas, merge the two greenhouse ones. two folders, one thing`. The next two lines need no prefix: they fall in the 10-minute exchange window (D Q1). A reply to a Daily Brief, a message in Atlas's own chat, or `/atlas …` is Atlas's too; anything else goes to Hermes's agent. A reply to one of Atlas's answers is Atlas's only inside the window or with the prefix (§9).

```python
# pre_gateway_dispatch: channel.Listener.note_inbound(event, gateway)  → remembers (chat, msg id, reply_to) ; no LLM
# post_gateway_admission:
channel.Listener.claim(session_key, platform, source, message_id, text)
  → OwnerMessage(text, at, channel=TelegramRef(chat), reply_to=None)     # claimed: addressed to Atlas
atlas.handle(msg)
  ⇒ notes/2026/11/2026-11-15-telegram.md   append entry nt-… (the owner's words: W Q14, a Note)
  converse.interpret(msg, snapshot) → [Merge(a="Greenhouse logger firmware", b="Greenhouse dashboard",
                                            into_name="Greenhouse logger", rule="two folders, one thing")]
  rules.decide(snapshot, Merge(...), Owner(via="telegram", note=nt-…)) → Decision(changes, outcome)
  ⇒ pursuits/greenhouse-logger.md (both Sessions lists + both Histories + Filing rule "~/home/greenhouse/firmware and
     .../dashboard are one Pursuit [F your Note · nt-…]"), pursuits/greenhouse-dashboard.md removed,
     journal links rewritten in the same commit
→ Reply("Merged into \"Greenhouse logger\" (project). It keeps both histories, 23 Sessions.")
```

The reply text is also appended to the same Notes file, so the Notes file reads as the conversation. Split and Area work the same way:

```python
converse.interpret → [Split(pursuit="Billing export service", new_name="Billing export: staging deploy",
                            criterion="staging deploy chores")]
  rules.decide → librarian.partition(snap, pursuit, criterion, new_name) → PursuitDraft is called BEFORE decide
                 (LLM, outside the lock); decide receives the draft as data ⇒ two pursuit files, filing rule on both
→ Reply("Split. \"Billing export: staging deploy\" (project) has 4 Sessions. Next Step: rotate the staging database password [I] ...")

converse.interpret → [AddArea("home"), SetArea("Greenhouse logger", "home")]  ⇒ profile.md areas, pursuit file
→ Reply("Added the Area \"home\". Your Areas: work, health, family, home.")
```

### 6. Pick the first Focus (stars)

```python
# POST /api/plugins/atlas/pursuits/{id}/focus {on:true}   ×4
atlas.act(SetFocus(pursuit_id, True), Owner(via="board"))
  rules.decide: Focus only for active Pursuits; soft cap 5 (a sixth star is accepted, Board shows "6 in Focus")
  ⇒ notes/…-board.md one-line Note ("You added it to Focus on the Board"), pursuit front matter focus: true,
    History "You added it to Focus [F your Note · nt-…]" (owner lines never raise the changed mark)
→ Outcome(BoardView)
```

Neglected is not stored: `rules.neglected(snapshot, today)` derives the three quiet non-Focus Pursuits whenever asked. Had the owner starred nothing, `compose_daily` would see no owner SetFocus line in any History and draw Monday's Steps from the most recently active Pursuits under "STEPS (no Focus picked yet)" (D Q10), until the first star.

---

## Monday

### 7. The first Daily Brief at 07:30

```python
# 07:30:00 cron fires atlas_tick.sh → hermes atlas tick --quiet  (prints nothing; Hermes delivers nothing)
atlas.tick(now=Mon 07:30)
  record.reconcile()                    # hand-edit sweep (stat cache → re-hash changed files)
  sources.collect(...) ⇒ new evidence ; librarian.file(new_ids) ⇒ pursuit updates     (bounded: brief is due)
  schedule.due(snapshot, now) → [DailyBrief(date=2026-11-16)]   # not paused, now ≥ 07:30 local, no briefs/2026/11/2026-11-16.md
  rules.compose_daily(snapshot, today) → (Brief, ChangeSet)
     followups: none (no earlier brief) ; steps: Focus Pursuits' open Next Steps, held-due first, then most recent activity,
     one per Pursuit, ≤3 ; decision: quietest Neglected (Agent-tooling newsletter, 28 clock days)
  ⇒ briefs/2026/11/2026-11-16.md  status: sending      # written BEFORE sending: at most once
  channel.Telegram.send(messages.render(brief, TELEGRAM))    # subprocess: hermes send --to telegram --json "<text>"
     → Delivered(chat="telegram:5550001", message_ids=("8812",))   # the chat it resolved the target to, and the ids
  ⇒ briefs/...-16.md status: sent, message_ids: [8812], chat: telegram:5550001, sent_at
→ TickReport(sent=["brief 2026-11-16"], collected=3, filed=3)
```

`hermes send --json` exits 0 when the message was sent (or skipped), 1 when the send tool failed, which is arrival unknown, and 2 on a usage error, which raises. With `--json` it prints the result as indented JSON. Under the Docker install, container start-up lines come first on the same stdout, so `channel.Telegram.send` reads only the last top-level JSON object: the last line that starts with `{`, through the end. A timeout of the `hermes` subprocess also counts as arrival unknown. The send phase holds `.atlas/send.lock` from planning the brief to recording its outcome, so a `sending` attempt that a later tick finds was left by a crash, and counts as arrival unknown.

If the PC was down at 07:30, the first tick after boot that day sends it (`now ≥ time` and same local date), even at 23:40. A tick after midnight sees a new date and never sends yesterday's. The text ends with one line: `Reply "1 prompt", "2 prompt" or "3 prompt" for a paste-in prompt.` (D Q2). If `hermes send` fails after Telegram may have accepted the message, the attempt is `ARRIVAL_UNKNOWN` and a later tick that day sends it once more, never a third time; a reply to either copy counts (D Q13).

### 8. Resume prompt, then resume in Codex

The owner replies to the brief `1 prompt`.

```python
note_inbound(event)   → remembers reply_to=8812 in telegram:5550001 → brief 2026-11-16
claim(...)            → OwnerMessage(text="1 prompt", reply_to=SentRef(brief=2026-11-16))
atlas.handle(msg)
  converse.interpret → [Ask(ResumePromptQ(brief=2026-11-16, item=1))]      # plain-word grammar fast path, no LLM
  atlas.read(ResumePromptQ(...)) → Answer(text=prompt, statements=[...])
     messages.resume_prompt(pursuit, snapshot)    # deterministic from the Resume Note: Where, Left off, Decided,
                                                   # Open question, Sessions to reopen (with native ids and folders), Next Step
→ Reply(prompt)   # sent by Hermes as its own message; the owner copies it into Codex
```

### 9. Plain-word replies

Reply `1 done, 10/10 pass with one worker. park the newsletter`.

```python
converse.interpret(msg, snapshot, brief=2026-11-16)
  → [MarkStep(step=s-…payments, done, detail="10/10 pass with one worker"), SetStatus("Agent-tooling newsletter", parked)]
rules.decide(..., Owner(via="telegram", note=nt-…))   # done by the owner's word; parked leaves Focus (not in it anyway)
⇒ pursuits/payments-ci-flakes.md (step done [F your Note · nt-…]), pursuits/agent-tooling-newsletter.md (status parked,
   raise clock starts), notes file append
→ Reply("Payments CI flakes: Step done.\nAgent-tooling newsletter: parked.")
```

The Note and both changes are one commit (`rules.converse`), so a redelivered Telegram message (same `platform_ref`) finds its Note, changes nothing and gets the same reply. `claim` looks for that Note before it applies the window, so a redelivery gets the same reply after the window and after a gateway restart, and it never moves the window. Until the model tier of `converse` exists, a claimed message the grammar cannot read, with no words at all, or whose words name several Pursuits, is not claimed after all: `handle` writes nothing and returns None, and Hermes's agent gets the message with Atlas's tools. `/atlas` words it cannot read get no reply. Once `handle` has read the words, the message stays Atlas's: a commit that fails partway is rolled forward and answered with the Note's reply, and one that wrote nothing is answered with D Q8's failure line. The Note is found by its id (`rules.noted`), so `/atlas` from the terminal, which has no platform id, gets its reply too. A transaction that cannot be applied yet is part of every Snapshot, so a redelivery finds its Note even then.

`/atlas …` passes the checks a plain message does about who and where: the owner, in the Channel's chat. It carries its `platform_ref`: a redelivered `/atlas` gets the Note's reply before anything else, so it changes nothing and never moves the window. A claimed `/atlas` opens the window.

A reply names a brief only by the chat and message id the brief was sent as (`rules.replied_to`). Atlas's answers have no message id on record: the gateway sends them (ADR 0007), and Hermes v0.23.0 tells no hook the id. Telegram's `reply_to_text` is no substitute. It holds the part of the replied-to message the owner selected, from any author, and Hermes v0.23.0 sets neither `reply_to_is_own_message` nor `reply_to_author_id` for Telegram. So a reply to an answer is Atlas's inside the window or with the prefix, like any other message, and numbers in it name the latest brief the owner was sent. A brief attempt written before Atlas kept its chat names no chat, because the home channel may have moved since.

If the owner replies before the tick has recorded the brief's message id (Telegram delivers the brief, then `hermes send` exits, then the tick commits `sent`), `claim` waits while a brief attempt is `sending` and under two minutes old (`rules.in_flight`), at most 10 seconds, and then looks again.

The Channel's chat is where `hermes send --to telegram` lands. Hermes v0.23.0 sends it to the home channel's chat without the topic `/sethome` was run in (`tools/send_message_tool.py`, `_home_chat_id`), so in a forum the brief arrives in the General topic. Hermes gives a General-topic message thread `1` and sends to General with no thread, so Atlas writes a chat's General topic as the chat itself, `telegram:<chat>`.

Evening: the owner does Step 2 in Claude Code via T3 Code, never replies. The next tick:

```python
sources.collect → CollectedSession(harness=claude_code, interface="T3 Code", native=…, owner_turns=[...])
librarian.file(new_id, snapshot)          # ledger filing (approach C) against Filing rules + cards
  → Filing(pursuits=["Billing export service"], advances=True,
           step_outcome=StepSeen(step=s-…billing, done, evidence=cc-…, quote checked))
⇒ pursuit: Session listed, Resume Note lines replaced, step done "seen" [F Claude Code via T3 Code · Mon 21:40 · cc-…],
   new Next Step [I · cc-…], History "Atlas marked the Step done from your Session" (Atlas line → changed mark)
```

What waits for filing (`filing.queue`): once backfill has built the Pursuits, every held Session that no Pursuit lists and no card marks as a one-off, and every listed Session whose last turn is newer than its membership (it grew while it ran). A grown Session stays in the Pursuits that list it; the model's answer only updates those. A tick files at most `filing.BUDGET` Sessions, oldest last turn first, each in its own commit. A Session the model gives no usable answer for (a field missing, of the wrong type or over its maximum in `librarian.FIELD_MAX`, an unknown Pursuit or Kind, a new Pursuit named as one there is) is skipped and waits out a back-off kept in `.atlas/pending/filing.json` (`filing.RETRY`: 1 hour, doubling, at most a day; a Session that grows waits no longer); the others are filed in the same tick. Filing stops for the tick only when a model call itself fails. The filing answer also gives the Session's card, so a new one-off has a card to mark. A Step is seen done only when the answer quotes a turn the Pursuit had not read before (after the Session's previous filing); that turn's time is the "Seen in …" time. A Session of turns in an exchange with Atlas neither advances a Pursuit nor ends its Step nor changes its Kind. Every message Atlas sends fits one Telegram message (`messages.MESSAGE_MAX`, 4000 UTF-16 units): a longer one has its longest lines cut, each ending in "…"; a brief whose send fails in any way is kept as arrival unknown and retried once (D Q13). The tick releases its lease before the send phase.

---

## Tuesday

### 10. Follow-up with a Step seen done

```python
rules.compose_daily(snapshot, Tue)
  prev = last sent brief before today = 2026-11-16
  followups = [Followup(payments, DONE_BY_WORD), Followup(billing, DONE_SEEN, evidence=cc-…),
               Followup(greenhouse, NO_SIGN, offers=1)]            # offers counted from briefs/*.md, the single source
  steps: greenhouse (2nd offer), reading tracker, billing   # oldest Step first, then most recent work
  decision: Bike route planner
→ Telegram text exactly as the tutorial
```

### 11. Hold: "3 remind me friday. drop the bike planner"

```python
converse.interpret → [HoldStep(step=s-…billing, until=2026-11-20), SetStatus("Bike route planner", dropped)]
rules.decide: hold is neither skip nor offer; dropped is an ending → Owner authority required (it is) ; leaves Focus
⇒ pursuit files ; Reply("Billing export service: I'll suggest this Step again on Friday.\nBike route planner: dropped. ...")
```

Evening reply `2 done. tablet app, nothing fancy` → `[MarkStep(done), AddDecision(pursuit, "tablet app")]` ⇒ Decided line `[F your Note · nt-…]`.

### 12. An agent in Claude Code: atlas_read, atlas_note, atlas_update

Claude Code was given the server once with `claude mcp add atlas -- hermes atlas mcp`.

```python
# MCP stdio server process: hermes atlas mcp → agentapi.serve_mcp()   (Atlas.open() in that process; no gateway needed)
agentapi.call("atlas_read", {"pursuit": "greenhouse logger"}, caller=Caller(harness="claude_code", cwd=~/home/greenhouse))
  atlas.read(ResumeQ("greenhouse logger")) → Answer(resume note statements) → rendered for MCP: text + JSON citations

agentapi.call("atlas_note", {"text": "Decided to log every 5 minutes ...", "pursuit": "Greenhouse logger"}, caller)
  atlas.act(AddNote(text, about="Greenhouse logger"), caller.authority(None))   # Agent(..., instruction=None): Notes only
  ⇒ notes/2026/11/2026-11-17-claude-code.md append nt-…   (agents may add Notes unasked)
  the librarian files the Note on the next tick like any Evidence ⇒ Decided line [F Note from Claude Code · nt-…]

agentapi.call("atlas_update", {"pursuit": "Greenhouse logger",
     "change": {"resume_note": {"remove_open_question": "card filling up",
                                "add_decided": "At 5-minute logging the card lasts about 3 years"}},
     "owner_words": "tell atlas the open question is settled: at 5-minute logging the card lasts about 3 years"}, caller)
  caller.authority(owner_words)
     sources.bind_caller(caller)        → BoundSession(claude_code, <ns>, <sessionId>)   # ~/.claude/sessions/<ppid>.json
     # Codex instead: caller.call_meta["threadId"] → ~/.codex/sessions/…/rollout-*-<threadId>.jsonl  (ADR 0010, G2)
     sources.find_instruction(bound, owner_words)
                                        → Instruction(...)   # the prompt is already in that Session's .jsonl
  atlas.act(EditResumeNote(...), Agent("claude_code", bound, instruction))
  rules.authorize: EditResumeNote.allowed ∋ AGENT_TOLD, instruction present → apply now
     ⇒ pursuit file + History "Changed by Claude Code on your instruction: \"tell atlas the open question is settled…\" [F Claude Code · Tue 21:24 · cc-…]"
→ "Updated the Resume Note of Greenhouse logger. Removed the open question ... and added that decision."
```

If the words are not in an owner-typed message of the calling Session, or the Harness offers no binding (Devin in v1; Codex when the call carries no `_meta.threadId` or its version is unverified, ADR 0010), `atlas_update` commits a `REFUSED_UPDATE` Note quoting the owner's words with the change as `pending`, and tells the agent so. Atlas's next answer to the owner, in any Channel, ends with `Claude Code asked to change Greenhouse logger on your words "…". Reply "apply" to make it.`; `apply` → `ApplyNote` → the change goes through `rules.decide` under Owner authority (D Q12). Nothing is applied and later reverted. An agent may end a Pursuit only when the found words name the ending ("done", "close it", "drop it"), D Q11.

---

## Wednesday

### 13. A short brief (two Steps)

`rules.compose_daily` finds only two Focus Pursuits with an open, unheld Step and sends two; it never pads. Billing is held until Friday. Greenhouse logger has no open Next Step: Tuesday evening's Session settled its open question, and `librarian.file` writes a Next Step only when it can name one small, unblocked action (`NextStep | None`). It writes one on Wednesday, which is why the greenhouse Step returns on Thursday. Follow-up line `· Billing export service: held until Friday, as you asked. [F]`. Decision slot: Home server backups (first ask), recorded in `briefs/…-18.md` `decision: {pursuit, ask: 1}`.

### 14. Kind change on its own

```python
librarian.file(new_id) → Filing(..., kind_change=KindChange("idea"→"project", basis=I · [cx-…] "own repository now"))
rules.decide(..., Librarian(evidence=[cx-…]))     # Librarian authority is internal; cannot end, can change Kind
⇒ pursuit kind: project, History "Changed Kind from idea to project: it has its own repository now [I · cx-…]"
Board: Card(kind="project (an idea until Wed 18 Nov)", marks={changed})       # "until" derived from History
```

### 15. The split applied by itself

The Filing rules section written on Sunday is part of `librarian.file`'s input. Two password-rotation Sessions → `Filing(pursuits=["Billing export: staging deploy"])` with the rule cited. Review later lists "Filed 2 Sessions under ... by Sunday's split".

### 16. Web-chat exports dropped into the inbox

```python
atlas.tick(now)
  sources.collect → sources.webchat.import_inbox(~/atlas/inbox)        # UNVERIFIED parser seam, version tolerant
    chatgpt-export.zip → 143 CollectedSession(harness=chatgpt, key=(chatgpt, conversation id)); group_chats.json ignored
    claude-export.zip  → 38 CollectedSession(harness=claude_ai, ...)
  ⇒ evidence/chatgpt/... , evidence/claude_ai/...
  sources.release(outcomes) after that commit → both zips deleted (every conversation kept; group_chats.json skipped);
     a file that failed in whole or part stays and is reported (D Q5). Board and Review say "Imported 143 ChatGPT
     Sessions and deleted the export".
  librarian.file(...) → most: OneOff(area=...) ; knee Sessions → NewPursuit("Knee rehab plan", kind=problem, area=health)
  rules.decide(NewPursuit) ⇒ pursuits/knee-rehab-plan.md, proposals: [FocusAdd(reason=I)]
  rules.proposals(snapshot) also checks: a Focus slot is free / new and worth pushing
```

What is built (#15): `sources.webchat` reads each settled `.zip` in the record's `inbox/` (unchanged for a minute, no link, regular file) in memory, only its `conversations.json` or `conversations-<n>.json` shards, with caps on the archive (2 GiB), its directory (100,000 entries and 8 MiB, read from the end record before `zipfile` reads it), each shard (256 MiB inflated, checked as it inflates), what json would build from the shards (1 GiB, bounded from their bytes, brackets, commas and colons as they inflate, before json builds anything) and the conversations (20,000). A time before 2015 or more than a day past the tick is no time; a conversation the record cannot hold (no time left, text it cannot write) is broken, and the file's other conversations and the other files go on. A tick reads at most 20 files. Each conversation is a Session whose raw copy is one line for the conversation and one per message of its branch; a later export adds the messages the copy lacks. The tick puts at most `IMPORT_BUDGET` (500) web-chat Sessions, 50 to a commit, then deletes each file whose every conversation the record holds or Forget keeps out, and notes each file in `.atlas/imports.json` (Harness, count, deleted or kept, failure type; no name or text). A kept file is read again only once it changes. `filing.queue` files a web-chat Session after the others, so an export of 2,000 conversations (4 ticks to import, 100 ticks of `filing.BUDGET` to file) never holds back today's Sessions or the brief. A Pursuit filing creates starts active or parked by backfill's rule (`rules.starting_status`: parked when its Session is more than 30 days old), so an imported years-old chat never starts a Neglected question. A Pursuit Atlas created in the last 7 days, untouched by the owner, active and outside Focus, that 3 Sessions of the last 30 days worked on, gets one `FocusProposal(add)` (`filing._offered`). The Settings line of a web chat says what its last export gave.

### 17. Focus proposal on the Board

```python
atlas.read(BoardQ()) → Card("Knee rehab plan", marks={new, proposal: "add to Focus [I]"})
# owner clicks the star:
atlas.act(SetFocus(knee, True), Owner(via="board")) ⇒ focus: true; proposal cleared (answered)
```

### 18. Journal correction from late Evidence

```python
librarian.file(...) also returns affected_days={2026-11-16}
rebuild 2026-11-16 as the tick writes a day:
  activetime.day(sessions touching 2026-11-16, gap=15min) → DayTime(total=3h55, areas, basis)
  librarian.journal_lines(snapshot, day, rules.uncited(...))   # LLM only for new Evidence; existing lines kept
  correction line derived from the evidence diff: "Corrected Wed 18 Nov: added ChatGPT 19:10 (40 min) when your ChatGPT export arrived."
⇒ journal/2026-11-16.md
```

What is built (#15): the tick passes the Sessions it filed to the Journal step. After writing the days since the newest one, it rewrites each earlier day the Journal has that those Sessions worked on (`corrections.late_days`) through the ChangeSet, keeping its lines, with `corrections.line` added: the day of the correction, then for each Session the day's Active Time now counts and its file did not, `added <Harness> <first time that day> (<the time it adds>)`, and ` when your <Harness> export arrived` for a web chat.

---

## Thursday

### 19. Ending proposal in the follow-up

```python
# Wednesday evening tick:
librarian.file(cx-…) → Filing(step_outcome=StepSeen(done), ending=EndingProposal(done, basis=I · cx-…))
rules.decide ⇒ pursuit proposals: [{ending: done, since: 2026-11-18, raised_in: followup}]
# Thursday 07:30:
rules.compose_daily → Followup(payments, DONE_SEEN, ending_proposal=True)   # because yesterday's Step finished it
render: 'This Pursuit looks finished. [I] Reply "close it" to mark it done.'
⇒ briefs/…-19.md items include ending_asked: payments   # never repeated in later briefs (rule reads this)
```

### 20. Neglected: second ask

`compose_daily` sees Home server backups asked in the previous brief, unanswered, not held → asks again, `decision: {ask: 2}`. If the next brief finds it still unanswered, `compose_daily` emits `SetStatus(parked, Librarian(reason=silence))` with the history line "Parked after two unanswered asks; reply continue to undo", and the slot moves to the next quietest.

The counting lives in `atlas/neglect.py`. A question is unanswered when its Pursuit is still Neglected and its History has no line of the owner's since the brief that asked it was sent: "continue", a park, a drop or a hold each answer it. A held question is neither asked nor counted while held, and is asked as a first ask once the hold ends. "continue" also undoes a park by silence: the Pursuit is active again and its quiet days count from that day. A follow-up raises only a `done` ending, and only one proposed since the brief it follows up; a `dropped` ending waits for the Review, as every ending not raised in a follow-up does.

Only silence parks. A bare `continue`, `park` or `drop` (with "it" or "that", any case) replying to a brief answers that brief's question, as the named form does; replying to nothing, it answers the latest brief's question, unless Atlas's last answer to the owner named another Pursuit. Words Atlas cannot read that reply to the asking brief or name its Pursuit mark the question `heard` on the latest brief: the next brief asks it again with the same ask number, and nothing is parked on that brief's strength. A day whose brief time a Pause covers is no silence either: the question is asked again as it was. After a park by silence, a bare `continue` to a brief with no question (sent since the park) or to nothing undoes it when it is the only one; with several, Atlas asks which. `remind me <weekday>` replying to the brief, or `remind me <weekday> about <words>`, holds the question until that day.

Reply `close it. continue the backups`:

```python
converse.interpret → [SetStatus(payments, done), Continue(backups)]
rules.decide: done = ending, Owner ✓ ; leaves Focus ; Continue sets continued: 2026-11-19 (quiet clock restarts)
→ Reply("Payments CI flakes: done. It leaves Focus.\nHome server backups: stays active. Its quiet days count from today.")
```

### 21. "atlas, what have I neglected?"

```python
claim → atlas.handle(msg) → converse.interpret → [Ask(NeglectedQ())]
atlas.read(NeglectedQ()) → Answer(statements=[ "Nothing is Neglected today. [F]",
     recent decisions from Pursuit Histories in the last 7 days ...])
```

### 22. Find the conversation

```python
atlas.read(FindQ("GPX elevation smoothing working"))
  snapshot.find(text) → index FTS over session.md + card.md + statements → [Hit(cx-…, score)]
  librarian.pick_answer(hits[:8], question)   # reads the top transcripts, returns a Fact with a checked quote
→ Answer([F "Codex, Sun 25 Oct 20:14, ~/code/bike-routes" · cx-…, F "Pursuit: Bike route planner (dropped)",
          F "You used a 30-point rolling median, then removed spikes over 8 m." · cx-…])
```

If the work takes longer than about 25 s, `claim` replies `Looking, I'll send it here.` and a worker thread in the gateway sends the answer as its own message with `hermes send`, or `I couldn't answer that: …` on failure (D Q8).

The index is `.atlas/index.sqlite`, one full-text row per Session: its transcript, its card and the Resume Note lines of the Pursuits that list it, as the record holds them, secrets masked. `find` builds it again first whenever `.atlas/HEAD` moved since it was built, and `hermes atlas reindex` builds it at once, so deleting it loses nothing. The model reads the three best hits. The first line is the Session's Harness, the time of the turn the quote was found in (its start when no quote was found) and its folder; the Pursuit line names the Pursuit that lists the Session, whatever its Status. A question the model cannot answer is answered `I couldn't answer that: <reason>.`, kept as the Note's reply like any answer.

### 23. "what was I doing on 14 August?" from Codex

```python
agentapi.call("atlas_read", {"day": "2026-08-14"}, caller=Caller(harness="codex", ...))
atlas.read(DayQ(2026-08-14)) → Answer(journal day statements, DayTime, coverage=Coverage(machine_only=True))
render for MCP: text block as in the tutorial + "Covers AI Sessions on ubuntu-pc only..." [F]
```

### 24. "atlas, send Dana the billing export status"

```python
converse.interpret → [OutsideAction(kind="send", to="Dana", about="billing export")]   # no action reaches others
rules.converse: authorize → OUTSIDE_WORLD → ask.refusal: the Pursuit the words name most closely, its Left off lines
→ Reply("I can't send anything to other people. ... Here is the status to forward yourself: \"...\" [F]")
```

The status to forward names the Pursuit in the owner's own words ("Billing export"), and the words name the Pursuit whose name holds them with the fewest words besides, so "billing export" names Billing export service rather than Billing export: staging deploy. The owner wrote `atlas, send Dana …`, so Atlas claims it and refuses. Without the prefix and outside the window it would go to Hermes's own agent, and whether Dana hears anything would be Hermes's decision; Atlas's limits bind Atlas, not Hermes (D Q1, D S2).

---

## Friday

### 25. The held Step comes back first

`compose_daily(Fri)`: billing step `held_until=2026-11-20 ≤ today` → first, counts as its first offer. Followups: reading tracker and greenhouse `NO_SIGN`, "I'll offer each once more": the owner acted on none of Thursday's Steps, so the Steps with no sign rest a day and are not offered today (`rules.ACTED`); after a day the owner acted on one, a Step with no sign is offered again at once, as on Tuesday; knee `NOT_VISIBLE_YET` because the Pursuit's last Session is from a late Harness (chatgpt in `settings.late_harnesses`). Decision slot empty: `rules.neglected(...)` returns none.

### 26. "skip 2, not today"

`[MarkStep(knee step, skipped, by=word)]` ⇒ skip counts toward the three-in-a-row Focus removal rule. Reply `Knee rehab plan: Step skipped.` When the skip leaves the Pursuit with no next Step, the same action comes back as a new Step (same text, its own id, created at the skip, no offers yet), so the next brief offers it again (scene 34). A Step skipped by silence does not come back.

### 27. Read the record with Hermes stopped

No Atlas code runs. `ls ~/atlas` shows `README.md briefs/ evidence/ inbox/ journal/ notes/ pauses.md profile.md pursuits/ reviews/ settings.md forgotten.md` (`.atlas/` hidden). `less journal/2026-11-16.md` shows the day as in the tutorial, its citations shortened as the tutorial prints them. The marks are explained in `README.md`, so the record makes sense without Atlas's code (ADR 0002).

### 28. Profile correction

```python
converse.interpret → [CorrectProfile(entry="You skip Steps that need hardware.",
                                     replacement="You do Greenhouse logger Steps in the evening, not in the morning.")]
rules.decide ⇒ profile.md entry replaced, basis [F your Note · Fri 13:02 · nt-…]
→ Reply('Changed: "You do Greenhouse logger Steps in the evening, not in the morning." [F your Note, Fri 13:02]')
```

### 29. A hand edit

The owner opens `profile.md` in an editor and changes a line.

```python
# next Atlas.open()/tick/commit in any process:
record.reconcile()
  stat cache says profile.md changed → seal check fails (front matter `sealed:` ≠ sha256 of the rest)
  ⇒ ONE commit: restore profile.md from .atlas/last-written/profile.md
              + notes/2026/11/2026-11-20-hand-edits.md entry nt-… {path: profile.md, diff, edited text}
→ HandEdit(path, note=nt-…); the Note appears in snapshot.pending_applies() until applied (offered once in the next answer, listed in the Review)
# the owner's next message to Atlas gets a suffix; otherwise the Review lists it:
"You edited profile.md by hand. I put it back and kept your text as a Note. Reply \"apply\" to make the change."
# reply "apply":
converse.interpret → [ApplyNote(nt-…)] → librarian.edit_to_commands(diff) → [CorrectProfile(...)] → rules.decide(Owner)
```

The same holds for any record file: a deleted file is restored, a file Atlas did not write is left as it is, and a link is never followed; editor swap/backup files (`.*`, `*~`, `*.swp`) are ignored.

### 30. Forget, with confirmation

`atlas, forget the Codex Session from Thu 19 Nov 14:02. I pasted a password in it`

```python
converse.interpret → forget.interpret → [RequestForget(selector="codex 2026-11-19T14:02:00+01:00")]
forget.converse(snapshot, msg, RequestForget) → the Codex Sessions that started in that minute ([cx-…]),
    and what goes with them, read from the record: every Pursuit statement, Step, History line and proposal
    citing cx-…, its memberships, a Pursuit made from it alone (D Q3), Journal lines citing it, Profile entries
    drawn from it, and the Harness's own copy named from RawPart.source (D Q3)
⇒ .atlas/pending/forget.json (ForgetPlan: evidence, asked_at)   # machinery; expires after 1 h or on any other reply
→ Reply("Forgetting removes these for good:\n- the Session itself\n- 1 Fact in the Resume Note of ...\n- 1 line in Thursday's Journal\n"
        "Not removed: the Session in Codex itself (~/.codex/sessions/2026/11/19/…).\nDelete it there if the password must go.\n"
        "Reply \"forget\" to go ahead.")

# reply "forget" (exact word, trimmed, case-insensitive; no LLM):
converse.interpret → forget.interpret → [ConfirmForget()]
forget.execute (what goes, read again from the record) → ChangeSet: RemoveEvidence(cx-…), rewritten pursuit + journal day (Active Time recomputed without it),
                          forgotten.md += "Fri 20 Nov 2026 13:05: a Session was forgotten at your request.", skip key sha256("codex:<thread id>")
                          and one for each Session folded into it, plan cleared
record.commit: removes evidence dir, mirror copies replaced, cache entries tagged cx-… deleted, index rows deleted,
               txn staging deleted after apply
→ Reply("Forgotten.")
```

The Note that holds the owner's own request ("I pasted a password in it") contains no secret and stays. The password itself was already shown as `[secret: password]` in session.md, the Resume Note and the Journal, because `record._codec` scrubs every derived text (D Q4); only `raw/` held it, and Forget removes that. The Codex rollout in `~/.codex` is the Harness's own file: Atlas names it in the confirmation, never touches it, and never re-collects it, because the one-way skip key matches (D Q14). There is no "unforget".

### 31. Board drag to Parked

```python
# PATCH /api/plugins/atlas/pursuits/{id} {status: "parked"}
atlas.act(SetStatus(staging_deploy, parked), Owner(via="board", note=nt-…))
  ⇒ board Note + pursuit status parked, leaves Focus, History line ; the 90-day raise clock starts
```

### 32. Pause

`atlas, pause until sunday morning, we're at my parents'`

```python
converse.interpret → [PauseCmd(until=Sun 22 Nov 07:00, said="until sunday morning, we're at my parents'")]
                                                             # no end given → until=None → +7 days, stated in the reply
rules.converse ⇒ pauses.md += {start: 2026-11-20T16:40+01:00, end: 2026-11-22T07:00+01:00, said: "..."}   # record-layout.md
→ Reply("Paused until Sun 22 Nov 07:00. No Daily Brief on Saturday, and no Pursuit becomes Neglected while Atlas is paused. ...")
```

Nothing touches the Hermes cron job. A resumed Hermes job would fire its elapsed slot (hermes-how.md), so the tick itself checks the Pause: `schedule.due` returns nothing while paused, and `rules.compose_daily` checks `rules.paused` again when it plans the send. `atlas.pausing` reads the end: after "until", a weekday, "tomorrow" or a date, each with an optional part of the day ("morning" is 07:00, "evening" 18:00) or a time, or a time alone; after "for", days or weeks. A day with no time ends at its start, so that day's brief comes. A bare hour ("till 5") already gone today is the evening hour when that is still ahead. An end Atlas cannot read leaves the message to Hermes's agent, and so do words after "pause" that end in a question mark or open with a word of `converse.TAKE_BACK` ("no", "not", "don't", "never mind", "actually", "wait", "cancel"). A Pause that ends after a day's brief time sends that day's brief when it ends (`pausing.brief_from`, which `schedule.due` uses), and the reply names that moment. An unreadable `pauses.md`, or a Pause in it longer than `pausing.LONGEST` (366 days), reads as no Pause, with one log line. "pause" while paused ends the running Pause at that minute and starts the new one, so the record keeps both sets of words. "I'm back", "back", "unpause", "end the pause" or "resume" ends the running Pause at that minute (`EndPause`); the reply says when the next brief comes, and the brief is due again at once if its time has passed today.

---

## Weekend

### 33. Saturday silence

```python
atlas.tick(Sat 07:30) → schedule.due(...) = []  because rules.paused(pauses, now) ; collection and filing continue
rules.clock_days(...) never counts Saturday: a day counts on a clock only if no Pause covered that day's brief time
```

### 34. Sunday: follow-up from Friday

`compose_daily(Sun)`: `prev = last sent brief` = Friday (Saturday has no file) → header `YESTERDAY'S STEPS (from Friday)`. Billing `DONE_SEEN` (Claude Code Fri 09:15), knee `SKIPPED_BY_WORD`. Steps: knee (the new Step the skip left, scene 26; Resume line names the ChatGPT Session) and reading tracker. A Resume Note with no `where`, as a web chat's, resumes in its first Session to reopen: `Resume: ChatGPT, Session "knee weeks 1 and 2" (Mon 16 Nov). [F]`.

### 35. claude.ai resume that needs the owner's word

The owner replies `1 prompt`, pastes into claude.ai, then replies `1 done`. → `MarkStep(done, by=word)`. When the next claude.ai export arrives, `librarian.file` sees the Session. The Step is already done, so the Session only adds Evidence, and the Journal day is corrected.

### 36. The weekly Review at 18:00

```python
schedule.due(snapshot, Sun 18:00) → [Review(week=2026-W47)]     # no reviews/2026/2026-W47.md; not paused
rules.compose_review(snapshot, week) → (Review, ChangeSet)
  FINISHED/DECIDED/CHANGED  ← Pursuit History lines and Journal "Decided" lines in the week (already marked)
  WHAT I DID ON MY OWN      ← History lines with actor Atlas in the week, plus Profile entries Atlas added this week and still as written (G3)
  FOCUS                     ← open FocusAdd/FocusRemove proposals (three skipped Steps in a row → FocusRemove)
  ENDINGS / REOPEN          ← proposals not raised in a follow-up
  PARKED                    ← rules.parked_to_raise(): quiet longest, ≥90 clock days since max(last Session, last raised); ≤1
  WAITING FOR "apply"        ← snap.pending_applies(): hand edits and refused agent updates (D Q12)
  IMPORTS                    ← export files imported and deleted, or kept after a failure (D Q5)
  TIME PROPOSAL              ← rules.time_proposal: on ≥10 of the last 14 unpaused days with Active Time, the first start is ≥60 min from brief_at → propose the median, rounded down to :15; not again for 4 weeks after "keep it" or silence (G4). None in the tutorial's week.
  # the Review lists all of Atlas's own filing whatever the Board's seen marks say (D Q17)
⇒ reviews/2026/2026-W47.md sending → send → sent ; parked Pursuit's raised_at recorded in that review file
```

Replies to the Review (`add backups`, `keep parked`, `continue`) are claimed like brief replies (`reply_to` → review). "Keep parked" and silence both restart the 90 days, because the review file records the raise.

### 37. "atlas, how long did I work this week, by Area?"

```python
atlas.read(PeriodTimeQ(start=Mon 16, end=Sun 22, by="area"))
  sums journal/*.md DayTime blocks for finished days + activetime.day(today, live) for today
→ Answer(table of counted Facts, Count(rule="active_time"), rendered "[F]" (D Q9),
          coverage=Coverage(late=["claude.ai: today's Session missing until the next export"], machine_only=True))
```

A day whose Journal file is not written yet is counted live like today. Areas run from most time to least, and so do the Pursuits in each, with a one-off Session's time last. A row wraps under its Pursuits at 80 characters.

### 38. Resume a Pursuit parked in August

`atlas, where did I leave off on the keyboard firmware?` → `atlas.read(ResumeQ("keyboard firmware"))`; the answer ends `Reply "prompt" for a paste-in prompt.`; the bare reply `prompt` right after it → `ResumePromptQ("keyboard firmware")` (D Q2). The answer's Note is `about` the Pursuit, and a bare `prompt` reads it only when that Note is the owner's latest exchange with Atlas. Reading does not count as work: `read` never commits, except the conversation Note, and Notes from Atlas conversations never advance a Pursuit (W Q14).

### 39. Auto-reactivation

```python
tick → collect Codex "qmk build" Session → librarian.file → Filing(pursuits=["Keyboard firmware"], advances=True)
rules.decide(..., Librarian(evidence=[cx-…])):
   parked + advances → status active (outside Focus), quiet clock from that Session, History "Active again: your Codex
   Session worked on it [F Codex · … · cx-…]"  → Board changed mark
   done/dropped + advances → stays, proposals += Reopen, Board mark + Review
```

---

## Scenes this design cannot serve as written

None. The three scenes the draft served differently are now what the tutorial shows (brief, D Q1, D Q2, D Q12): `1 prompt` replies instead of a button, an "atlas" prefix or Atlas's own chat for un-addressed lines, and refused agent updates kept as applyable Notes. "Codex starts the first test run about 40 seconds after" is the owner's own speed; Atlas only guarantees the prompt arrives as one message.
