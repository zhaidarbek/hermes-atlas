# Atlas is a manager of Pursuits first and a searchable memory second

Finding past conversations across Harnesses on one machine is already solved by existing Hermes plugins (deja-memory, memex-hermes, the memory providers), and none of them keep the state of what the owner has started or push them to continue it. We decided Atlas's product is the set of Pursuits, each with a Status, a Resume Note and a next Step, kept current without the owner's effort and pushed through the Daily Brief; search supports that and may lean on existing tools.

## Considered Options

- **Build Atlas as a Hermes memory provider.** Rejected: providers recall facts into a conversation, have no notion of Status or resuming, and only one can be active at a time.
- **Adopt an existing cross-Harness recall plugin and stop there.** Rejected: it answers "find the conversation" but not "where did I leave off" or "what have I neglected", and nothing in it is proactive.

## Consequences

A feature that makes recall better but does not improve the Pursuits or the Daily Brief is out of scope by default.
