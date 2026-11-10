---
status: accepted
---

# Sessions are captured passively from each Harness's own store and keyed by canonical identity

We decided Atlas collects Sessions by reading each Harness's own store read-only. It finds the stores through environment overrides, then platform defaults, then roots the owner adds in settings. It never decides from a binary name on PATH. Each Session is copied verbatim and compressed into `evidence/`, so it survives the Harness's own cleanup. Its identity is (Harness, account namespace, native id), never a path or an Interface. An Interface's lineage folds delegated children into the Session that started them. T3 Code is the case that matters: its `app_owned` children appear in Claude Code, Codex and Devin as ordinary top-level Sessions. Hermes's own Sessions come from its `state.db`, the same way as any other Harness, and Hermes's session hooks are not used. MCP is not a capture path. Atlas ships its own stdio MCP server (`hermes atlas mcp`) with three tools: read, note, and update when told. Hermes's own MCP surfaces never expose plugin tools.

## Considered Options

- **Capture through hooks or agent cooperation.** Rejected. `on_session_end` fires every turn, `on_session_finalize` skipped cron and Telegram sessions, and agents forget to report.
- **Using T3 Code's database as the primary record.** Rejected. It prunes itself and holds only some of the work.
- **Dropping duplicates by content similarity.** Rejected, because it would merge distinct work.

## Consequences

- Each new Harness needs an adapter and fixtures, which is the seam for future Sources. ChatGPT and claude.ai come from data exports dropped into `inbox/`, through a parser that tolerates format versions. Neither vendor documents its export format, so that seam is unverified.
- Forget records a one-way hash of the canonical key, so a forgotten Session is not collected again from the store the Harness still holds. The owner accepted this, with no "unforget" (D Q14).
- An export file is deleted only after every conversation in it is committed. A file that fails is kept and reported (D Q5).
