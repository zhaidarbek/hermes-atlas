# Demo script: a week with Atlas in 90 seconds

This is the voiceover for a demo video of Atlas. The video is a recording of the proof harness replaying the tutorial's week, `cd tests && python -m harness`, cut to this script. Each row names the scenes to show, by their ids in `tests/harness/scenes.py`, and the tutorial section they come from. Show only scenes that pass at the commit you record. A row whose scenes do not pass yet is cut, and its line is cut with it.

The spoken text is 214 words, about 86 seconds at 150 words a minute. Every name, Session and folder on screen is the tutorial's invented world.

| Time | Scenes | On screen | Voiceover |
|---|---|---|---|
| 0:00 | none | Title: "Atlas, a Hermes plugin" | You start a lot of work with AI agents, and most of it goes quiet. Atlas keeps track of it for you. |
| 0:07 | 1 | `hermes atlas setup` output, from "Found on this machine" down | Setup finds Claude Code, Codex, Devin and Hermes on this machine, and schedules a tick every ten minutes. |
| 0:15 | 3a, 3b | `hermes atlas backfill` output: 977 Sessions, 11 Pursuits | Backfill reads every Session you already have and groups them into Pursuits: eleven here, eight active. |
| 0:23 | 4, 6 | The Board, then four cards starred as Focus | On the Board you star the four you care about this week. |
| 0:28 | 7 | The Monday Daily Brief in Telegram | At half past seven the Daily Brief arrives. Each Step is one small action, with where you left off and where to resume. |
| 0:37 | 8 | The reply `1 prompt`, then the resume prompt | Reply "1 prompt" and paste the answer into Codex. You stopped in Claude Code, and you carry on in Codex. |
| 0:45 | 9, 9b, 10 | The reply `1 done ...`, then Tuesday's follow-up | Answer in plain words. Do a Step without telling Atlas, and the next brief has noticed. |
| 0:53 | 12 | Claude Code calling `atlas_read` and `atlas_note` | Your agents read Atlas and leave it Notes through MCP. |
| 0:58 | 21, 22 | "what have I neglected?", then the GPX answer with its quote | Ask what you have neglected, or where that GPX conversation was. Each answer quotes its source. |
| 1:06 | 27, 30 | `ls ~/atlas` with Hermes stopped, then the Forget preview | The record is plain Markdown files on your disk. You can read it without Hermes, and you can forget a Session for good. |
| 1:14 | 36 | The Sunday Review | On Sunday the Review shows what you finished and decided, and what Atlas did on its own. |
| 1:20 | 38, 39 | "where did I leave off on the keyboard firmware?", then the card back in Active | And a Pursuit you parked in August is back in under a minute. Atlas: install it from the Hermes plugin catalog. |

## Cutting notes

- Cut on the row times. Hold a still frame where the harness output is shorter than its voiceover.
- Crop every frame to the Atlas output. Leave out the terminal prompt, the window title and any path outside the harness's temporary home.
- Telegram rows show the text the harness handed to `hermes send`, rendered as a chat bubble. Do not call it a live Telegram recording.
