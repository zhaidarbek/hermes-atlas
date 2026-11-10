# Atlas's record is open to read and guarded to write

Atlas lives in Hermes, a tool the owner had never used when this was decided, and its record is meant to outlast any one tool. We decided that anything can read the record directly, with neither Hermes nor Atlas running, while every change goes through Atlas so that its rules hold: only the owner ends a Pursuit, an agent changes a Pursuit only when told to, and every statement is marked as a Fact or an Inference.

## Considered Options

- **Keep the record inside Hermes's own storage.** Rejected: the record would be lost or locked away if the owner stops using Hermes.
- **Let any agent read and write the record directly.** Rejected: direct writes bypass the rules above, and a record the owner cannot trust defeats a manager that files on its own.

## Consequences

Making the storage swappable in code does not satisfy this decision; the stored record itself must be understandable without Atlas's code.
