---
status: accepted
---

# The record is Markdown files that are their own source of truth

ADR 0002 requires that the stored record itself be understandable without Atlas's code. We decided the truth is a folder of Markdown files with YAML front matter: one file per Pursuit, per Journal day, per sent Daily Brief or Review, per Session (a readable transcript beside a verbatim zstd copy of the raw store), per day and origin of Notes, plus `profile.md`, `settings.md`, `pauses.md`, `forgotten.md` and a `README.md` that explains the marks. Every statement line ends with its mark: `[F <label> · <time> · <evidence id>]` for a Fact read from Evidence, `[F counted · <rule> · <n> Sessions]` for a figure counted from Evidence by a fixed rule (D Q9), or `[I · <evidence ids>]` for a judgment. Every text Atlas renders into the record masks detected secrets, and only the verbatim `raw/` copies keep them (D Q4). Everything under `.atlas/` (index, model-output cache, last-written mirror, locks) is machinery that can be deleted and rebuilt from the files.

## Considered Options

- **An append-only JSONL change log as the truth, with Markdown files rendered from it** (arena candidate 2). Rejected. Readers would meet two representations of one fact and could not tell which is current. Forget would have to rewrite a log promised to be append-only. Restoring a hand edit would need a second sealed copy of the log, kept in Hermes's data.
- **JSON snapshots, switched atomically and owned by a service** (candidate 3). Rejected. JSON is harder to read than Markdown, and nothing in the snapshot keeps the history the Review reports.
- **SQLite as the truth, with a Markdown export.** Rejected. The readable part would be a copy that can lag behind the truth, and "restore a hand-edited file" would lose its meaning.

## Consequences

- Each Pursuit file carries its own History section. That section is the change account the Review and the Board marks are built from, so no separate event log exists.
- Things derived from the files are never stored: Neglected, quiet days, last worked, the offer counts (taken from the brief files) and the Board marks.
- The marks notation is part of the file format. Changing it means migrating every file.
