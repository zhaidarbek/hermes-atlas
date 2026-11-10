---
status: accepted
---

# Messages leave through `hermes send` and the owner's replies are claimed by gateway hooks

A plugin has no supported call that sends a message on its own schedule. `ctx.post_message` needs a live session, and `inject_message` starts an agent turn. We decided on two paths.

**Outbound.** The tick sends the Daily Brief and Review with `hermes send --to <home channel> --json`. That command needs no running gateway for bot-token platforms. Its JSON output includes the platform message id, which Atlas records in the brief file.

**Inbound.** A cheap `pre_gateway_dispatch` hook remembers which Atlas message a reply quotes. `post_gateway_admission` then claims messages addressed to Atlas, answers them through `Atlas.handle`, and returns the reply, so Hermes's agent never sees them. `/atlas …` is the explicit fallback.

## Considered Options

- **Agent cron jobs that compose the brief with a prompt.** Rejected. They give no message id, their output varies from run to run, they add cost, and the Hermes sessions they create would read as work.
- **A separate Hermes profile or bot for Atlas.** Rejected for v1. It is heavy setup for strangers, and injection cannot cross profiles.
- **Buttons sent through a Telegram handler inside the gateway.** Rejected for scheduled messages, because those are sent from outside the gateway. The owner accepted a typed `1 prompt` (D Q2).

## Consequences

- Claimed replies and `/atlas` commands never reach Hermes's `state.db`. Rows mirrored by `hermes send` are dropped by matching the sent brief files. Atlas's own conversations are therefore told apart structurally (W Q14).
- One setting, `channel.atlas_chat`, decides both where the Brief and Review go and which chat is Atlas's own (D Q16). Unset means the Hermes home channel, shared with Hermes's agent. There a message is Atlas's if it replies to Atlas, starts with "atlas", or comes within 10 minutes of an exchange with Atlas (D Q1).
- A claimed answer must fit the hook's 30-second budget. Past about 25 s, Atlas replies at once that it is looking, and a gateway worker thread sends the answer with `hermes send` (D Q8).
- Delivery is at least once and at most twice. When an attempt's arrival is unknown, Atlas retries once the same day, and a reply to either copy counts (D Q13).
