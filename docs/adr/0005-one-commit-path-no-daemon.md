---
status: accepted
---

# Every Atlas process writes through one commit, and no Atlas daemon runs

Hermes loads the plugin into every one of its processes: the gateway, each `hermes atlas …` run (the cron tick among them), the dashboard backend, and the stdio MCP server that each Harness launches. Hermes gives a plugin no start hook and no timer. We decided any of these processes may write, but only through `record.commit`.

`record.commit` works in four steps:

1. Take an OS file lock for milliseconds.
2. Finish any half-applied transaction and absorb hand edits.
3. Compare each file the change touches with the version its snapshot read.
4. Stage the new files, mark the transaction ready, and apply it.

A commit is all-or-nothing across files. Merge, split and Forget all depend on that. Model calls always run before the lock. When the compare step finds a change, the pure rules plan again on a fresh snapshot.

## Considered Options

- **One long-running Atlas service that owns all writes, reached over a local socket** (arena candidate 3, and the cross-judge's preferred base). Rejected. Every stranger would have to install and supervise a systemd or launchd unit. Hermes has no hook that could start the service. Writes from the CLI and MCP would stop whenever the service was down. The lock-and-compare commit gives the same guarantee with nothing left running.
- **Per-process outbox files, merged by the tick.** Rejected. An agent's update would only appear after the next tick, and some rules span actors, such as "one brief per day" and "Forget against the plan shown". Those still need serialising.

## Consequences

- The record must be on a local filesystem. Setup refuses a network mount, where `flock` is unreliable.
- Each commit also checks for hand edits, so every writer enforces W Q15.
