# Changelog

## Unreleased

- Fixed: one model answer with a text over its field's maximum stopped backfill from writing any more cards, and the same Session came first on every run, so no Pursuits were ever built. Atlas now tells the model each field's maximum, and skips a Session whose answer it cannot use, asking again on the next backfill.
- Fixed: with an Anthropic model, or OpenAI's strict structured output, every model call failed, so backfill made no Pursuits and the tick filed nothing. Atlas now sends each answer's shape closed, with every field required, which every provider Hermes supports accepts.

Security fixes from the November 2026 audit:

- Fixed: a line inside a pasted web page or a command's output no longer counts as a message you typed, so an agent cannot use it to change a Pursuit with `atlas_update`.
- Fixed: backfill and "split" keep a Decided line only when its quote is in a message you typed, as filing already did.
- Fixed: the paste-in prompt now opens by telling the agent that its lines are notes quoted from earlier Sessions, not requests, and the Next Step carries its [F] or [I] mark.
- Fixed: when the model reads "merge ..." or "split ...", the reason or criterion it records as your Filing rule may hold only words of your message; a merge reason with other words is left out and such a split is not made.
- Fixed: text from a model's answer can no longer show a second evidence mark, the "Atlas:" label or reordered text in the brief, `atlas_read` or the paste-in prompt.
- Fixed: Atlas left some secrets unmasked: a Telegram bot token inside a Bot API URL, a URL password with no user name, `--password <value>`, `access_token=` and `aws_secret_access_key =` in lower case, `PGPASSWORD=`, and a value written right after a mask.
- Fixed: the title of a ChatGPT or claude.ai conversation could keep the first part of a secret when the 80-character cut fell inside it.
- Fixed: one very long line of upper-case key names, or of private-key headers, could stall the tick for many seconds while Atlas masked it.
- Fixed: a record masked by an earlier version was never masked again when Atlas learned new forms of secret. The opens after an upgrade now mask its files again.
- Fixed: text from a Session can no longer make Hermes attach a file from this machine to a Daily Brief, Review or answer. `MEDIA:<path>` and a bare file path are broken invisibly before the message leaves.
- Fixed: a domain, @name, e-mail address or /command taken from a Session now shows as code in Telegram, also inside a Pursuit name, so Telegram no longer turns it into a link, a mention or a tappable command. A message too long for one Telegram message is still sent without formatting.
- Fixed: a message that starts with `-` can no longer be read by `hermes send` as an option.
- Fixed: a title cut in the middle of an emoji, or holding a NUL character, no longer stops the Daily Brief from being sent.
- Fixed: reading a Hermes, Devin or T3 Code store that no Harness has open no longer leaves `-wal` and `-shm` files beside it.
- Fixed: a Session with a line cut inside an emoji is collected, one such Devin message no longer keeps every Devin Session out, and the Session's transcript opens in the Board.
- Fixed: a transcript file over 1 GiB, or a compressed one that expands to more than 1,000 times its size, is skipped and no longer read into memory.
- Fixed: a store database whose table is a view, or that needs a recursive query, is refused and can no longer keep a tick running.
- Fixed: a store database deleted while Atlas opens it is refused, so a tick no longer waits on a file left under its name.
- Fixed: a `subagents` folder that is a link is no longer listed, and a folder added in Settings is refused when a link is put in its path after Atlas checked it.
- Fixed: a folder added in Settings that Atlas may not enter no longer stops that Harness's own store from being collected.
- Fixed: an export zip whose conversation is mostly very short lines could take many times the stated 1 GiB of memory to import. Atlas now counts what putting the largest conversation in the record takes and refuses the file first.
- Fixed: a zip64 end record behind an ordinary-looking end record, or behind a long archive comment, got past the 100,000-entry and directory-size limits.
- Fixed: a zip was deleted from the inbox although one of its conversation files held no list Atlas could read. Atlas now imports the rest, keeps the zip and says it could not read all of it.
- Fixed: Atlas no longer inflates a conversation file compressed with anything but the stored and deflate methods ChatGPT and claude.ai use.
- Fixed: Atlas no longer replaces or deletes a file of yours that sits where it would write one, such as a Journal day; it leaves the file and writes none until you move it.
- Fixed: only setup creates the record folder, and it is mode 0700 from the moment it is made; the Board, a tick or a reindex before setup no longer create it with a looser mode.
- Fixed: setup now refuses a record folder on a network mount when the path is a link to one, when the share is mounted on demand, or when the mount point has a space or a non-ASCII name.
- Fixed: a Session part with a very long file name no longer leaves a commit that can never finish and blocks every later one.
- Fixed: Atlas no longer opens a record whose `.atlas` is a link, and setup no longer writes its state through one.
- Fixed: after you forgot a Session that a Devin subagent conversation had been folded into through T3 Code, Atlas could collect that conversation again as a Session of its own. Atlas now keeps each folded conversation's key and never collects it again.
- Fixed: Forget left a forgotten Session's lines in the answers Atlas had given you, kept in `notes/`. Forget now replaces such an answer with a line saying it is gone; your own words stay.
- Fixed: after a Forget, the search index still held lines of the forgotten Session in the rows of other Sessions of the same Pursuit. Forget now empties the index, and Atlas builds it again from what is left.
- Fixed: the dashboard's transcript route took `*`, `?` and `[...]` in a Session id as wildcards, so it served a Session whose id the caller did not know. It now answers only an exact id.
- Fixed: the dashboard backend put Hermes's whole plugins folder on Python's import path, so a plugin you installed but never enabled could be loaded in the dashboard process. Atlas now loads its own package by path.
- Fixed: Atlas's dependency ranges allowed `mcp` versions with four known advisories, and `pyyaml` and `zstandard` versions with no ready-built package for Python 3.14. The ranges now start at `mcp` 2.2, `pyyaml` 6.0.3 and `zstandard` 0.24.
- Fixed: removed the unused `demo` extra, which pulled in `pillow`.
- Fixed: `hermes atlas setup` now names the script it writes, `$HERMES_HOME/scripts/atlas_tick.sh`, in "What Atlas does on this machine".
- Docs: the guide now says Atlas supports Python 3.11 to 3.14, what Hermes's install scanner reports for an install from GitHub, that `hermes plugins validate --install-deps` installs dependencies without asking, that the cron job and its script must be removed before the plugin, and that on a machine other people can log in to they can reach the dashboard on 127.0.0.1.
- Added: `SECURITY.md`, on how to report a vulnerability privately.

## 0.1.0

The first release.

- Finds the Sessions of Claude Code, Codex, Devin and Hermes on your machine, including those run through T3 Code, and imports the ChatGPT and claude.ai exports you drop in its inbox.
- `hermes atlas backfill` groups your history into Pursuits, each with where you left off, what you decided and the next Step, and writes a Journal day by day.
- Every 10 minutes the `atlas-tick` cron job collects new Sessions and files them, with one model call each.
- Sends a Daily Brief to your Telegram chat with Hermes each morning: up to three Steps from your Focus, what became of yesterday's, and one decision about a quiet Pursuit.
- Answers your replies in plain words: done, skip, prompt, remind me, park, drop, continue, close it, pause, forget, and corrections to its filing and your Profile.
- Answers "where did I leave off", "what have I neglected", "find the conversation where" and "how long did I work this week".
- Sends a weekly Review on Sunday evening with what you finished, decided and changed, and what it did on its own.
- Formats its Telegram messages: the Daily Brief and the Review as Telegram HTML, answers as Markdown, with bold, italic, code and one emoji per heading. Text from your Sessions is escaped.
- Lists a Session under up to two more active Pursuits it worked on and splits its Active Time with them, reads decisions from your words or an agent's Note in a Session, and writes the steps you finished into the Journal.
- Adds a Board and a Settings tab to the Hermes dashboard.
- Gives agents in Hermes, Claude Code and Codex three tools, `atlas_read`, `atlas_note` and `atlas_update`, through `hermes atlas mcp`.
- Keeps everything in a folder of plain Markdown files, masks the secrets it detects, and lets you forget a Session for good.
- Known gap: `hermes atlas status` is not built yet.
