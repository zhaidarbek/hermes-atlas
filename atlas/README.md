# Atlas

Atlas is a Hermes plugin that keeps track of what you are working on across your AI coding tools. It reads the Sessions that Claude Code, Codex, Devin, Hermes and T3 Code already keep on your machine. It groups them into Pursuits, the projects and ideas you started, and writes each Pursuit's state into a folder of Markdown files. Each morning it sends you a Daily Brief in Telegram with the next Step for the Pursuits you starred.

[A week with Atlas](https://github.com/zhaidarbek/hermes-atlas/blob/main/docs/tutorial/a-week-with-atlas.md) walks through every scene Atlas is built to pass. [What works in 0.1.0](#what-works-in-010) says where to find the scenes this version passes.

[The demo video](docs/recordings/demo-week.mp4) replays that week from the proof harness, on invented data and a fake Channel; `cd tests && python -m harness.demo` makes it again.

## Install

Atlas needs Hermes v0.23.0 or later, on Linux. The Daily Brief and replies need a Telegram bot paired in Hermes and a Telegram home channel (send `/sethome` to your bot). The scheduled tick needs the Hermes gateway running, because the gateway runs Hermes's cron jobs.

```
hermes plugins install atlas
hermes plugins enable atlas
hermes atlas setup
hermes atlas backfill
```

`hermes plugins install atlas` installs by catalog name, so it works once the Hermes plugin catalog lists Atlas. Until then, install from the repository: `hermes plugins install zhaidarbek/hermes-atlas#atlas` (Hermes v0.23.0 reads the part after `#` as the folder that holds the plugin). Hermes asks before it installs Atlas's three Python dependencies: `zstandard`, `mcp` and `pyyaml`.

`hermes atlas setup` creates the record folder, finds the Harness stores on this machine, checks the Telegram home channel and schedules the tick. It prints what it found and a list headed "What Atlas does on this machine". Run it again at any time. It keeps your settings and finds the cron job already there. On a first run:

- When it finds no Harness store, the report lists the Harnesses under "Not found" and leaves out the "Reads, never writes" line. Atlas then collects nothing.
- When Hermes has no Telegram home channel, the `Channel:` line says so and tells you to send `/sethome` and run setup again. No brief is delivered until then.
- When Hermes refuses to create the cron job, the report prints the `hermes cron create` command for you to run.
- When the record folder is on a network mount, setup stops and asks for a local folder.

`hermes atlas backfill` reads every Session in those stores, copies it into the record and asks your Hermes model to group the Sessions into Pursuits. It makes one model call for each Session, more calls to group the Sessions folder by folder, and one for each Session on each day of the Journal, so it can take several minutes on a long history. If it stops, run it again. It keeps the model answers it already has and goes on from there. When no model answers, it prints "No model answered, so no Pursuits yet. Set auxiliary.atlas_backfill in Hermes's config and run backfill again."

To check a checkout before you install it, run `hermes plugins validate /path/to/hermes-atlas/atlas --install-deps`. The command takes the plugin's folder, not its name.

To stop Atlas, remove the cron job (see "Adds a cron job" below) and run `hermes plugins disable atlas`. The record folder stays until you delete it.

## What Atlas does on your machine

Read this before you install. Each point names the code that does it.

- **Reads other tools' conversation stores.** Atlas reads, and never writes, these stores (`atlas/sources/_adapters.py`):

  | Tool | Default location | Override |
  |---|---|---|
  | Claude Code | `~/.claude/projects` | `CLAUDE_CONFIG_DIR` |
  | Codex | `~/.codex/sessions` and `~/.codex/archived_sessions` | `CODEX_HOME` |
  | Devin | `~/.local/share/devin/cli/sessions.db` | none |
  | Hermes | `~/.hermes/state.db` | `HERMES_HOME` |
  | T3 Code | `~/.t3/userdata/statev2.sqlite` | `T3CODE_HOME` |

  T3 Code is an Interface, an app that runs the other Harnesses. Atlas reads its database to learn which Session each T3 Code thread ran, and counts that Session once, under its Harness. Atlas opens each SQLite store read-only. It also reads the extra Harness folders you add in the Settings tab, which it keeps in the record's `settings.md` (`atlas/settings_rules.py`). It leaves out every Session that ran in a folder you list under exclusions.

  How Atlas treats links. It never opens a store file that is a symbolic link, and it skips a SQLite database whose `-wal` or `-shm` file has a second hard link. It skips a store folder that is itself a link, such as `~/.claude/projects` or `~/.codex/sessions`. It refuses a folder you add in the Settings tab when any part of its path is a link, both when you add it and each time it reads it. It does read through a Harness's default folder that is itself a link: if a dotfile tool makes `~/.codex` a link to another folder, Atlas reads the Sessions in that folder. The same holds for a folder named by one of the override variables above.

  Atlas opens only the stores above, and the web-chat export files you put in its inbox, described below. It never opens a credential file such as `~/.claude/.credentials.json`, `~/.codex/auth.json` or Hermes's `.env`. To check that SQLite opened the database file Atlas checked, Atlas reads its own process's `/proc/self/fd` and `/proc/self/maps`. The Hermes install scanner reports these reads as `proc_access` cautions. Atlas reads the `TZ` environment variable, `/etc/timezone` and the `/etc/localtime` link to learn this machine's time zone, and `/proc/mounts` to check that the record folder is on a local disk (`atlas/app.py`, `atlas/record/_txn.py`). The dashboard backend reads `plugins.entries.atlas.settings.record_dir` from Hermes's `config.yaml` and nothing else in that file (`atlas/board_api.py`).
- **Keeps a verbatim copy of every transcript.** Atlas writes everything it keeps to one folder, `~/atlas` by default (`atlas/record/`). Set `plugins.entries.atlas.settings.record_dir` to move it, or `ATLAS_RECORD` for one command. Setup sets the folder to mode 0700 and refuses a folder on a network mount. The folder holds a zstd-compressed copy of every Session transcript Atlas read, byte for byte, under `evidence/`. A password or key you pasted into a conversation is in that copy. Atlas masks the secrets it detects (API keys, tokens, passwords, private keys, credentials in URLs) in every file it derives from the copies: the readable transcript, Pursuits, the Journal, Notes, briefs and the search index (`atlas/privacy.py`, `atlas/record/_codec.py`). Detection is best effort, so a secret in a form it does not know stays unmasked in the derived files too. Forget removes a Session's copy. A web-chat conversation's copy holds each of its messages as the export holds it: for claude.ai that includes the text the export extracted from each file you attached (`attachments[].extracted_content`).
- **Reads and deletes the web-chat exports you drop in its inbox.** Once you add ChatGPT or claude.ai in the Settings tab, each tick reads the export `.zip` files you put directly in the record's `inbox/` folder, `~/atlas/inbox/` by default, up to 20 files and 500 conversations a tick (`atlas/sources/_webchat.py`). It reads a file only once it has not changed for a minute, and never one that is a link. It reads only the conversation files inside, in memory, never extracts the archive and never opens `group_chats.json`, the group chats other people wrote in. It refuses an archive larger than 2 GiB, with more than 100,000 entries, or whose conversation files would take more than about 1 GiB of memory to read, and it keeps a time only between 2015 and a day after the tick. After the commit that holds every conversation of a file, Atlas **deletes that file from the inbox**, and only while the name still holds the file it read. A file it cannot read in whole or in part stays in the inbox, the Settings tab says so, and Atlas reads it again only once it changes. Its import log, `.atlas/imports.json`, holds counts, dates and failure types, never a file name or conversation text.
- **Collects every 10 minutes.** From the first tick after setup, before any backfill, the tick copies new and grown Sessions into the record (`atlas/app.py`).
- **Sends conversation text to a model, unattended.** Atlas makes every model call through Hermes's `ctx.llm`, so the text goes to the model your Hermes is configured with (`atlas/librarian.py`). If that model is a cloud provider, the text leaves this machine and goes to that provider. Atlas masks detected secrets in the text first, with the same best-effort detection. The calls are:
  - `hermes atlas backfill` sends each Session's transcript, up to about 11,000 characters of it with its folder, branch and title, in one call per Session. It then sends, folder by folder, the summary the model wrote of each Session and that Session's last turn.
  - Once backfill has built your Pursuits, every tick files up to 20 new or grown Sessions with one model call each, without you typing anything. Each call sends that Session's transcript and the list of your Pursuits: names, Areas, Filing rules, where you left off and the open Step.
  - Every tick also writes the Journal for each day that has closed, with one call for each Session worked on that day. When a Session filed on that tick, such as one from a web-chat export, worked on a day the Journal already has, the tick writes that day again with the same calls and adds a "Corrected" line.
  - To write your Profile, backfill sends your Profile and the list of your Pursuits with each Session's harness and times, in one call, and every tick sends, once for each day it closes, your Profile, that day's Steps and Journal lines and the harness and times of the Sessions behind them. Both also send the Profile texts you corrected away, so the model is told not to add them back; Atlas leaves out any such text, and any text shaped like the marks `profile.md` uses, whatever the model answers.
  - When you ask Atlas to find a conversation, it sends your question and up to three matching transcripts. When you correct its filing or your Profile in plain words, it sends your message with the list of your Pursuits or your Profile. When you ask it to split a Pursuit, it also sends the summary and last turn of each of that Pursuit's Sessions.

  These calls cost whatever your model costs, and the tick makes them whether or not you are at the machine: up to 20 filing calls every 10 minutes while Sessions wait. Atlas keeps each model answer in the record's `.atlas/cache`, so the same input is not sent twice. You can route each kind of call to a different model, a local one for example, in Hermes's auxiliary-model settings. The task names are in the table below.
- **Lets a model's answer change the record.** The model's answers decide which Pursuit a Session is filed in and what Atlas writes about that Pursuit. Filing one Session can add a Pursuit, or rewrite one Pursuit's "left off" lines, mark its open Step done, set its next Step, change its Kind, and make a parked Pursuit active again (`atlas/filing.py`). The model reads the whole conversation, including text you did not write: a pasted web page, a file an agent read, an agent's own output. So text inside a conversation can influence what Atlas writes about that Pursuit and what the next Daily Brief says. The code enforces these bounds:
  - One filing rewrites the lines of at most one Pursuit, the one the Session is filed in, and writes that Session's summary card. It changes no settings and no Notes.
  - Filing never marks a Pursuit done or dropped. When a Session works on an ended Pursuit, Atlas adds a proposal to reopen it and leaves the Status alone.
  - A Session with no turn you typed can be filed, and can start a new Pursuit, but changes no line of a Pursuit that already exists.
  - Each text the model writes is put on one line, masked, and limited to 400 characters (80 for a name). Atlas throws away an answer with a longer text whole, and asks again later.
  - A line counts as a Fact only when the model quotes at least 15 characters that Atlas finds in that Session. Atlas marks every other line as an Inference. It marks a Step done only on such a quote.
  - Marking a Step done, changing the Kind, making a Pursuit active again and proposing to reopen one each add a History line to the Pursuit's file. A new "left off" line or next Step adds none.

  Atlas never runs text a model wrote as a shell command, and it has no tool that acts outside its record. It refuses a message, addressed to it by name, that asks it to send, tell, email or message someone.
- **Parks a Pursuit by itself in one case.** When an active Pursuit has gone quiet, the Daily Brief asks whether to continue, park or drop it. The question is asked at most twice. If the second ask also goes unanswered, Atlas parks that Pursuit when it composes the next Daily Brief and writes a History line saying so (`atlas/neglect.py`). A reply in words to the asking brief keeps the question open even when Atlas cannot read it as a command: the next brief repeats the same ask, and silence after that repeat parks the Pursuit. A brief that was not sent does not count as an ask, and one brief parks at most one Pursuit. Atlas never marks a Pursuit done or dropped by itself, and replying "continue" with the Pursuit's name makes it active again. The park shows on the Board as a changed card and is not announced in the brief.
- **Claims messages in your Telegram chat with Hermes.** Atlas registers two gateway hooks and the `/atlas` command (`atlas/channel.py`). It looks only at messages from the Telegram user who ran `/sethome`, in the home channel's chat. Of those, it claims a message that starts with the word "atlas", a reply to a Daily Brief, a message sent within 10 minutes of the last one it claimed, and `/atlas <words>`. A claimed message that Atlas can read does not reach Hermes's agent. Atlas answers it in the same chat and keeps your words and its answer as a Note in the record. A claimed message that Atlas cannot read goes on to Hermes's agent as if Atlas were not there. If you give Atlas a chat or topic of its own in the Settings tab, it claims every message you type there. An answer that takes longer than 25 seconds arrives as a separate message.
- **Sends Telegram messages unprompted.** Each morning, at 07:30 local time unless you change it, the tick sends the Daily Brief to your Hermes Telegram home channel by running `hermes send --to <channel> --json <text>` (`atlas/channel.py`, `atlas/schedule.py`). The brief names your Pursuits and Steps drawn from your Sessions, with detected secrets masked. Under each Step it prints a Resume line that names the Harness, this machine's host name, the folder the work is in (written from your home folder, such as `~/work/payments-api`) and the git branch (`atlas/messages.py`). For a Step last worked in a web chat, which has no folder, the Resume line names the Harness, the conversation's title and its day instead, so the title of a ChatGPT or claude.ai conversation goes to Telegram in the brief. If Hermes does not confirm delivery, a later tick sends the same brief once more that day. Atlas sends to that one chat, or to the chat you give it in the Settings tab. If your home channel is a group, everyone in the group sees the brief, including the host name, folders and branches in it. Atlas's answers in that chat carry the same facts: "where did I leave off" and the paste-in prompt print the host name, folder and branch, and a find answer prints the folder (`atlas/ask.py`).
- **Can be paused by message, and keeps working while paused.** Send "atlas, pause until sunday morning", "pause for 3 days" or just "pause" (a week), and "I'm back" to end it early (`atlas/pausing.py`, `atlas/converse.py`). A Pause stops the Daily Brief and the clocks that count quiet days, and nothing else: the tick still collects and files your Sessions, writes the Journal and makes the model calls described above, and the Hermes cron job keeps running. Atlas keeps each Pause in the record's `pauses.md` with the words you wrote after "pause", masked like every other derived file, and Forget does not remove them.
- **Lets your agents read and write your record.** Any agent you give the tools to, in Hermes or in a Harness through `hermes atlas mcp`, can read every Pursuit's Resume Note and every Journal day, across all Areas, as the record holds them, with detected secrets masked, and the one-off Sessions of a day from their summary cards (`atlas/agents.py`, `atlas/agentapi.py`). It cannot read a transcript, a file or a forgotten Session. It can add Notes unasked. Every text an agent sends is normalised (Unicode NFKC), cleared of invisible format characters, put on one line with every quotation mark as an apostrophe and every bracket as a round one, masked, and given "Atlas -" for "Atlas:" and "You -" for "You:". It is refused when it holds a control character or a character that reorders text, has a word that mixes Latin letters with another script's, or is longer than 400 characters. So it cannot pass for Atlas's own words in a brief, on the Board or in a reply (`atlas/untrusted.py`). An agent changes a Pursuit only under all of these conditions:
  - Atlas can tell which Session the call comes from. For Claude Code, `hermes atlas mcp` reads `~/.claude/sessions/<pid>.json` of the process that started it, and that process's `/proc/<pid>/stat` to check it is the process that wrote the file. For Codex, it uses the thread id Codex puts on each call, for Codex versions Atlas has checked (`atlas/sources/_caller.py`). Devin and Hermes's own agent give no such proof.
  - The agent passes, as the owner's words, one whole message you typed in that Session among your newest five, naming Atlas ("tell atlas ..."), which the Harness stored on one line and at most 400 characters long. Atlas reads it from the Harness's own store. Pasted images, pasted content Codex marks, tool results and blocks the Harness inserts, such as `<system-reminder>`, do not count. A message counts once: a second change on the same message is refused.
  - The Pursuit lists that Session. When more than one Pursuit lists it, your message names the Pursuit.

  The change comes from your message, not from the agent (`atlas/told.py`). Atlas adds at most one Decided line, and it is your message itself, without the "tell atlas", "atlas," or "atlas:" it starts with. It removes at most one Open question, and only when your message, addressed to Atlas, states outright that the question is settled, answered, closed, resolved or done ("tell atlas the open question is settled") and the Pursuit has just that one Open question. A question, a condition ("once the question is settled"), a quoted line or a message about another Pursuit's question does not count. Any other change an agent asks for is kept as a Note and offered once in Atlas's next answer to you. The offer shows the whole change: at most one Open question to remove, by its text, and at most one decision to add, which for an agent Atlas could not check is the words it says you typed. "apply" makes exactly that change, or nothing when that Open question is no longer there. Atlas keeps at most three such Notes from one Harness, and `hermes atlas mcp` takes at most 20 Notes and changes a minute. Any process running as your user on this machine can start `hermes atlas mcp`, call itself Codex and name the thread id of one of your Codex Sessions, which Atlas then takes as proof (ADR 0010 accepts this). Such a process can make, once, the change one of your messages to Atlas in that Session allows: add that message as a Decided line and remove the one Open question it says is settled, on a Pursuit that lists the Session. Forget removes a Note an agent left from a Session Atlas tied it to. A Note Atlas could not tie to a Session, such as any Note from Devin or Hermes's agent, stays in the record, and Forget does not remove it.
- **Changes and deletes files in its own record folder.** Atlas rewrites its Markdown files as your Pursuits change.
  - Forget. When you ask Atlas to forget a Session and confirm with the word "forget", it deletes the Session's raw copy, transcript and summary card, the cached model answers that read it, its rows in the search index, every Pursuit line, Step, History line, Journal line and Profile entry that cites it, a Profile text you corrected away that rested on it, a hand-edit Note that copied text from it, and a Pursuit made from that Session alone (`atlas/forget.py`). It keeps the time of the Forget and a hash of the Session's id in `forgotten.md`, so it never collects that Session again. Forget does not remove the Session from the Harness's own store, which Atlas never writes. It does not change a Daily Brief already written, which keeps its text as sent, or a Note that holds a message you typed to Atlas. Telegram keeps what was delivered.
  - Hand edits. When you edit a record file by hand, Atlas puts its own version back and keeps your edit, with a diff, as a Note (`atlas/record/__init__.py`). A file-sync tool that rewrites the record looks like such an edit.
  - Outside the Session folders under `evidence/` and the export files in `inbox/`, Atlas never changes or deletes a file in the record folder that it did not write. When it removes a Session's folder, it deletes every file in that folder, a file you put there included. It deletes an export file you put in `inbox/` once it has imported every conversation in it, as described above.
- **Serves a read and write API in the Hermes dashboard.** While the plugin is enabled, `hermes dashboard` mounts Atlas's routes under `/api/plugins/atlas/` (`atlas/dashboard/plugin_api.py`). They read the Board, the settings and the masked transcript of any Session in the record, and they change a Pursuit's Status and Focus and Atlas's settings. Hermes's own dashboard sign-in guards these routes, as it guards Hermes's other `/api/` routes: on the default local address that is the dashboard's session token. Atlas adds no check of its own. The API listens wherever the dashboard listens: 127.0.0.1 by default, and on your network if you start the dashboard with another `--host`. The page calls only these routes, and its Copy prompt button writes to your clipboard.
- **Runs shell commands.** Atlas runs the `hermes` CLI as a subprocess for `hermes send`, `hermes send -l`, `hermes cron list --all` and `hermes cron create` (`atlas/_hermes.py`). Where `/proc/mounts` is missing, it runs `mount` to check that the record folder is on a local disk.
- **Adds a cron job and a script to Hermes.** Setup writes `$HERMES_HOME/scripts/atlas_tick.sh` and creates the Hermes cron job `atlas-tick`, which runs `hermes atlas tick --quiet` every 10 minutes with no agent (`atlas/schedule.py`). To remove it, run `hermes cron list`, then `hermes cron remove <id>` with the id it shows for `atlas-tick`. Atlas starts no background process of its own.
- **Records this machine's host name** in the record's `settings.md`, with each Session it copies and in the Where line of each Pursuit (`atlas/app.py`). Setup prints it. Backfill's last grouping call sends each draft Pursuit's Where line, host name included, to the model (`atlas/librarian.py`). The host name also goes to Telegram in each Daily Brief and resume answer, as described above.
- **Stores no credentials and sends no telemetry.** Atlas reads no token, key or password of its own and asks for none. Its code opens no network connection. Everything that leaves the machine goes through Hermes: model calls through `ctx.llm`, and Telegram messages through `hermes send`.

## Hermes surfaces Atlas uses

| Surface | Name | What it does in 0.1.0 |
|---|---|---|
| CLI command | `hermes atlas setup` | Creates the record, finds the Harness stores, checks the Telegram home channel, schedules the tick |
| CLI command | `hermes atlas backfill` | Copies every Session into the record, builds the Pursuits and writes the Journal |
| CLI command | `hermes atlas tick` | Collects new Sessions, imports and deletes the web-chat exports in the inbox, files the Sessions, writes the Journal and corrects a day already written, and sends the Daily Brief when it is due |
| CLI command | `hermes atlas reindex` | Rebuilds the disposable search index from the record |
| CLI command | `hermes atlas mcp` | Serves the three agent tools to the one Harness that starts it, such as Claude Code or Codex, over that process's stdin and stdout. It opens no network port. Setup prints the `claude mcp add` and `codex mcp add` lines for it |
| CLI command | `hermes atlas status` | Not built yet. The command exits with an error |
| Cron job | `atlas-tick` | Runs `hermes atlas tick --quiet` every 10 minutes |
| Agent tools | `atlas_read`, `atlas_note`, `atlas_update` | Let an agent in Hermes, or in a Harness through `hermes atlas mcp`, read a Pursuit's Resume Note or a Journal day, leave a Note, and change a Resume Note when you told it to in that Session |
| Gateway hooks | `pre_gateway_dispatch`, `post_gateway_admission` | Claim your messages to Atlas in Telegram and answer them: replies to the brief, Step outcomes, parking and dropping, pausing, questions, filing and Profile corrections, Forget |
| Slash command | `/atlas` | Hands the words after it to the same handler, from Telegram or the Hermes terminal |
| Skill | `atlas` | Tells Hermes agents when to call the three agent tools |
| Auxiliary model task | `atlas_backfill` | Summarises each Session and groups the Sessions into Pursuits during backfill, and writes the first Profile |
| Auxiliary model task | `atlas_file` | Files each new or grown Session on the tick |
| Auxiliary model task | `atlas_journal` | Writes each day's Journal lines and what that day adds to the Profile |
| Auxiliary model task | `atlas_converse` | Reads your corrections to filing and to your Profile |
| Auxiliary model task | `atlas_answer` | Picks the Session that answers "find the conversation where" |
| Messages out | `hermes send` | Sends the Daily Brief, and an answer that took longer than 25 seconds, to the Telegram home channel |
| Plugin setting | `record_dir` | Where the record lives |
| Dashboard tab | `/atlas` | The Board and Settings, as inner tabs of one Atlas tab |
| Dashboard API | `GET /board` | The Board: your Pursuits in Status columns, with Focus stars and new and changed marks |
| Dashboard API | `PATCH /pursuits/{pursuit}` | Changes one Pursuit's Status or its Focus star |
| Dashboard API | `POST /seen` | Records which cards you have seen, to clear their new and changed marks |
| Dashboard API | `GET /transcript/{evidence}` | The masked transcript of one Session, as plain text |
| Dashboard API | `GET /settings` | The Settings tab's view of `settings.md` |
| Dashboard API | `PATCH /settings` | Makes one settings change: an extra Harness folder, a web chat, an exclusion, the brief and Review times, the Active Time gap, Atlas's own chat |

The dashboard API paths are relative to `/api/plugins/atlas`. The Atlas tab is in the Hermes dashboard (`hermes dashboard`), at http://127.0.0.1:9119/atlas by default.

One setting has nothing behind it yet. You can set the Review day and time, and Atlas sends no weekly Review.

## What works in 0.1.0

The proof harness in `tests/harness` replays the tutorial's invented week and checks Atlas's output against the tutorial's text. `tests/harness/manifest.toml` lists the scenes this version passes, by the numbers in `docs/architecture/atlas-v1.md` §1. CI fails when a listed scene fails. The catalog entry's known issues name the scenes that are not built yet.

## How it was tested

The proof harness replays the tutorial's invented week against a scripted model and a fake Channel that records each message in place of Telegram. The `verify-atlas` tool in the source repository drives the command-line and Telegram scenes through a real Hermes v0.23.0, with a stub `hermes send` and, where a scene needs a model, a stub model. No test in this repository runs a scene against a real model or delivers a message to Telegram.

## Development

```
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
cd tests && ../.venv/bin/python -m harness
```

The design lives in `docs/`: the [brief](https://github.com/zhaidarbek/hermes-atlas/blob/main/docs/atlas-v1-brief.md), the [architecture](https://github.com/zhaidarbek/hermes-atlas/blob/main/docs/architecture/atlas-v1.md) and the [decision records](https://github.com/zhaidarbek/hermes-atlas/tree/main/docs/adr). Every name, Session and folder in the tests and docs is invented.

## License

MIT. See [LICENSE](https://github.com/zhaidarbek/hermes-atlas/blob/main/LICENSE).
