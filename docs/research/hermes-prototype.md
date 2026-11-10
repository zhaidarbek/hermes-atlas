# Hermes prototype

A throwaway prototype run on 2026-11-06. It asked whether Hermes can do what Atlas needs. It tested six claims from the Hermes section of [harness-and-hermes-findings.md](harness-and-hermes-findings.md), which had been read from the documentation and never run. Vocabulary is defined in [GLOSSARY.md](../../GLOSSARY.md).

The probe plugin, the Hermes data folder, and the copy of the Hermes source lived in a session scratch folder. The folder and the container were deleted at the end. This file is the record.

## How to read this file

Every claim carries one of four marks, as in the earlier prototype records.

| Mark | Meaning |
|---|---|
| **(M)** | Observed during the prototype: command output, a log the probe plugin wrote, a screenshot, a message arriving on the owner's phone, or a line read in the shipped Hermes source. |
| **(R)** | Reported by an agent, and not checked independently. |
| **(J)** | The judgment of the agent that ran the prototype. |
| **(O)** | Decided by the owner. |

This file holds no keys, tokens, bot names, account ids, network addresses, or local paths.

## Result in brief

- Five of the six claims pass. Claims 1 and 2 pass with a correction to what the findings said. (M)
- Hermes runs on the owner's always-on Ubuntu PC. Whether the Windows laptop can act as a client of it was read in the source and not run. (M)
- Three findings were wrong or incomplete in ways that change Atlas's design. `on_session_end` fires after every turn. The once-per-session hook skipped cron and Telegram sessions. Cron catches up missed runs when the gateway returns. (M)
- The owner decided that Atlas's home is Hermes on the Ubuntu PC. See [What the owner decided](#what-the-owner-decided). (O)

## The setup

- **Machine.** The owner's always-on PC, running Ubuntu 26.04 natively. Hermes was not installed, and `~/.hermes` did not exist. (M) The findings were gathered on the owner's Windows 11 laptop, which the owner uses mainly as an interface to this PC. (O)
- **Install.** The official Docker image `nousresearch/hermes-agent:v0.23.0`, build commit `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`, built 2026-11-05. The Hermes data folder was a scratch folder mounted as `HERMES_HOME`. The container ran as the owner's user through `HERMES_UID` and `HERMES_GID`. The dashboard was published on loopback only. (M)
- **Model.** Anthropic, through an API key the owner entered without it passing through the chat. The main model was `claude-sonnet-5`. (M)
- **Channel for the test.** A Telegram bot the owner created for the test. The owner was paired to it through Hermes's DM pairing, `hermes pairing approve telegram <request-id>`. (M) Telegram was the owner's choice for this test only. It does not settle which Channel carries the Daily Brief. (O)
- **Source.** The Python source was copied out of the image and read in place of the documentation. It is exactly the code that ran. (M)

### The probe plugin

One general plugin, `atlas_probe`, in `HERMES_HOME/plugins/`, enabled with `hermes plugins enable atlas_probe`. (M)

- Two tools. `atlas_read_record` reads a JSON file of two Pursuits, which stands in for Atlas's record. `atlas_llm_probe` makes one `ctx.llm.complete` call.
- Hooks on `on_session_start`, `on_session_end` and `on_session_finalize`. Each hook appends one line to an event log. The finalize hook also makes one `ctx.llm.complete` call.
- A CLI command, `hermes atlas-probe read|llm`, through `ctx.register_cli_command`.
- A dashboard tab, "Atlas Board", with `dashboard/manifest.json`, a hand-written `dist/index.js` that uses `window.__HERMES_PLUGIN_SDK__`, and a `plugin_api.py` route `GET /api/plugins/atlas_probe/pursuits`.

The record file was mounted read-only at a path outside `HERMES_HOME`. The event log was written to a second folder outside `HERMES_HOME`. (M)

A second plugin, `dummy_mem`, is an external memory provider that does nothing except log when it starts. `memory.provider` was set to it so that the single memory-provider slot was taken for the whole test. (M)

## The six claims

| # | Claim | Result | Evidence |
|---|---|---|---|
| 1 | A plugin can register a tool, and a hook that fires when a Hermes session ends. | **Pass, with a correction** | In a one-shot session, `hermes chat -Q -q "..."`, the agent called both tools. The event log shows `on_session_start`, the two tool calls, `on_session_end` with `completed: true`, and `on_session_finalize` with `reason: cli_close`. (M) `on_session_end` fires after every agent turn, not once per session (`agent/turn_finalizer.py`). (M) `on_session_finalize` is the once-per-session hook. It did not fire for any of the 4 cron sessions or for the 1 Telegram session, including when the gateway stopped with that Telegram session open. (M) The `cron/` source never calls it. (M) |
| 2 | A scheduled job runs a fresh agent session, delivers a message to the owner's phone, and stops when the gateway is down. | **Pass, with a correction** | A job on `*/2 * * * *` with `--deliver telegram:<chat id>` ran at 08:24 and 08:26 UTC. Each run was a new session with `platform: cron` that called `atlas_read_record`. The owner received both messages. (M) The gateway was stopped from 08:27:20 to 08:33:13 UTC. No output was written for the 08:28, 08:30 and 08:32 slots. (M) On restart, Hermes ran the job once at 08:33:35 with `kind: catch_up`. The run history shows `missed ×2 (collapsed into one catch-up run)`, and the owner received that message. (M) The regular 08:34 slot also ran. (M) |
| 3 | A plugin can add a tab to the dashboard with its own backend route. | **Pass** | `/api/dashboard/plugins` listed `atlas_probe` with label "Atlas Board" and tab path `/atlas`. (M) Without a login, the route returned 401 with `reason: no_cookie`. After a login, it returned 200 with the record. (M) In a browser, the tab rendered both Pursuits in columns by Status, and the network log shows the tab calling its route. (M) |
| 4 | A plugin can read a file on disk outside `~/.hermes`. | **Pass** | The tool, the dashboard route, and the CLI command each read the record from its read-only mount outside `HERMES_HOME`. The hooks wrote their log to a folder outside `HERMES_HOME`. (M) Hermes declares no capability for file access, and plugins are not sandboxed. (R) In Docker, the file has to be mounted into the container. A native install would read the path directly. (J) |
| 5 | A plugin can hold its own LLM call through the host, without taking the single external memory-provider slot. | **Pass** | `dummy_mem` started in every session, so the slot was taken. (M) In the same sessions, `ctx.llm.complete` returned `PROBE-OK` from a tool and a sentence from the finalize hook. (M) `hermes atlas-probe llm` returned `CLI-LLM-OK` from a CLI command with no agent session. (M) A plain call needs no capability in the manifest and uses the main model. (M) |
| 6 | What Hermes needs to run on the owner's machines, and what breaks. | **Ubuntu backend: pass. Windows as a client: can't tell.** | On the Ubuntu PC, Docker and the image were enough. Nothing Atlas needs failed. (M) The image ships desktop-app code for connecting to a remote gateway, including `apps/desktop/electron/connection-config.ts` and `windows-remote-lifecycle.ts`. `hermes serve` is described in the source as the backend "the desktop app and remote clients connect to". (M) Nothing was run from Windows. (M) |

## What the findings got wrong

These points are corrected in place in [harness-and-hermes-findings.md](harness-and-hermes-findings.md). Each is (M) unless marked.

- **The machine.** The findings describe one machine, the owner's Windows 11 laptop. Most of the owner's work runs on an always-on Ubuntu PC, which had no Hermes install. (O for where the work runs)
- **`on_session_end`.** The findings list it among the session hooks without saying when it fires. It fires after every agent turn. The once-per-session hook is `on_session_finalize`, and it skipped cron and Telegram sessions in this run.
- **Cron with the gateway down.** The findings say agent jobs need the gateway, which is true. They do not say that a recurring job catches up its missed runs once when the gateway returns. This is the default, `cron.catch_up_missed: True` in `hermes_cli/config_defaults.py`. A job's own `catch_up: false` turns it off, and so does an explicit `grace_minutes` on the job (`cron/missed_runs.py`).
- **`plugins.enabled` and the dashboard.** The findings say general plugins load only when listed in `plugins.enabled`. They do not say that the same list gates a user plugin's dashboard backend route. A user plugin that is not in the list gets no route (`_plugin_api_mount_skip_reason` in `hermes_cli/web_server_dashboard.py`). An agent that read the source reported that the dashboard also drops its tab. (R for the tab)
- **`ctx.llm` overrides.** The findings describe `ctx.llm` as a host-owned call. They do not say that a plugin cannot choose its provider, model, agent id, or profile. Passing any of those raises `PluginLlmTrustError` unless `plugins.entries.<id>.llm.allow_*_override` is set in config.
- **The isolation default.** The findings describe `plugins.isolation: host` without saying that it is opt-in. The default is `in_process`.

## What else the run found

- **Every `docker run` of the image starts a full gateway**, which connects to every configured platform, even for a one-shot command. (M) Two containers on one bot token would compete for its messages, so the test used one long-lived container and `docker exec`. (J)
- **The dashboard requires a login when it binds beyond loopback.** The image binds `0.0.0.0` inside the container, so the test used basic auth. The login endpoint takes a JSON body with `provider`, `username` and `password`. (M) Without an auth provider, the dashboard refuses to start. (R)
- **`hermes cron remove` deletes the job's run output** along with the job. (M)
- **A message to the bot from the paired owner started a Telegram session** that the plugin's `on_session_start` and `on_session_end` hooks saw. Replies to a Daily Brief would arrive this way. Handling them was not tested. (M for the session, J for the reading)

## What the owner decided

- Atlas's home is Hermes on the owner's always-on Ubuntu PC. The Windows laptop is only a way to reach it, added later. (O)
- The owner chose Docker for the test, an Anthropic API key for the model, and Telegram for the test messages. (O)

## Suggested direction

This is the agent's recommendation. The owner has not accepted it. (J)

- **Read Hermes's own `state.db` to collect Hermes Sessions, as for any other Harness.** Do not rely on session hooks. `on_session_end` is per turn, and `on_session_finalize` skipped every cron and Telegram session in this run.
- **Set catch-up on each Atlas job on purpose.** By default Hermes runs a missed job once when the gateway comes back. A Daily Brief that arrives late is better than none, so the default may suit it. Make Pause pause the Hermes job itself.
- **Run upkeep as a plugin CLI command that calls `ctx.llm`.** It needs no agent session and leaves the memory-provider slot free.

## Open points

- Whether the Hermes desktop app on Windows connects to the gateway on the Ubuntu PC. Testing it means exposing the backend beyond loopback, behind the dashboard login.
- Why `on_session_finalize` did not fire for the open Telegram session when the gateway stopped. An agent that read the source reported a shutdown path that calls it. (R) The cause was not found.
- Whether the real build uses Docker or the native installer. A native install avoids mounting the record into a container.
- Which Channel carries the Daily Brief. It is still a parked "how" question in the brief.
