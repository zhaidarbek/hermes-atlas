# Changelog

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
