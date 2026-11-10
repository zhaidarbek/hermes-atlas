# Harness and Hermes findings

Facts gathered on 2026-11-05 during the Atlas design interview. Decisions are in [../atlas-v1-brief.md](../atlas-v1-brief.md); this file holds only what was found.

The [Hermes prototype](hermes-prototype.md) of 2026-11-06 ran Hermes and corrected the description of the machine and several points in section 5. Those corrections carry the mark (V-run).

## How to read this file

Every claim carries one of three marks.

| Mark | Meaning |
|---|---|
| **(V-machine)** | Verified by a read-only check on the owner's Windows 11 laptop: a file listing or a database query. |
| **(V-linux)** | Verified on 2026-11-08 by a read-only check on the owner's Ubuntu PC that printed key names, column names, type names and counts only. |
| **(V-run)** | Verified in the [Hermes prototype](hermes-prototype.md) of 2026-11-06, against a running Hermes v0.23.0 or its shipped source. |
| **(V-docs)** | Verified as stated on the cited page. Pages were read through a fetch tool that summarises them, and some pages were cut off (listed under [What the docs did not say](#what-the-docs-did-not-say)). Nothing was tested against a running Hermes. |
| **(I)** | Inferred, or taken from a search-result summary without opening the page. |

Paths are written with `~` for the user's home folder. This file contains structure, counts and field names only: no conversation content, no account details and no ids.

Two subagents examined the machine. Where they disagree, both results are given.

- **Subagent A** examined T3 Code's own database.
- **Subagent B** compared T3 Code with the Harnesses' own stores.

## 1. What is installed on this machine

- This machine is the owner's Windows 11 laptop, which the owner uses mainly as an interface. Most of the owner's work runs on an always-on Ubuntu PC, which this section does not cover. On 2026-11-06 Hermes was not installed on that PC. (V-run for the PC; the owner's statement for where the work runs)
- On the PATH: `hermes`, `claude`, `codex`, `devin`, `wsl`, `tailscale`. (V-machine)
- `~/.hermes` exists with one entry, so Hermes is installed but barely set up. (V-machine for the listing; I for "barely set up")
- `~/.claude/projects` has 19 entries. A recursive count found 328 `.jsonl` files, about 1,971 MB, the oldest last written 2026-07-03. (V-machine)
- `~/.codex/sessions` held 485 files, about 1,129 MB, the oldest last written 2026-06-09. (V-machine)
- Devin has data in `~/.devin` (2 entries), `%APPDATA%\devin` (43 entries) and `%LOCALAPPDATA%\devin` (2 entries). (V-machine)
- `~/.t3` (9 entries) and `~/.orca` (2 entries) exist. (V-machine)

## 2. Where each Harness keeps Sessions

All of this section is from Subagent B and is (V-machine) unless marked.

### Claude Code

- **Store:** `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`.
- **One Session:** one top-level file. There were 113.
- **Native id:** the file name is the session id, and each record also carries `sessionId`.
- **Subagents:** `<sessionId>/subagents/agent-<17 hex>.jsonl` (142 files), and workflow subagents in `subagents/workflows/wf_*/` (76 files).
- **Launch fields**, present on every message record: `entrypoint`, `userType`, `version`, `cwd`, `gitBranch`, `isSidechain`, `permissionMode`.
- **Values of `entrypoint` seen:** `cli`, `claude-desktop`, `sdk-ts`, `t3-code`. `userType` was always `external`.
- **Retention:** `cleanupPeriodDays` is 3650 in `~/.claude/settings.json`. Sessions starting as early as 2026-06 are still present.

### Codex

- **Store:** `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl.zst`.
- **Counts:** 495 zstd-compressed files plus 4 plain `.jsonl`, and 14 more in `archived_sessions`.
- **One Session:** one file is one thread.
- **Native id and launch fields:** the first record is `session_meta`. Its payload has `id`, `session_id`, `originator`, `source`, `thread_source`, `cli_version`, `cwd`, `git`, `model_provider`, `timestamp`.
- **Indexes:**
  - `~/.codex/state_5.sqlite` has a `threads` table (495 rows) with the same fields, including `rollout_path`, `originator` and `source`, plus `thread_spawn_edges` (107 rows).
  - `thread_history_1.sqlite` holds per-item projections in `thread_items` (22,916 rows).
  - `~/.codex/sqlite/codex.db` has `local_thread_catalog` with 426 rows, another index of the same threads, used by the Codex desktop app.
- **Extra content:** a rollout has per-turn `token_count` and `token_usage_record`.
- **Retention:** no retention setting was found. Codex compresses old rollouts to `.zst`, archives some (14 files) and records `archived` in `state_5`.
- **Worktrees:** `~/.codex/worktrees` has 17 directories.

### Devin CLI

- **Store:** `%APPDATA%\devin\cli\sessions.db`.
- **One Session:** one row of the `sessions` table. There were 32, created 2026-05-28 to 2026-10-19.
- **Native id:** a readable slug, not a UUID.
- **Launch fields:** `backend_type` (always `windsurf`), `agent_mode`, `working_directory`, `model`, `hidden`, and `metadata.client_meta["cognition.ai/requestingTabId"]`.
- **Messages:** in `message_nodes` (10,954 rows), with `subagent_heads` and `tool_call_state` alongside.
- **Other files:** `%APPDATA%\devin\User\acp-messages\<uuid>.db` holds 120 small per-session caches, of which only 4 contain messages. None refers to a `sessions.db` id.

### Stores on the Linux PC

All of this subsection is (V-linux). It records the shapes Atlas's adapters read, which differ from the guesses the first fixtures used.

- **Codex:** `~/.codex/sessions` and `archived_sessions` hold 383 rollouts, 288 `.jsonl.zst` and 95 `.jsonl`, written by `cli_version` 0.160 to 0.167.
  - `session_meta.source` is `vscode` (176), `cli` (1) or `exec` (2) for the owner's 179 threads, and an object for the other 204: `{"subagent": {"thread_spawn": …}}` (125) or `{"subagent": {"other": …}}` (79, guardian reviews).
  - An `event_msg` `item_completed` item of type `UserMessage` has the keys `id` and `content`, sometimes `client_id`. One of type `AgentMessage` has `id`, `phase` and `content`, sometimes `delivery` and `questions`. Neither has `text`.
  - `content` is a list of blocks. A `UserMessage` holds `{"type": "text", "text", "text_elements"}` blocks and sometimes `{"type": "image", "image_url"}`. An `AgentMessage` holds `{"type": "Text", "text"}` blocks.
  - In the owner's threads each `UserMessage` holds one text block, the typed text. The context Codex injects is in `response_item` messages with role `user` or `developer` and `input_text` blocks, some starting with tags such as `<environment_context>`. A guardian review's `UserMessage` holds 14 to 25 text blocks.
  - A rollout also holds `compacted` lines (25 in all) and keeps the lines before them.
- **Devin CLI:** `~/.local/share/devin/cli/sessions.db` holds 26 Sessions.
  - Tables: `sessions`, `message_nodes`, `prompt_history`, `tool_call_state`, `subagent_heads`, `live_row_times`, `app_state`, `rendered_commits`, `refinery_schema_history`, and the full-text index `message_search*`.
  - `sessions` columns: `id`, `working_directory`, `backend_type`, `model`, `agent_mode`, `created_at` (INTEGER, epoch seconds), `last_activity_at`, `title`, `main_chain_id` (the node id of the main chain's last node), `shell_last_seen_index`, `cogs_json`, `workspace_dirs`, `hidden`, `metadata`.
  - `message_nodes` columns: `row_id`, `session_id`, `node_id`, `parent_node_id`, `chat_message`, `created_at` (INTEGER, epoch seconds), `metadata`. Its 2,150 rows form chains from 122 roots.
  - `chat_message` is JSON with `role` (`system` 749, `user` 169, `assistant` 784, `tool` 448), `content` (a string in every row), `message_id` (a 36-character UUID) and `metadata`, and on some rows `tool_calls`, `tool_call_id`, `thinking` and `phase`. `metadata` holds `created_at` (ISO 8601 with nanoseconds and `Z`), `is_user_input`, `extensions`, `finish_reason` and token counts.
  - `is_user_input` is true on 167 `user` rows and absent on 2. Nine of those 167 carry the extension `subagent/handoff`: a subagent's handoff prompt, not the owner's.
  - A compaction copies earlier messages into a new chain, marked in the row's `metadata` column by the extension `compact/prior_node_ids`. A copy keeps its `message_id` and `metadata.created_at`. 126 of the 167 owner-input rows repeat an earlier one.
- **Claude Code:** 215 top-level transcripts. A newer user record carries `origin` as an object: `{"kind": "human"}` on typed messages (`promptSource` `typed`, `sdk`, `suggestion_accepted` or none), and `{"kind": "task-notification", …}` or `{"kind": "peer", …}` on injected ones (`promptSource` `system`).
- **Hermes:** not used on this PC (no Sessions). Its `state.db` has the v0.23.0 columns, `messages.message_uid` among them.

#### What identifies a line

Counted on 2026-11-09 over the same stores, read-only, counts and key names only. Atlas keeps each line of a raw copy once by these identities (record-layout.md, "Line identities").

- **Claude Code:** 826 transcript files (subagents included), 330,903 lines. 211,615 carry a `uuid`, and no `uuid` occurs twice in one file. The other 119,288 are bookkeeping lines with no `uuid` (`api-request`, `api-request-blob`, `last-prompt`, `queue-operation`, `custom-title`, `ai-title`, `mode` and others), and 18,547 of them repeat an identical earlier line of their file.
- **Codex:** 385 rollouts (288 `.jsonl.zst`), 84,712 lines. No line repeats within a rollout, and no item id repeats.
- **Devin CLI:** 2,394 message nodes in 28 Sessions. `node_id` is unique within a Session. 702 `message_id`s have copies (1,238 copies in all), and every copy has the same role, content, `is_user_input` and `metadata.created_at` as the first. A `sessions` row's `cogs_json` averages 33,560 bytes, and Devin rewrites it and `last_activity_at` as a Session runs. Counted again later the same day, over 29 Sessions: 744 `message_id`s have copies (1,290 copies). In 400 of them a later copy's `metadata.extensions` differs from the first copy's (it gains a `chisel/...` extension), and in none does a copy differ in role, content or whether it is the owner's. Identified with its extensions, a message showed twice: 58 extra agent turns in 18 of the 29 Sessions; identified without them, none.
- **Hermes v0.23.0** (its source, `hermes_state_messages.py` and `hermes_state_identity.py`, and its `SessionDB` in the image): `replace_messages` (for `/retry`, `/undo` and `/compress`, and ACP's persist with `active_only=True`) deletes a Session's live rows and inserts the kept ones again under new row ids, with the same role, content and timestamp. `rewind_user_turn` (`/undo n` in the CLI) marks the rows it takes back `active = 0, compacted = 0` instead. A compaction archives rows (`active = 0, compacted = 1`) and inserts copies of the protected tail with the original timestamps. `set_user_message_content` rewrites a user row's text in place after an `@`-file expansion. `message_uid` is minted once per message and kept by every rewrite, but rows written before the column existed get theirs later, a slice per open. Hermes shows each message once by `_display_dedupe_key`: role, content, timestamp, tool call id, tool calls and tool name, or the tool calls' ids for an assistant row whose calls all have them, so a prune that rewrites their arguments keeps it.

### Gaps in the dates

- August is sparse in both Claude (3 sessions) and Codex (8 day folders). (V-machine)
- Nothing points to deletion, so this probably reflects how much the tools were used. (I)

## 3. How T3 Code stores conversations

T3 Code's data is in `~/.t3`. Its folders are `bin`, `caches`, `projects`, `runtime`, `scratch`, `tools`, `userdata` and `worktrees`. (V-machine)

### The database

From Subagent A, (V-machine) unless marked.

- **Main file:** `userdata/statev2.sqlite`, about 3.3 GB with a write-ahead log beside it.
- **Mostly empty:** 442,145 of 809,221 pages are free, so about 1.8 GB is space left by pruning.
- **Where the live data is:** of roughly 1.4 GB, `orchestration_v2_projection_turn_items` holds about 1.32 GB. About 1.26 GB of that is 4,646 `Read` tool results containing base64 image blocks, across 72 threads.
- **Table sizes are estimates:** `dbstat` was not available, so sizes come from summed column lengths.

Main tables and row counts:

| Table | Rows | Holds |
|---|---|---|
| `orchestration_v2_projection_threads` | 92 | One row per conversation |
| `orchestration_v2_projection_runs` | 59 | One row per prompt sent to the agent |
| `orchestration_v2_projection_run_attempts` | 59 | Retries of a run |
| `orchestration_v2_projection_nodes` | 10,963 | The execution tree |
| `orchestration_v2_projection_turn_items` | 17,471 | The transcript items |
| `orchestration_v2_turn_item_positions` | 17,471 | Positions of those items |
| `orchestration_v2_projection_messages` | 1,805 | User and assistant message text |
| `orchestration_v2_projection_provider_sessions` | 10 | Link to each tool's native session |
| `orchestration_v2_projection_provider_threads` | 11 | Link to each tool's native thread |
| `orchestration_v2_projection_provider_turns` | 58 | Link to native turns |
| `orchestration_v2_projection_provider_session_bindings` | 9 | Bindings for the above |
| `orchestration_v2_projection_subagents` | 78 | Subagent prompt, result, model, status |
| `orchestration_v2_projection_checkpoint_scopes` | 11 | Checkpoint scopes |
| `orchestration_v2_projection_checkpoints` | 70 | Git checkpoints |
| `orchestration_v2_projection_context_handoffs` | 2 | Handoffs |
| `orchestration_v2_projection_context_transfers` | 1 | Transfers |
| `orchestration_v2_message_search*` | 342 documents | Full-text search index |
| `orchestration_v2_events` | 0 | Event log, pruned |
| `orchestration_events` | 463 | Older event log, pruned |
| `projection_projects` | 8 | The live project list (older v1 table) |
| `projection_threads` | 4 | Older v1 threads |
| `projection_thread_messages` | 36 | Older v1 messages |
| `projection_thread_sessions` | 4 | Older v1 sessions |
| `provider_session_runtime` | 3 | Older resume cursors |
| `orchestration_v2_legacy_imports` | 4 | Record of the v1 import |
| `auth_sessions` | 33 | Client sign-ins |
| `auth_pairing_links` | 7 | Pairing links |
| `peer_environment_links` | 0 | Links to other machines |
| `scheduled_tasks*`, `pull_request_*` | 0 | Unused |
| `effect_sql_migrations` | 79 | Schema migrations |

Columns of the two central tables:

- **Threads:** `thread_id`, `project_id`, `title`, `default_provider`, `provider_instance_id`, `runtime_mode`, `interaction_mode`, `active_provider_thread_id`, `created_at`, `updated_at`, `archived_at`, `deleted_at`, `payload_json`.
- **Runs:** `run_id`, `thread_id`, `ordinal`, `provider`, `provider_thread_id`, `status`, `requested_at`, `completed_at`.

### The model

From Subagent A, (V-machine).

- The shape is **project → thread → run → node tree → turn items**.
- The unit of conversation is the **thread**. A project is a workspace root, and threads link to it by `project_id`.
- A **run** is one prompt plus the agent's response. It links by `thread_id` and carries `userMessageId` and `rootNodeId`.
- **Nodes** link by `run_id` and `parent_node_id`, and also have `root_node_id`. Their kinds are `root_turn`, `tool_call`, `assistant_message`, `subagent` and `reasoning`.
- **Turn items** link by `thread_id`, `run_id`, `node_id`, `provider_turn_id` and `parent_item_id`.
- **Subagents** get their own child thread. Every thread's `payload_json.lineage` holds `parentThreadId`, `relationshipToParent` (subagent or fork) and `rootThreadId`. A top-level thread's has `parentThreadId` and `relationshipToParent` null and its own id as `rootThreadId`. (V-machine)
- **Attachments** are files in `userdata/attachments`, named `<threadId>-<uuid>.<ext>` (10 files). Messages reference them through `attachments[]` in the JSON.

### What each conversation records

From Subagent A, (V-machine).

- **Provider and model:** `default_provider`, `provider_instance_id`, and `modelSelection.model` in the JSON.
- **Working directory:** `cwd` in the provider session and checkpoint-scope JSON.
- **Git:** `branch` and `worktreePath` in the thread JSON. Branch is set on 79 threads, worktree on 3.
- **Title**, and thread `created_at` and `updated_at`.
- **Timing:** run `requested_at` and `completed_at`, and per-item `startedAt` and `completedAt`.
- **Content:** user prompts, assistant text and reasoning text. The full transcript, including tool outputs, is stored; it is not only a pointer.
- **Tools:** tool calls with `toolName`, `input` and `output`; shell commands with `input`, `output` and `exitCode`; file changes as `fileName` plus `diffStr`; web searches.
- **Subagents:** prompt, result, model and status.
- **Tokens:** `tokenUsage` and `turnTokenUsage` per provider turn (input, cached, output, reasoning), and `contextUsage` per provider thread.
- **Cost:** not stored. No cost field was found.

### Volume

- 92 threads, of which 78 are Claude subagent child threads. (V-machine, both subagents)
- Top-level conversations: Subagent A counted 13 plus 1 fork; Subagent B counted 14. (V-machine)
- **Claude (`claudeAgent`):** 87 threads: 78 subagent threads, 7 started from the web UI, 1 via MCP, 1 fork. Created 2026-10-27 to 2026-11-04. (V-machine, A)
- **Codex:** 5 threads: 4 imported from v1 and 1 from the web UI. Created 2026-07-30 to 2026-11-04. (V-machine, A)
- **Devin:** none, although `acpRegistry_devin` is configured in T3 Code's `settings.json`. (V-machine, both)
- **Top-level threads by month:** 2026-07: 2 Codex. 2026-10: 4 Claude and 2 Codex. 2026-11: 4 Claude and 1 Codex. (V-machine, A)
- Messages span 2026-07-30 to 2026-11-04. (V-machine, A)

### The older database

From Subagent A, (V-machine).

- `userdata/state.sqlite` is the v1 format. It has no `orchestration_v2_*` tables; it has `projection_turns`, `projection_thread_activities` and `checkpoint_diff_blobs`.
- It holds 4 Codex threads (2026-07-30 to 2026-10-06), 36 messages and 3 projects.
- All 4 threads are in `statev2.sqlite`, marked `historyOrigin = 'v1_import'`, with 36 messages imported according to `orchestration_v2_legacy_imports`. So the new database contains the old data.

### Machine identity

From Subagent A.

- `userdata/environment-id` and `userdata/anonymous-id` each hold a UUID, and `~/.t3/usage-host-id` holds a 15-character id. (V-machine)
- `server-runtime.json` records the host, port and an `ownerId`. (V-machine)
- `peer_environment_links` is empty. (V-machine)
- `auth_sessions` lists 26 desktop and 7 mobile client sessions, with IP, OS and browser columns. (V-machine)
- Thread and project ids are random UUIDs, and `workspace_root` and `cwd` are absolute local paths. Nothing in the database marks which machine a thread came from, so data from several machines would have to be told apart by `environment-id`. (I)

### Cache and config files

From Subagent A, (V-machine).

- `caches/<provider>.json` exists for `antigravity`, `claudeAgent`, `codex`, `cursor`, `fx`, `grok`, `kiro`, `muse`, `opencode` and `pi`. Each holds that provider's install and sign-in status, version, model list, slash commands, skills and usage limits. No sessions. Two of them include an account identifier, not recorded here.
- `connection-catalog.json` holds a version number and a single encrypted blob, `encryptedCatalog`.
- `settings.json` holds favorites, provider instances and per-project overrides.
- `model-manifest.json` holds the current model list for each provider.

## 4. Matching T3 Code to the Harness stores

### Where T3 Code keeps the native id

(V-machine, both subagents.)

- **Main place:** `orchestration_v2_projection_provider_threads.payload_json`, at `$.nativeThreadRef.nativeId`. It joins to T3 Code's thread through `$.appThreadId`, which is also the `thread_id` column.
- **The `provider_thread_id` column:** Subagent A reports the native id is embedded in this string after `native-thread:`. Subagent B describes the column as a T3 Code synthetic id, not the native one. Both can be true; this was not reconciled.
- **Subagents:** `orchestration_v2_projection_subagents.payload_json` at `$.nativeTaskRef.nativeId`.
- **Older Codex threads:** `provider_session_runtime.resume_cursor_json` at `$.threadId`.
- **Per turn and per item:** native ids are under `nativeTurnRef` and `nativeItemRef`.

### Match rates, T3 Code to Harness

| Provider | Subagent A | Subagent B |
|---|---|---|
| Claude | 9 of 9 found in `~/.claude/projects` | 9 of 9 |
| Codex | 1 of 2 found as a rollout file | 5 of 5 found (rollout file and `threads` row) |
| Claude subagents | not tested | 4 of 4 direct ones found as `agent-*.jsonl` |
| Devin | none to test | none to test |

All (V-machine). Subagent B looked in more places for Codex than Subagent A. 72 nested subagent ids (of the form `<runId>:agent:N`) and 2 workflow ids could not be matched by id. (V-machine, B)

### Match rates, Harness to T3 Code

From Subagent B, (V-machine).

- **Claude:** of the 10 most recently modified transcripts, 1 is known to T3 Code. Across all 113, 9 are (8%).
- **Codex:** of the 10 most recent user threads, 1 is known to T3 Code. Most recent work has originator `Codex Desktop`.
- **Devin:** 0 of 32.
- So most of the work on disk on this machine was done outside T3 Code. (V-machine)
- T3 Code may have dropped older threads, which would understate its share. (I)
- The owner said they mostly use these Harnesses through T3 Code. The counts above do not show that for this machine; the owner's other machines were not examined. (I)

### Which copy is richer

From Subagent B, checked on one matched Claude pair. (V-machine)

| | Native transcript | T3 Code copy |
|---|---|---|
| Size | 338 records | 21 messages |
| Token usage | `usage` on each of its 63 assistant records | Only a running `contextUsage` total |
| Content | 24 thinking, 25 `tool_use`, 26 `tool_result`, 8 image blocks | Turn items with `input`, `output`, `exitCode`, `startedAt`, `completedAt` |
| Item types | — | `command_execution`, `dynamic_tool`, `file_change`, `reasoning`, `subagent`, `checkpoint` |
| Timestamps | On most records | Per item |
| Subagents | Separate files | Child threads |
| Only here | — | Git checkpoints |

The native transcript is the richer raw record; the T3 Code copy is cleaner and more structured.

### Can a transcript alone say it came from T3 Code?

From Subagent B.

- **Claude:** partly. The newest T3 Code session has `entrypoint: "t3-code"`; the 8 older ones have `sdk-ts`. (V-machine) No session started outside T3 Code had `sdk-ts`, but that value would appear for any Agent SDK app, so it is not proof. (I)
- **Codex:** yes for recent ones. `originator: "T3 Code"` appears in 3 of the 5; the 2 older ones (CLI 0.146) have no originator. (V-machine)
- **Devin:** unknown.

### T3 Code prunes and compacts itself

- `settings.json` has `idleCompaction: "onReturn"`. (V-machine, B)
- The `compacted-at` marker read 2026-11-05 20:41. (V-machine, both)
- The event logs are pruned: `orchestration_v2_projection_metadata` shows pruned through sequence 64401. They are not a durable history. (V-machine, A)
- 4 threads are soft-deleted (`deleted_at`). (V-machine, both)
- Pruning and deletion seem to have removed older history: scratch folders and project folders show more activity than the database holds. (I, A) That T3 Code dropped older threads is not proven. (I, B)
- T3 Code's database therefore looks like a poor primary record compared with the Harnesses' own stores. (I)

### Other copies on this machine

From Subagent B, (V-machine), counts only.

- **Orca holds a second Codex home:** `%APPDATA%\orca\codex-runtime-home\home`, about 5.4 GB.
  - It has 492 uncompressed rollouts: 471 have the same ids as files in `~/.codex`, 21 exist only in Orca, and 14 exist only in `~/.codex`.
  - It has its own `state_5.sqlite` and `thread_history_1.sqlite`.
  - Orca also has `agent-session-journal.db` and `orchestration.db`, which were not opened.
  - `~/.orca` itself has no sessions.
- **Worktrees:** `~/.t3/worktrees` is empty. One Claude project directory is a worktree path. Worktree sessions get their own project directory, not a copy of another session.

### Could not determine: Subagent A

- Whether hard-deleted threads leave any trace. The event log is pruned, so earlier history cannot be rebuilt.
- Where pre-October Claude activity went: deleted, or never run through T3 Code's v2 format.
- The second Codex native id: no rollout file matched it. It may have been deleted or stored under a different path. (Subagent B later found all five.)
- The contents of `connection-catalog.json`: it is encrypted.
- Exact page usage per table: `dbstat` was not available.

### Could not determine: Subagent B

- How Devin runs inside T3 Code store their ids, since T3 Code has no Devin threads to test.
- How T3 Code's nested and workflow subagent ids map to the `wf_*` folders in Claude's store.
- Message contents of the `.zst` rollouts: no zstd decoder was available and none was downloaded. The `state_5` index and the 4 plain files were used instead.
- What T3 Code's compaction removed: the free pages cannot be read without a carving tool.
- The contents of Orca's `agent-session-journal.db` and `orchestration.db`.

### Not examined at all

- `~/.t3/userdata/secrets` and `~/.t3/userdata/clerk`, by instruction.
- Credential files of the Harnesses, by instruction.
- The owner's other machines.
- What access ChatGPT and claude.ai give for taking conversations out.

## 5. Hermes

All of this section is (V-docs) unless marked. Links are under [Sources](#sources).

### Feature list

From the features overview page.

- **Core:** tools and toolsets; skills; persistent memory (`MEMORY.md`, `USER.md`); context files (`AGENTS.md`, `CLAUDE.md`); context references with `@`; checkpoints.
- **Automation:** scheduled tasks (cron); subagent delegation; code execution; event hooks; batch processing.
- **Media and web:** voice mode; wake word; browser automation; vision; image generation; text-to-speech.
- **Integrations:** MCP; provider routing; fallback providers; credential pools; prompt caching; memory providers; API server; ACP; batch processing.
- **Customization:** personality (`SOUL.md`); skins and themes; plugins.

### What a plugin can register

- **Plugin types:**
  - General: tools, hooks, slash commands, CLI commands, bundled skills. Many can be enabled at once.
  - Memory provider: a single one is active.
  - Context engine: a single one is active.
  - Model provider: many load, one is used at a time.
- **Layout:** `~/.hermes/plugins/<name>/` with `plugin.yaml`, `__init__.py` (a `register(ctx)` function), `schemas.py` and `tools.py`.
- **Manifest fields:** `name`, `version`, `description`, `provides_tools`, `provides_hooks`, `requires_env`, `capabilities`, `update_url`, and for manifest v2 `manifest_version`, `api_version`, `requires_plugins`.
- **Core registration calls:** `ctx.register_tool`, `ctx.register_hook`, `ctx.register_command` (slash command in CLI and gateway), `ctx.register_cli_command` (adds `hermes <plugin> <subcommand>`), `ctx.register_skill`, `ctx.dispatch_tool`.
- **Backend registration calls:** `ctx.register_platform`, `ctx.register_image_gen_provider`, `ctx.register_video_gen_provider`, `ctx.register_delegation_provider`, `ctx.register_context_engine`, `ctx.register_terminal_environment_provider`, `ctx.register_computer_use_provider`, `ctx.register_approval_transport`, `ctx.register_tts_provider`, `ctx.register_transcription_provider`.
- **Runtime services:**
  - `ctx.llm.complete` and `ctx.llm.complete_structured`: one-shot LLM calls owned by the host. A plain call needs no declared capability and uses the main model. Passing `provider`, `model`, `agent_id` or `profile` raises `PluginLlmTrustError` unless `plugins.entries.<id>.llm.allow_*_override` is set in config. (V-run)
  - `ctx.call_mcp(server, tool, arguments, timeout=30)`.
  - `ctx.inject_message(content, role=, session_key=, origin=)`.
  - `ctx.post_message(text)`, returning a handle with `.edit(text)`.
  - `ctx.platform_actions` (`add_reaction`, `set_thread_title`), Telegram and Discord only.
  - `ctx.session_cwd()`, `ctx.session_surface()`, `ctx.has_capability()`.
- **Discovery sources, later ones overriding earlier on a name clash:** bundled; user (`~/.hermes/plugins/`); project (`.hermes/plugins/`, not loaded by the gateway); pip entry points (`hermes_agent.plugins`); Nix.
- **Install:** `hermes plugins install owner/repo` or a catalog name; also `search`, `browse`, `info`, `update`, `enable`, `disable`, `remove`, `list`, `validate`, `pack`.
- **Opt-in:** general plugins are disabled by default and load only when listed in `plugins.enabled`. `hermes plugins enable <name>` adds a plugin to the list. The same list gates a user plugin's dashboard backend route, which is not mounted while the plugin is missing from the list. (V-run)
- **Security:** every install and update runs a static scan; a "dangerous" verdict blocks the install and `--force` does not override it. Privileged surfaces are declared in `capabilities` and need recorded consent.
- **Isolation:** `plugins.isolation: host` runs user plugins in a separate process. The default is `in_process`, so `host` is opt-in. (V-run) Gateway platform adapters, approval transports, some model providers, terminal backends and streaming dashboard endpoints need `in_process`. `ctx.post_message` is not available in host mode.
- **Limits:**
  - Plugins are not sandboxed; capabilities are a consent and audit layer.
  - In the gateway, `os.getcwd()` is the gateway's directory, so `ctx.session_cwd()` must be used.
  - Injection needs the `allow_gateway_injection` permission.
  - MCP access is off by default and needs a per-server `mcp_allowlist`; results over about 64 KB are truncated.

### Hooks

- The docs describe four hook systems: gateway event hooks, plugin hooks, shell hooks and outbound webhooks.
- **Gateway event hooks** live in `~/.hermes/hooks/<name>/` with `HOOK.yaml` and `handler.py`. They run only in the gateway, load at startup, and are observers: they cannot inject context, block or modify.
  - Events: `gateway:startup`, `session:start`, `session:end`, `session:reset`, `session:compress`, `agent:start`, `agent:step`, `agent:end`, `reaction:added`, `reaction:removed`, `command:*`.
- **Plugin hooks** are registered with `ctx.register_hook` and run in both CLI and gateway sessions.
  - Directive: `pre_tool_call`, `pre_llm_call`, `pre_verify`, `pre_gateway_dispatch`, `post_gateway_admission`.
  - Transform: `transform_tool_result`, `transform_terminal_output`, `pre_transcription`, `transform_llm_output`, `transform_api_error_classification`.
  - Observer: `post_tool_call`, `post_llm_call`, `pre_api_request`, `post_api_request`, `api_request_error`, `pre_auxiliary_call`, `post_auxiliary_call`, `on_stream_start`, `on_stream_delta`, `on_stream_end`, `on_interim_message`, `on_session_start`, `on_session_end`, `on_session_finalize`, `on_session_reset`, `on_session_delete`, `post_compaction`, `agent_loop_stopped`, `on_skill_lifecycle`, `subagent_start`, `subagent_stop`, `gateway_platform_event`, `pre_command`, `pre_approval_request`, `post_approval_response`, `on_human_input_request`, `on_human_input_resolved`, `on_room_member_activity`, `kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`, `on_kanban_worker_spawned`, `on_kanban_worker_exited`, `on_kanban_worker_stale_claim`, `on_kanban_task_updated`, `on_kanban_dispatch_tick`.
- `on_session_end` fires after every agent turn, not once per session. `on_session_finalize` is the once-per-session hook. It fired at the end of a one-shot CLI session. It did not fire for cron sessions, which never call it, or for a Telegram session that was open when the gateway stopped. (V-run)
- `pre_llm_call` can return context, which is appended to the current user message and not the system prompt. It can also block the turn.
- `pre_tool_call` can block, approve or modify. A callback that times out or raises blocks the tool; most other hooks fail open. The timeout is `plugins.hook_callback_timeout`, default 30 seconds.
- **Shell hooks** are a `hooks:` block in the profile's `config.yaml` pointing at scripts. **Outbound webhooks** push signed lifecycle events to HTTP endpoints.

### Scheduled jobs

- Jobs are managed through one `cronjob_manage` tool (create, list, pause, resume, edit, run, remove), the `/cron` command, the `hermes cron` CLI, or plain-language requests.
- Job definitions are in `~/.hermes/cron/jobs.json`; run output goes to `~/.hermes/cron/output/{job_id}/`.
- The gateway daemon checks for due jobs every 60 seconds. Each due job starts a fresh agent session with no memory of earlier runs, so the prompt must be self-contained. Attached skills load first.
- **The gateway must be running.** Agent jobs run inside the gateway; `hermes cron tick` does not start them. Script-only (`no_agent`) jobs can run from a bare tick.
- **Missed runs are caught up.** While the gateway is down, no agent job runs. When it returns, a recurring job runs once for all its missed slots and logs `missed ×N (collapsed into one catch-up run)`. This is the default, `cron.catch_up_missed: True`. A job's own `catch_up: false`, or an explicit `grace_minutes` on the job, skips missed runs instead. (V-run)
- The model is resolved at fire time: a per-job pin, then `cron.model`, then the main model.
- Sessions started by cron cannot create more cron jobs unless `cron.allow_agent_scheduling` relaxes it.
- **Delivery targets:** `origin`, `local`, named platforms (Telegram, Discord, Slack, WhatsApp, Signal, Email), specific chats or topics, `bot-chat`, and `all`. Targets can be combined.
- **Pre-run script:** a `script` in `$HERMES_HOME/scripts/` runs before the agent. Printing `{"wakeAgent": false}` as the last line skips the agent at no token cost.
- **Silent runs:** a final response containing `[SILENT]` suppresses delivery. Failed runs always deliver.
- Script timeout defaults to 3600 seconds. Toolsets can be restricted per job, and `cron.max_parallel_jobs` caps concurrency.

### The gateway and messaging

- The gateway is one background process that connects to the configured chat platforms, handles sessions, runs cron jobs and delivers voice messages.
- **Platforms listed:** Telegram, Discord, Slack, Google Chat, WhatsApp (Baileys bridge and Cloud API), Signal, SMS, Email, Home Assistant, Mattermost, Matrix, DingTalk, Feishu/Lark, WeCom, Weixin, BlueBubbles and Photon (iMessage), QQ, Yuanbao, Microsoft Teams, LINE, ntfy, SimpleX, IRC, Buzz, Raft.
- **Running it:** `hermes gateway` in the foreground; `hermes gateway setup` is a wizard; `hermes gateway install` registers a service (systemd on Linux, launchd on macOS, a Scheduled Task on Windows).
- **Home channel:** set with `/sethome` or `home_chat_id`; used for restart notifications and operator alerts.
- **Sessions** are per chat and persist until `/new` or `/reset`. Time-based resets need a separate plugin.
- **Access:** users not on an allowlist or paired through DM are denied by default.
- The gateway appears to work through bot accounts, not by reading the owner's personal chats. (I; the page does not state it.)

### Memory and sessions

- **Built-in memory:** `MEMORY.md` (2,200-character limit) and `USER.md` (1,375-character limit) in `~/.hermes/memories/`, scoped per profile. The agent writes through a `memory` tool with `add`, `patch` and `remove`. Entries load at session start as a frozen snapshot.
- **Background review:** after a turn, a background review may save memory or update skills. Its frequency is set by `memory.nudge_interval` and `skills.creation_nudge_interval`.
- **Session storage:** all CLI and messaging sessions are in SQLite at `~/.hermes/state.db`, in WAL mode with FTS5 full-text search.
  - `sessions` holds metadata: id, source platform, user id, model, a system prompt snapshot, token counts, start and end times, and a parent session id for compression splits.
  - `messages` holds role, content, tool calls, tool name and token count.
- **Session ids** follow `YYYYMMDD_HHMMSS_<hex>`. Each session is tagged with a source such as `cli`, `telegram`, `discord`, `cron` or `tool`.
- **`session_search`** runs FTS5 queries with no LLM calls.
- **Resume:** `hermes -c`, `hermes --resume <id|prefix|title|latest>`, and `--in <dir>`.
- **Export:** `hermes sessions export` writes JSONL with every stored message; other formats are for display. `--redact` scrubs secrets.
- **Retention:** `hermes sessions prune` deletes ended sessions (90-day default). Auto-prune is off by default.

### The memory provider limit

- **Only one external memory provider can be active at a time.** Built-in memory always runs alongside it. A second provider is rejected with a warning.
- **Base class:** `MemoryProvider` in `agent/memory_provider.py`.
- **Required:** `name`, `is_available()`, `initialize(session_id, **kwargs)`, `get_tool_schemas()`, `handle_tool_call(tool_name, args, **kwargs)`, `get_config_schema()`, `save_config(values, hermes_home)`.
- **Optional:** `system_prompt_block()`, `prefetch`, `queue_prefetch`, `sync_turn`, `on_session_end(messages)`, `on_pre_compress(messages)`, `on_memory_write`, `shutdown()`, `on_profile_created`, `on_profile_renamed`.
- **Rules:** `sync_turn()` must not block; threads must be started with `spawn_context_thread`; storage paths must come from the `hermes_home` argument; no module-level singleton.
- **Lifecycle when a provider is active:** Hermes injects its context into the system prompt, prefetches before each turn, syncs turns after each response, extracts at session end where supported, mirrors built-in memory writes, and scrubs secrets from what it sends.

### Extending the dashboard

- `hermes dashboard` starts a local server at `http://127.0.0.1:9119`. Pages: Status, Chat, Config, API Keys, Sessions, Logs, Analytics, Cron, Profiles, Skills, MCP, Webhooks, Pairing, Channels, System.
- The Chat tab needs a POSIX terminal, so on native Windows it needs WSL2.
- Binding to a non-loopback address turns on an auth gate, which fails closed if no provider is configured.
- **A dashboard plugin** lives in a `dashboard/` subfolder of the plugin directory:
  - `manifest.json` (required): `name`, `label`, `tab.path`, and optionally `tab.position`, `tab.override`, `tab.hidden`, `slots`, `icon`, `entry`, `css`, `api`.
  - `dist/index.js` (required): a prebuilt single-file bundle. React is not bundled; it comes from `window.__HERMES_PLUGIN_SDK__`.
  - `plugin_api.py` (optional): exports a FastAPI `router`, mounted under `/api/plugins/<name>/`, with auth applied.
- Components register through `window.__HERMES_PLUGINS__.register(name, Component)` and `registerSlot(name, slot, Component)`.
- **Slots:** shell-wide (`backdrop`, `header-left`, `header-right`, `header-banner`, `sidebar`, `pre-main`, `post-main`, `footer-left`, `footer-right`, `overlay`) and page-scoped (`<page>:top`, `<page>:bottom`).
- **Limits:** backend routes mount once at startup; dashboard plugins are distributed by cloning a git repository into `~/.hermes/plugins/`, and pip distribution is not supported; the native desktop app uses a separate SDK and shares only the backend routes.

### Kanban

- Hermes Kanban is a durable task board shared across Hermes profiles. It is primarily a work queue for agents, not a personal to-do list.
- Boards are independent, each with its own SQLite database; the default is `~/.hermes/kanban.db`.
- Tasks have a title, body, one assignee (a profile name) and a status: `triage`, `todo`, `ready`, `running`, `blocked`, `review`, `done`, `archived`.
- A dispatcher in the gateway checks about every 60 seconds and spawns a worker process for each ready task.
- Agent tools are named `kanban_*`. Humans use `hermes kanban`, `/kanban` and a dashboard tab.

### MCP, ACP and the API server

- **Hermes as an MCP client:** servers are defined under `mcp_servers` in `~/.hermes/config.yaml`, over stdio or HTTP, with OAuth, tool filtering and sampling. Tools register as `mcp_<server>_<tool>`.
- **Hermes as an MCP server:** no longer built in. It ships as the official `mcp-serve` plugin, started as `hermes mcp-serve`. It lets clients list and read messaging conversations, wait for and send messages, and optionally call some Hermes tools.
- A plugin that wants to serve its own data to other Harnesses would therefore need its own MCP server. (I)
- **ACP:** Hermes can run as an ACP server (`hermes acp`) inside VS Code, Zed, JetBrains and Buzz. Messaging delivery and cron management are excluded from its toolset.
- **API server:** an OpenAI-compatible endpoint at `http://127.0.0.1:8642` by default, with a required bearer key. It exposes chat completions, responses, runs, jobs at `/api/jobs` and sessions at `/api/sessions`. Every request runs the agent with its full toolset.

### Skills, the curator and delegation

- **Skills** are `SKILL.md` documents in `~/.hermes/skills/`, loaded on demand; each works as a slash command. The agent creates and edits them with `skill_manage`. Plugins can bundle skills.
- **The curator** is background maintenance for agent-created skills. It is triggered by inactivity, not cron: by default every 168 hours after 2 idle hours. An LLM consolidation phase is off by default. It never deletes; it archives.
- **Delegation:** `delegate_task` spawns child agents with a fresh conversation. Defaults: 10 concurrent children, depth 1, 250 iterations. Children cannot use `memory`, `send_message` or `cronjob`.

### Bundled plugins and integrations

- **Bundled plugins, all disabled by default:** `disk-cleanup`, `security-guidance`, `teams_pipeline`, `google_meet`, `image_gen/openai`, `image_gen/openai-codex`, `image_gen/xai`, `hermes-achievements`, `kanban/dashboard`.
- None of them provides memory, goals, reminders or briefings.
- **Integrations page:** lists AI providers, MCP, web search backends, browser backends, voice, ACP, the API server, memory, messaging platforms and Home Assistant. It lists no calendar, note-taking, task-manager or health-data integration.
- The plugin catalog page separately mentions 54 connectors, including Slack, Notion, Linear, Jira, Airtable, Stripe, Vercel and Sentry.

### What the docs did not say

- **Plugins page:** it does not document a call for registering a dashboard tab, and it does not list cron as something a plugin can register. It says 27 hook events exist but lists 37; the hooks page lists more.
- **Cron page:** it does not say whether a plugin can register cron jobs. The last 1,090 characters of the page were not read.
- **Hooks page:** the last 49,851 characters were not read.
- **Kanban page:** the last 37,799 characters were not read.
- **User stories page:** about 395 stories are listed; the last 81,934 characters were not read.
- **Memory providers page:** it describes behaviour, not method names; those came from the developer guide. A method named `on_delegation` could not be confirmed. No provider is described as offering recall by date.
- **MCP page:** it does not list the exact tools of `mcp-serve` or explain its `--tools` mode.
- **ACP page:** it does not say whether Hermes can drive other ACP agents, such as Claude Code or Codex, as a client.
- **Delegation page:** it does not say whether Hermes can delegate to Claude Code, Codex or ACP agents. It links to a "Delegation Provider Plugins" guide and a "Codex App-Server Runtime" page, neither of which was read.
- **Messaging page:** it does not fully define the home channel, does not document the `send_message` tool, and does not say whether the gateway can read the owner's personal WhatsApp or Telegram chats as the owner's own account.
- **API server page:** no dedicated memory endpoint and no standalone search endpoint appear.
- **Curator page:** it does not say whether a plugin can hook into the curator or reuse its scheduling.
- **Skills page:** it only links to details on the curator and on how plugins bundle skills.
- **Dashboard page:** it does not describe the manifest or bundle format (those came from the extending guide), does not mention Kanban, and has no separate Memory page.
- **Plugin catalog:** the page reports 977 plugins; only about 25 were visible. The catalog was searched by topic, not read in full, so a close match to Atlas could exist.

## 6. Existing plugins and memory providers that overlap

### What Hermes itself has

| What | Covers | Lacks for Atlas |
|---|---|---|
| Built-in memory and `session_search` (V-docs) | Small notes about the user; full-text search of Hermes sessions | Hermes only; no Pursuits, no push |
| Bundled plugins (V-docs) | Agent Kanban, achievements, meeting summaries, image backends | None touch memory, goals or reminders; Kanban is a queue for agents |

### Memory providers

All (V-docs). Nine are listed. RetainDB and ByteRover ship with Hermes but were due to leave core on 2026-10-15; the other seven install from the plugin catalog.

| Provider | What it remembers | Storage | Tools |
|---|---|---|---|
| Honcho | A model of the user through "dialectic reasoning"; session summary, user representation, peer cards | Cloud or self-hosted | 5 |
| OpenViking | A filesystem-style knowledge hierarchy, loaded in tiers; six memory categories extracted on session commit | Self-hosted | 6 |
| Mem0 | Facts extracted by an LLM on the server, with semantic search and deduplication | Cloud, self-hosted or in-process | 4 |
| Hindsight | A knowledge graph with entity resolution; retains full turns including tool calls | Cloud or local | 3 |
| Holographic | Facts in local SQLite with full-text search, trust scoring and contradiction detection | Local only | 2 |
| RetainDB | Seven memory types with hybrid search | Cloud, paid | 10 |
| ByteRover | A hierarchical knowledge tree; extracts insights before context compression | Local, optional cloud sync | 3 |
| Supermemory | Semantic memory with profile recall; turns grouped per session per 4-hour window | Cloud or self-hosted | 4 |
| Memori | Structured long-term memory with project and session attribution | Cloud | 5 |

What they all lack for Atlas: no date-based recall is described, none tracks a Status or how to resume, and none is proactive beyond recalling into a conversation. (V-docs for what the page describes; I for "all lack")

### Plugins

| Plugin | Covers | Lacks for Atlas | Mark |
|---|---|---|---|
| Memory Wiki (official, `hermes-memory-wiki`) | Dashboard tab with subject pages, daily logs, recent activity and a read-only audit of `MEMORY.md` and `USER.md` | Reads only Hermes's own `state.db` and memory files; no LLM calls; read-only; registers no tools or hooks; nothing proactive; each gateway serves its own data | V-docs |
| agent-importers (official) | Imports Claude Code and Codex configuration and memory files into Hermes | Does not import conversations | I (search summary only) |
| deja-memory (community) | A memory provider backed by the `deja` binary; answers from Claude Code, Codex, Cursor and Hermes sessions on the machine, including history from before it was installed; tools `deja_recall`, `deja_fix`, `deja_blame`; injects a digest at session start | One machine; no separate Pursuit or Status tracking; depends on a separately installed binary; occupies the single memory provider slot | V-docs |
| memex-hermes (community, v0.2.2, beta) | Stores Hermes conversations verbatim in a local SQLite file shared with `memex-mvp`, which captures Claude Code, OpenClaw, Telegram exports, Cursor and others; tools `memex_search`, `memex_get`, `memex_recent` | One file per machine, with sync on its roadmap; keyword search only; raw text, no extraction; no Pursuit tracking | V-docs |
| pastdotdev (community) | A memory provider that recalls from past conversations and sends each ended session to the past.dev service, with common secret patterns redacted | Only Hermes is mentioned; storage, timeline, resume, tracking and pricing are not described | V-docs |
| Supermemory plugin | Ingests full sessions when they end; Hermes memories appear alongside Codex and Claude Code in its app | Fact recall; no Pursuit state | I (search summary only) |
| Cognee plugin | Starts or attaches to a local cognee server shared with cognee plugins for Claude Code, Codex and OpenClaw | Not examined further | I (search summary only) |
| A plugin listed under the author `gamaze-labs` | Described as distilling sessions across machines and Harnesses into one shared store | Could not be verified: the author page fetch returned a different publisher's plugins. One GitHub star, first listed 2026-10-02 | I (search summary only) |
| `hermes-plugins` (community collection) | Goal management, among other things | Not opened | I (search summary only) |
| `hermes-life-os` (community, experimental) | Learns daily routines | Not opened | I (search summary only) |
| OMH (community) | Routing hints, plan todos, a status tool, a Desktop status pane | Partial match on tracking only | V-docs |

Other details recorded about these:

- **deja-memory:** the listing showed "1.4k", version 0.1.0, added about a month before. Its benchmark figures are the project's own and were not verified. The page does not list the other agents it reads, say how it ranks results, address use across machines, or state a license.
- **Memory Wiki:** the documentation URL `/docs/plugins/memory-wiki` returned 404; the marketplace page was read instead. A search summary claimed its sources include Claude Code's folder; the marketplace page does not say that.

### Related setups described in user stories

From the user stories page, which lists about 395 stories and was cut off. All (V-docs) as descriptions of what users report; none was checked.

- **Importing Sessions from other Harnesses:** no visible story describes importing Claude Code or Codex Sessions into Hermes. The closest are a memory shared through one Obsidian vault by Hermes, Claude Code, Codex and Grok; a Claude-to-Hermes MCP bridge; a Hermes MCP server that Claude Desktop can call; and Codex used as a runtime monitor for Hermes workflows.
- **A manager or chief of staff:** a chief-of-staff agent coordinating other agents through a Linear MCP server; a chief-of-staff agent that works out the owner's goals and delegates to specialist profiles; an orchestrator that grades its workers' output; a daily "boss" agent that gives three tasks each day and keeps a memory of the business.
- **Nightly review:** a 3 AM cron job that reads the day's conversations, extracts decisions, projects and mistakes, and writes context for the next day, built from more than 30 custom skills.
- **Daily briefings:** several, delivered by cron to Telegram, Discord, Slack, Notion, email, Obsidian and local Markdown.
- **Second brain and wiki:** a self-improving LLM wiki with a Telegram front end; forwarded emails filed into a company wiki; an Obsidian vault shared by four agents; a daily log skill that journals into Obsidian.
- **Tracking work:** a custom MCP server for tasks, clients and invoices; a Kanban board for seven agents; a dashboard plugin for phones showing activity, projects, Kanban, cron and profiles.
- **Reminders:** Telegram reminders; agents that schedule their own wake-ups.
- **Memory technology mentioned:** SQLite FTS5 stores, Hindsight, Graphiti, MemPalace, Mem0 with Qdrant, and local layers named Reverie-Core and Synapse.

These are setups assembled by individual users from skills, cron jobs and profiles. None is described as a plugin that does what Atlas is meant to do. (I)

### Conclusion drawn from this section

- Finding past conversations across Harnesses on one machine is already done by existing plugins. (I)
- No examined plugin keeps the state of what the owner has started (Status, where they left off, next Step) and pushes them on it. (I)
- The only cross-machine claim found is the unverified `gamaze-labs` listing. (I)

This conclusion is recorded as [ADR 0001](../adr/0001-manager-first-memory-second.md).

## Sources

Hermes documentation:

- [Features overview](https://hermes-agent.nousresearch.com/docs/user-guide/features/overview)
- [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Built-in plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins)
- [Persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [Memory providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers)
- [Memory provider plugins (developer guide)](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [Scheduled tasks (cron)](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)
- [Event hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)
- [MCP integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp)
- [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [Web dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
- [Extending the dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard)
- [Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)
- [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator)
- [Subagent delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation)
- [Sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions)
- [Messaging gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/)
- [Integrations](https://hermes-agent.nousresearch.com/docs/integrations/)
- [User stories](https://hermes-agent.nousresearch.com/docs/user-stories)
- [Import from other agents](https://hermes-agent.nousresearch.com/docs/user-guide/import-from-other-agents) (search result, not opened)

Plugin listings:

- [Plugin catalog](https://hermes-agent.nousresearch.com/docs/plugins)
- [Memory Wiki (marketplace)](https://hermes-agent.nousresearch.com/marketplace/hermes-memory-wiki)
- [Memory Wiki (docs URL, returned 404)](https://hermes-agent.nousresearch.com/docs/plugins/memory-wiki)
- [deja-memory](https://hermes-agent.nousresearch.com/docs/plugins/deja-memory)
- [pastdotdev](https://hermes-agent.nousresearch.com/docs/plugins/pastdotdev)
- [agent-importers](https://hermes-agent.nousresearch.com/docs/plugins/agent-importers) (search result, not opened)
- [gamaze-labs author page](https://hermes-agent.nousresearch.com/docs/plugins/by/gamaze-labs) (fetch returned another publisher's plugins)

Outside the Hermes site:

- [memex-hermes on PyPI](https://pypi.org/project/memex-hermes/)
- [Supermemory for Hermes](https://supermemory.ai/changelog/supermemory-for-hermes/) (search result, not opened)
- [Cognee Hermes integration on PyPI](https://pypi.org/project/cognee-integration-hermes-agent/1.2.0/) (search result, not opened)
- [Awesome Hermes Agent list](https://awesome.ecosyste.ms/projects/github.com%2FSamurAIGPT%2Fawesome-hermes-agent) (search result, not opened)
- [Hermes Agent ecosystem catalog](https://ayush7614.github.io/agentic-ai-ecosystem/guides/awesome-hermes-agent/ecosystem/) (search result, not opened)
- [hermes-plugins](https://github.com/42-evey/hermes-plugins) (search result, not opened)
- [hermes-life-os](https://github.com/Lethe044/hermes-life-os) (search result, not opened)
- [Hermes as the engine of a second brain](https://okikusan-public.pages.dev/hermes-agent-second-brain-engine.en) (search result, not opened)
- [NVIDIA chief-of-staff playbook](https://build.nvidia.com/playbooks/agents-for-productivity) (search result, not opened)
