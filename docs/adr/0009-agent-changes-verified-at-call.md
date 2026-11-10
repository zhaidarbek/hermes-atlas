---
status: accepted
---

# An agent's change to a Pursuit is checked against its own live Session before it applies

R4 Q4 lets an agent change a Pursuit only when the owner told it to in that Session. We decided the MCP server establishes the calling Session from the calling process, never from what the agent says:

- Claude Code: `~/.claude/sessions/<parent pid>.json` gives the `sessionId`.
- Hermes tools: the `session_id` argument.
- Codex: the `_meta.threadId` Codex adds to each call ([ADR 0010](0010-codex-caller-bound-by-call-metadata.md)).
- Devin: no binding in v1.

The server then reads that Session from the Harness store, where the owner's prompt is already written. It looks for the owner's words in a message the owner typed. Only when it finds them does the change apply, carrying a quote of the instruction in the History line. Otherwise the request is refused and kept as a Note.

## Considered Options

- **Apply at once, verify on a later tick by cwd and time window, revert if not found** (all three arena candidates). Rejected. A different Session in the same folder could vouch for the change, and an unauthorised ending would stay visible for up to a day.
- **Queue every agent change for the owner to confirm.** Rejected, because it contradicts the tutorial, where the agent's update applies when the owner asked for it.

## Consequences

- When Atlas cannot confirm the owner's words, the request becomes a refused-update Note that quotes them. Atlas's next answer to the owner offers "apply", which makes the change through Atlas (D Q12).
- Verifying the Codex binding is a condition of releasing v1. Devin may stay Notes-only in v1.
- An agent may end a Pursuit only when the confirmed words themselves name the ending ("done", "close it", "drop it"). A finished Step is not enough (D Q11).
