# Hackathon submission

The text for the entry. Links and anything personal stay as `<...>` placeholders until the owner fills them in.

## X post

> Atlas is a Hermes plugin for one person: it reads the Sessions your AI coding agents already keep and sends you one Telegram brief each morning with the next Step for the work you starred. <repo-url> <catalog-pr-url>

## The form

The form is at https://form.typeform.com/to/FwJLvOln. Its page did not load from the machine this was written on (the connection timed out), so its fields could not be read. The fields below are guesses, the usual ones for a hackathon entry. Check them against the form before filling it in.

| Field (guess) | Answer |
|---|---|
| Project name | Atlas |
| One-line description | A Hermes plugin that keeps track of what you are working on across your AI coding tools and sends you the next Step each morning. |
| What it does | Atlas reads the Sessions that Claude Code, Codex, Devin, Hermes and T3 Code already keep on your machine. It groups them into Pursuits, the projects and ideas you started, and writes each Pursuit's state into a folder of Markdown files you can read without Hermes. Each morning it sends a Daily Brief in Telegram with one small Step for each Pursuit you starred, where you left off and where to resume. You answer it in plain words. |
| Hermes surfaces it uses | CLI commands (`hermes atlas setup`, `backfill`, `tick`), a no-agent cron job (`atlas-tick`), `hermes send` to the Telegram home channel, the gateway hooks `pre_gateway_dispatch` and `post_gateway_admission`, the `/atlas` slash command, agent tools (`atlas_read`, `atlas_note`, `atlas_update`), a skill, auxiliary model tasks through `ctx.llm`, a plugin setting (`record_dir`) and a dashboard tab (the Board). |
| Repository link | `<repo-url>` |
| Demo video link | `<demo-video-url>` (the file is `docs/recordings/demo-week.mp4`) |
| Plugin catalog link | `<catalog-pr-url>` |
| Team or entrant name | `<entrant-name>` |
| Contact email | `<contact-email>` |
| Social handle | `<x-handle>` |
