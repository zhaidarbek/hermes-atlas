---
status: accepted
---

# A Codex agent's call is bound to its Session by the metadata Codex adds to each call

ADR 0009 says Atlas finds the Session behind an agent's `atlas_update` from the calling process, never from what the agent says. For Codex we bind through the `_meta` that Codex itself puts on every MCP `tools/call` request. `_meta.threadId` names the Session, and it equals the id in the Session's rollout file under `~/.codex/sessions/`. The agent writes only the call's `arguments`, never its `_meta`. When `_meta.threadId` is missing, or no rollout file matches it, the update becomes a refused-update Note, as before (D Q12).

This was verified live on 2026-11-08 with codex-cli 0.167.0 and `codex exec`, including a resumed Session, which keeps its id. Two paths are still unverified, and checking them is part of the v1 release condition: the interactive TUI, and Codex run through T3 Code's app server.

## Considered Options

- **The rollout file the parent process holds open** (`/proc/<pid>/fd`). Rejected as the main binding. Codex opens the file only lazily, and one app-server process can hold several Sessions open, so the file is ambiguous exactly where T3 Code runs Codex. It remains a cross-check.
- **Environment variables.** Rejected. Codex passes no Session id to the server, and other Harnesses pass on whatever launched them: a Devin run from Claude Code inherited Claude Code's Session variables.
- **Keep Codex updates as refused Notes in v1.** Rejected by the owner (G2). It would have dropped the release condition in D Q12.

## Consequences

- Atlas trusts a field written by the Codex client. A process that pretends to be Codex could name another Session, but it would still need the owner's typed words in that Session's own store before anything applies.
- `_meta` keys are undocumented and may change between Codex versions. The Codex adapter pins the versions it has verified, and on any other version, or a missing key, it falls back to a refused-update Note.
- Devin sends no Session id in its calls, so Devin stays Notes-only in v1 (D Q12).
