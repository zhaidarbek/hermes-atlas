# Record layout. All content is invented, taken from the tutorial's invented week.

The files are the record. Everything outside `.atlas/` is readable with `ls`, `less` and `zstd -d`, and `README.md` explains it. Everything inside `.atlas/` is Atlas's machinery. The index and the cache can be deleted at any time. The last-written mirror is needed only to restore hand edits.

```
~/atlas/
├── README.md                     how to read this folder: kinds of files, the [F]/[I] notation, Evidence ids
├── settings.md                   times, gap, Channel, Harnesses found and added, exclusions
├── profile.md                    Areas and what Atlas learned about the owner
├── pauses.md                     every Pause, with its end
├── forgotten.md                  when something was forgotten, never what
├── pursuits/
│   ├── billing-export-service.md
│   ├── billing-export-staging-deploy.md
│   ├── greenhouse-logger.md
│   ├── keyboard-firmware.md
│   └── ...                       one file per Pursuit, any Status
├── journal/
│   ├── 2026-08-14.md
│   └── 2026-11-16.md             one file per day
├── briefs/2026/11/
│   ├── 2026-11-16.md             each Daily Brief exactly as sent, plus what it offered (the sent ledger)
│   └── 2026-11-20.md
├── reviews/2026/2026-W47.md      each Review as sent
├── notes/2026/11/
│   ├── 2026-11-15-telegram.md    the owner's conversation with Atlas that day (Notes + Atlas's answers)
│   ├── 2026-11-17-claude-code.md Notes added by agents in Claude Code that day
│   ├── 2026-11-20-board.md       the owner's Board clicks (Status, Focus) as one-line Notes
│   └── 2026-11-20-hand-edits.md  hand edits Atlas restored, with the edited text
├── evidence/
│   ├── claude_code/2026/11/cc-spjgwqujw7ejpbl7/
│   │   ├── session.md            readable transcript (regenerated from raw/; detected secrets masked)
│   │   ├── card.md               one-paragraph card (all Inference)
│   │   └── raw/
│   │       ├── main.jsonl.zst                verbatim copy of the Harness file
│   │       └── subagent-a1b2c3.jsonl.zst     folded subagent
│   ├── codex/2026/11/cx-acdo6557j7vy6xfl/ ...
│   ├── chatgpt/2026/11/gpt-hmacgassx2oybydn/ ...
│   └── hermes/2026/11/hm-4tfy4bqstbte5mr7/ ...
├── inbox/                        drop ChatGPT / claude.ai export zips here (emptied after import)
└── .atlas/                       machinery (hidden)
    ├── write.lock                flock: one commit at a time across all processes
    ├── tick.lock                 flock: one collect/file pass at a time
    ├── send.lock                 flock: one process sends at a time, so a `sending` brief it finds was left by a crash
    ├── HEAD                      commit counter
    ├── txn/                      staged commits; *.ready manifests are rolled forward on open
    ├── swept                     marks the one-off sweep of empty evidence folders as done
    ├── sweeping                  a sweep under way may have left removals unsynced; the next one syncs every folder
    ├── masking/                  the masking under way of the files older versions wrote bare: the last step, the staged batch and the files a link holds
    ├── masked                    marks the masking of the files older versions wrote bare as done
    ├── last-written/             byte copy of every mirrored record file as Atlas last wrote it
    ├── index.sqlite              disposable: FTS, memberships, backlinks, cursors, stat cache
    ├── cache/                    disposable LLM outputs, tagged by Evidence id
    ├── seen.json                 Board seen marks, JSON {pursuit id: newest History id seen}; committed with the record, an unreadable file or value reads as no mark; a mark only moves forward
    └── pending/forget.json       a Forget plan waiting for "forget": the Session ids and when asked (expires after 1 h)
```

Every text Atlas writes into a record file is masked by `privacy.scrub` first, so a detected secret reads `[secret: <kind>]` (D Q4). Each text is masked on its own, before it is joined to the file's structure, and a mark may hold a mask, as a pattern's description can. Areas are masked too: the owner names them, and every file that holds one masks it the same way. Kept as they are: ids, times and fixed words, and what something outside the file must match exactly: a raw part's name, sha256, fingerprint and origin, a Session's namespace and native key and a folded part's key, a sent message's ids and text hash, a Note's platform message id, the skip keys of `forgotten.md`, and `settings.md`, whose roots, exclusions and chat Atlas acts on. `raw/` keeps the bytes as they were. A file written before Atlas masked is written again through the codec by the opens after the upgrade, under the write lock, each open for about a second with every sync counted, so a large record holds the lock briefly and is done after some opens. The files whose names and Areas other files match (the top files and Pursuits) are staged in `.atlas/masking/` across opens and written in one transaction, so names and Areas change together; briefs, Notes and evidence files follow in path order, one transaction per open. A write replaces a file only while it holds the bytes the pass read, so an edit saved meanwhile is kept, and a staged copy is checked against the sha256 the state keeps of it before any file is replaced. A sealed file whose seal fails, a `session.md` that is not as an Atlas that did not mask wrote it, and a file or folder Atlas cannot open or read are left as they are, with their last-written copies, and counted in the log by error type. The pass reaches files through the record root and the folders above a Session's folder when they are links, as a commit does. It lists only the folders the layout names on the way to the files it masks, a Harness's folder by its id, and never a folder inside itself, by device and inode, so a link loop costs one listing. A step is a file it reads or a folder it lists, so the listing counts in the open's second, and the next open goes on after the last step. It never follows a link where a Session's folder, its mirror, a folder in them or a file in them is, also one that takes that place once the pass has staged the file: it leaves the file, logs `Linked` and does not write `.atlas/masked`. Once the pass has walked the whole record, `.atlas/masking/state` keeps the files a link held, and each open after it reads only those, without listing a folder, until no link holds one. It then walks the whole record again, so it also masks a file an older Atlas still running wrote bare meanwhile, and writes `.atlas/masked` once a walk meets no link. So a link that stays costs each open a few system calls, and the log names it again only when the files it holds change. A garbled `.atlas/masking/` starts the pass over. A Forget deletes the staged copies of the files it writes, which may hold the text it removes, before it is staged, fails when one cannot be deleted, and starts the pass over. Any other commit deletes them as it is staged and logs a copy it cannot delete by error type: the pass passes over that file, which no longer holds what it read. `.atlas/masked` stops the pass for good. A Note's Pursuit is found by its name masked, and a text sent bare is still recognised once its file holds it masked.

---

## README.md (excerpt)

```markdown
---
atlas: readme/1
sealed: sha256:51c0…
---
# Atlas record

Atlas keeps this folder. Read anything; change things by talking to Atlas. A file edited
here by hand is put back, and your text is kept as a Note in notes/; reply "apply" to make
the change through Atlas.

## Marks
Every statement ends with a mark.
- `[F Codex · 2026-11-16 08:05 · cx-acdo6557j7vy6xfl]` is a Fact: read directly from that Evidence at that time.
- `[F counted · active_time · 5 Sessions]` is a Fact counted from Evidence by a fixed rule (Active Time,
  quiet days, numbers of Steps or Sessions). The file lists those Sessions in its front matter: a
  Journal day under `evidence:`, any other file under `counted:`, one entry per mark in body order.
- `[I · cx-acdo6557j7vy6xfl, cc-spjgwqujw7ejpbl7]` is an Inference: Atlas's judgment from those pieces of Evidence.
- Detected secrets are shown as `[secret: <kind>]` everywhere except the verbatim copies in `raw/`.
- `[I · pattern: 131 days of Sessions, 2026-06-02..2026-11-15]` is an Inference from a pattern across many Sessions.

## Evidence ids
`cc` Claude Code, `cx` Codex, `dv` Devin, `hm` Hermes, `gpt` ChatGPT, `cai` claude.ai, `nt` a Note.
The id is also the folder name: evidence/<harness>/<yyyy>/<mm>/<id>/session.md.
A Note's id heads it in notes/: `### 07:58 · nt-1a9s4d2f`.
...
```

## settings.md

```markdown
---
atlas: settings/1
timezone: Europe/Berlin
brief_at: "07:30"
review: {day: sunday, at: "18:00"}
active_time_gap_minutes: 15
machine: ubuntu-pc
channel: {platform: telegram, atlas_chat: null, address_word: atlas, exchange_window_minutes: 10}
# atlas_chat: e.g. "telegram:<chat>:<topic>" makes that chat or topic Atlas's own (briefs go there, every message there is Atlas's)
harnesses:
  claude_code: {enabled: true, mode: store, found_by: probe, roots: ["~/.claude"]}
  codex:       {enabled: true, mode: store, found_by: probe, roots: ["~/.codex"]}
  devin:       {enabled: true, mode: store, found_by: probe, roots: ["~/.local/share/devin/cli"]}
  hermes:      {enabled: true, mode: store, found_by: probe, roots: ["~/.hermes"]}
  chatgpt:     {enabled: true, mode: export, found_by: owner, roots: ["~/atlas/inbox"]}
  claude_ai:   {enabled: true, mode: export, found_by: owner, roots: ["~/atlas/inbox"]}
interfaces: {t3_code: {found_by: probe, root: "~/.t3"}}
exclusions: []
sealed: sha256:0a9d…
---
# Settings

Daily Brief at 07:30, Review on Sunday at 18:00 (Europe/Berlin). Active Time gap: 15 minutes.
Channel: Telegram, your Hermes home chat. Change these by telling Atlas or in the Board's Settings tab.
```

A time in a file body (a mark, a Notes heading, a History or Sessions line) is wall time in `timezone`, and Atlas reads it back in that zone. A datetime in front matter carries its offset, as in `pauses.md`. A Step's done time is the time of the Fact it rests on. A skip's time is the time of its basis, or 00:00 on the day shown when the skip is by silence.

A statement with its mark is one line, so a line break in its text (a folder name can hold one) is written as a space. A Fact's label, which names an Interface a Harness store wrote, writes as a `\uXXXX` escape each backslash, bracket, middle dot, control character, line break and lone surrogate, so the mark reads back. A quoted text, in front matter or a body line, writes as a `\uXXXX` escape each character the YAML reader rejects or reads as a line break: C1 controls, U+FFFE, U+FFFF, lone surrogates, NEL, U+2028 and U+2029. Every record file writes a surrogate pair as the character it stands for, so the YAML and the JSON reader read the same text, and a lone surrogate, which UTF-8 cannot hold, as U+FFFD wherever it is not quoted (a statement, a turn, a Note, a heading's folder).

## profile.md

```markdown
---
atlas: profile/1
areas: [work, health, family, home]
patterns:
  - {description: 131 days of Sessions, first: 2026-06-02, last: 2026-11-15, drawn_from: [cx-kxmhgr73ardh6q4s, cx-2sx2td2dfmibfk6u, cc-qgce44oeoxhikf3a]}
sealed: sha256:77e2…
---
# Profile

- You work in two blocks, 07:30 to 10:00 and 21:00 to 23:30. [I · pattern: 131 days of Sessions, 2026-06-02..2026-11-15] <!-- added 2026-11-15 by atlas -->
- You do Greenhouse logger Steps in the evening, not in the morning. [F your Note · 2026-11-20 13:02 · nt-6q2w8r3t] <!-- added 2026-11-20 by you -->
- You park hardware experiments when parts arrive late. [I · cc-wm5ggv3wlhmelyvr, cc-dve7x4dc3bejiqsa] <!-- added 2026-11-16 by atlas -->
```

A pattern mark, such as `[I · pattern: 131 days of Sessions, 2026-06-02..2026-11-15]`, names its Sessions in the front-matter `patterns:` entry with the same description, first and last day. When two Patterns would share a description and both days, as two descriptions that differed only by a masked secret do, the second takes the description with ` (2)` after it, a third ` (3)`, so each keeps its Sessions.

Each entry also records when it was added and by whom (Atlas or the owner), in an HTML comment at the end of its line, such as `<!-- added 2026-11-16 by atlas -->`, or `by you` for an entry the owner wrote. The weekly Review lists the entries Atlas added that week and still holds as written (G3).

## pauses.md

```markdown
---
atlas: pauses/1
pauses:
  - {start: 2026-11-20T16:40+01:00, end: 2026-11-22T07:00+01:00, said: "until sunday morning, we're at my parents'"}
sealed: sha256:c41f…
---
# Pauses

- Fri 20 Nov 16:40 to Sun 22 Nov 07:00. You said: "until sunday morning, we're at my parents'".
```

## forgotten.md

```markdown
---
atlas: forgotten/1
skip_keys:
  - sha256:3be0c9…        # never re-collect: hash of "<harness>:<native id>", not content
sealed: sha256:e90a…
---
# Forgotten

- Fri 20 Nov 2026 13:05: a Session was forgotten at your request.
```

A commit that puts a Session fails when `forgotten.md` then holds the skip key of that Session or of a Session folded into it, or, for a Session read alone and put inside another Session's evidence, that Session's skip key. So a Forget that commits while a collect reads makes the collect plan again and leave the Session out, and a Session held inside a forgotten Session is never put again as one of its own. A Forget of any other Session does not stop it. Each collect also removes any evidence whose skip key `forgotten.md` holds.

Removing a Session's evidence, by Forget or by a fold, deletes the whole folder, also a file Atlas did not write there, such as a `.DS_Store` or an editor backup. The folder and everything in it belong to that Session, so the file goes with it, and it is not kept as a Note, because the Note would keep what the Forget removes (R4 Q9). `session.md`, `card.md` and the raw parts `session.md` names go only while they hold what the removal read. Any other file, also one in `raw/`, goes as the commit finds it. A link goes as itself, also one standing for `raw/` or named like a raw part: Atlas never reads, follows or deletes what it points to. When a link stands where the Session's folder or its mirror is, the removal leaves the evidence and logs it, and a link that takes a folder's place after the commit planned its deletions keeps what is behind it, logged: the commit deletes nothing behind it, and neither opens nor removes a folder through it. A folder that then holds only an empty folder or something that is neither a file nor a link stays, and holds nothing of the Session.

A commit writes in a Session's folder, its `raw/` and its mirror only while no link or file stands where one of them is: it fails with Conflict when one stands there as it plans. When one takes a folder's place after the commit was staged, the commit writes every file it can still reach, deletes nothing and fails with Conflict, and it stays ready. The replay of a commit a crash left ready does the same, logged, and never raises. Each open and each commit tries such a commit again, and the first one after the link is gone writes the files it left and only then makes its deletions. So a commit is whole or still ready: none reports success with a file unwritten, and a fold never removes a Session whose parts have not reached the Session it folds into. Until then the record shows that commit's other writes, HEAD among them, and a commit that writes or deletes in a Session folder, or a file, that the waiting commit has left fails with Conflict, so nothing it will still write or delete changes first. Atlas opens and makes each of these folders down from the one above the Session's folder, never through a link, and replaces and syncs each file in the folder it opened. A file counts as written only when, once it is in that folder, the folder is still the one at its path. When a link, or anything else, has taken the folder's place by then, Atlas moves the file back to the commit's staging and the commit waits as above. So nothing a commit writes stays in a folder taken out of the record, which keeps no older copy of that file either. A commit makes its deletions only once each file it writes in a Session folder is at its path, also one written before the commit last waited. So a fold whose part was in a folder moved out of the record after it was written keeps the folded Session, and waits until that folder is back. The record root and every folder above a Session's folder, `evidence/`, a Harness's folder and `.atlas/last-written/` among them, may be links: the owner may move one and link it back. Limit: Atlas cannot tell such a move from a link swapped in to take a commit's writes, so a link that takes the place of one of these folders, or of a folder outside the evidence such as `pursuits/` or `journal/2026/`, while a commit is staged takes that commit's writes to those files. It needs someone who can already write in the owner's home.

Collect plans each batch of Sessions as one commit. When a link stands in the way of one Session's folder, or a commit a link holds up waits on it, that Session alone is left to the next collect, logged with its Harness and no path, and the rest of the batch is planned again without it and committed in the same tick. A fold waits with the Session it folds in: while a link stands in the way of that Session's folder, the Session it folds into is left too, so once the folder is back the fold holds each part once.

A hand edit does not stop a Forget. The owner asked for the removal, so a `session.md` or `card.md` edited by hand goes with the folder, whether the edit was saved before the Forget was planned or while it commits, and no Note keeps it, because the Note would keep what the Forget removes (R4 Q9). A fold is Atlas moving a Session, so it keeps a hand edit (W Q15). A Session whose `card.md` fails its seal is not folded: it stays its own Session, logged, until the card is put back and the edit kept as a Note, and the next collect that reads it again folds it. A fold's commit deletes a folded Session's card only while the card holds its seal and what the commit compared, also a card written after the fold was planned. Otherwise the commit changes nothing and fails with Conflict, so collect plans again and leaves that Session unfolded. A fold a crash left ready is dropped the same way, and the next collect makes it again. Once the commit has found every card as it compared it, the fold goes through: a replay after a crash finishes it, so no card goes while its Session stays. `session.md` is not sealed and is restored by regenerating it, so a fold does not look for an edit in it.

## pursuits/billing-export-staging-deploy.md

```markdown
---
atlas: pursuit/1
id: p-9m3c
name: "Billing export: staging deploy"
kind: project
area: work
status: parked
focus: false
created: 2026-11-15
continued: null
decision_held_until: null
raised_at: null
proposals:
  - {kind: ending, to: done, basis: "The staging password is rotated and the old one revoked, which is what this Pursuit was for. [I · cc-rf6i7kcuky24rnfi]", since: 2026-11-19, in_followup: true}
read_only_sessions: [cc-jjkt6v3xg3gkf6lu]
sealed: sha256:2f6a…
---
# Billing export: staging deploy

project · work · parked · last worked Wed 18 Nov 2026

## Resume Note

Where
- ubuntu-pc, ~/work/billing-export, branch deploy-staging [F Claude Code · 2026-11-18 10:12 · cc-rf6i7kcuky24rnfi]

Left off
- The staging database password is rotated; the old one is revoked. [F Claude Code · 2026-11-18 10:40 · cc-rf6i7kcuky24rnfi]

Decided
- Staging deploys stay manual until the export ships. [F your Note · 2026-11-15 20:41 · nt-3k8d1p0x]

Open questions
- Should the deploy script read the password from the secret store? [I · cc-rf6i7kcuky24rnfi]

Sessions to reopen
- Claude Code "rotate staging db password" · 2026-11-18 · cc-rf6i7kcuky24rnfi

## Next Step

- [open · held until 2026-12-07] s-20261118-1 · Move the staging password read into the deploy script's secret lookup. [I · cc-rf6i7kcuky24rnfi]

## Steps

- [done · seen 2026-11-18] s-20261115-1 · Rotate the staging database password. [I · cc-qgce44oeoxhikf3a]
  done: [F Claude Code · 2026-11-18 10:40 · cc-rf6i7kcuky24rnfi]

## Filing rules

- Staging deploy chores belong here, not in "Billing export service". [F your Note · 2026-11-15 20:41 · nt-3k8d1p0x]

## Sessions

- 2026-11-12 16:50 · Codex · "staging deploy env vars" · cx-2sx2td2dfmibfk6u
- 2026-11-18 10:42 · Claude Code · "rotate staging db password" · cc-rf6i7kcuky24rnfi
- 2026-11-23 09:14 · Claude Code · "hold the staging step" · cc-jjkt6v3xg3gkf6lu

## History

- h-0001 · 2026-11-15 20:41 · You · Split out of "Billing export service" with 4 Sessions. [F your Note · 2026-11-15 20:41 · nt-3k8d1p0x]
- h-0002 · 2026-11-18 11:02 · Atlas · Filed 2 Sessions here by your split. [I · cc-rf6i7kcuky24rnfi, cc-qgce44oeoxhikf3a]
- h-0003 · 2026-11-18 11:02 · Atlas · Marked the Step done from your Session. [F Claude Code · 2026-11-18 10:40 · cc-rf6i7kcuky24rnfi]
- h-0004 · 2026-11-20 13:05 · Atlas · Removed 1 statement when you forgot a Session. [F your Note · 2026-11-20 13:05 · nt-5c7v3b8n]
- h-0005 · 2026-11-20 16:31 · You · Parked it on the Board. [F your Note · 2026-11-20 16:31 · nt-2w6e9r1y]
- h-0006 · 2026-11-23 09:14 · Claude Code · Changed by Claude Code on your instruction: "tell atlas to hold the staging step until 7 december" [F Claude Code · 2026-11-23 09:13 · cc-jjkt6v3xg3gkf6lu]
```

The owner's word in a message to Atlas writes these History lines, each citing that message's Note: `Marked the Step done.`, `Skipped the Step.`, `Parked it.`, `Dropped it.` and `Said "continue".`. A Step ended by the owner's word has the owner's words about it, if any, as its basis line, for example `done: 10/10 pass with one worker [F your Note · 2026-11-16 08:34 · nt-cmdeifs6]`. The id of a Note from a message is derived from its `platform_ref`, so a redelivery cannot add a second Note.

A correction to filing writes these, citing its Note too. A merge keeps the id, Kind, Area, Resume Note and Next Step of the Pursuit worked on last, lists every Session of both, and appends the other's History lines after its own, numbered on, then `Merged "<a>" and "<b>" into one Pursuit.`. The other's open Step ends `skipped · replaced`, and one of its Steps whose id the kept Pursuit uses takes the next free id of its day. The other's file goes, and the Notes and briefs that name it name the kept Pursuit. Its Filing rule reads `Work filed as "<other>" belongs here: <the owner's reason>.`. A split writes `Split out of "<from>" with N Sessions.` on the new Pursuit and `Split "<new>" out of it with N Sessions.` on the other, with the Filing rules `<Criterion> belong here, not in "<from>".` and `<Criterion> belong in "<new>", not here.`. The new Pursuit starts active or parked as backfill would start it, outside Focus, in the Area of the one it came from. A move to an Area writes `Moved it to the Area "<area>".`.

Each Board click (Status, Focus) is also kept as a one-line Note in `notes/…-board.md` ("You moved it to Parked on the Board"). That way every History Fact names Evidence, as a Fact must. Seen marks are not Notes: they are machinery.

`proposals:` lists what Atlas has proposed and the owner has not yet answered. Each item has a `kind` (`ending`, `focus` or `reopen`) and the fields of that kind: an ending has `to`, `basis`, `since` and `in_followup`, a focus proposal has `add`, `reason` and `since`, and a reopen proposal has `basis` and `since`. A statement in a proposal is written as a body line is, text then mark.

A Sessions line's time is the Session's last turn, so "last worked" is the day of the last turn of the Pursuit's latest Session.

`read_only_sessions:` names the listed Sessions that only read or asked Atlas about the Pursuit (W Q18). They stay in the Sessions list but do not count as working on it, so "last worked" ignores them.

A History line written for an agent names the agent's Harness in the actor column, and its statement quotes the owner's words the agent acted on (ADR 0009).

A counted mark in a Pursuit file, such as `[F counted · sessions · 3 Sessions]`, has its Sessions in a front-matter `counted:` list, one `{rule, basis: [ids]}` per counted mark, in the order the marks appear in the body. A Journal day keeps them in its `evidence:` list instead.

How backfill creates a Pursuit file:

- **Once.** Backfill builds Pursuits only while the record holds no Pursuit file, in one commit with the one-off marks, so a crash leaves all of them or none. Sessions collected after that are filed one at a time (#10).
- **A file Atlas cannot read.** Such as one Atlas may not open, one that does not parse, or one cut short, whose body lacks its Resume Note, Sessions or History, the sections Atlas never writes empty. A body without another of its sections reads it as empty. It is logged (the error's type only, once per tick) and left out of every Pursuit Atlas reads, so the Daily Brief still goes out, and it is never written over. While one is there, collect folds no Session into another, because that file may list it.
- **File name.** `pursuits/<name>.md`: the Pursuit's name in lower case, each run of characters other than ASCII letters and digits (`a-z`, `0-9`) made one `-`, cut to 60 characters, with no `-` at either end. A name with no ASCII letter or digit is `pursuit`. When another Pursuit or a file Atlas cannot read has that name, `<name>-<id>.md`, then `<name>-<id>-2.md` and on.
- **Id.** `p-` and the first 4 base32 characters, in lower case, of the sha256 of its Sessions' ids, longer when another Pursuit has that id. Planning the same build again names it the same.
- **Status.** Active when the last turn of its latest Session, on the owner's clock, is at most 30 days before the backfill's day, parked otherwise, outside Focus. The report's "touched since 15 Oct" names the last day that starts parked, counted from the day backfill built the Pursuits (their `created`, or the one-offs' `one_off_day` in card.md when it built no Pursuit), so a later backfill prints the block as it was built: on 15 Nov a Pursuit last worked on 15 Oct is 31 days quiet.
- **Resume Note.** Where is a Fact read from its latest Session's metadata: machine, folder, branch. Every other line is drafted by the model. A line whose quote, of at least 15 characters besides its masked secrets, is found in one cited Session's masked transcript, whitespace aside, is a Fact at the time of the last turn that holds it; any other line is an Inference on the Sessions it names. Its Next Step is an Inference, `s-<backfill day>-1`.
- **History.** One line, `h-0001 · <backfill time> · Atlas · Created from N Sessions [I · <every Session>]`.
- **Forget.** Everything the model wrote after reading a Session goes when the Session goes. So a Session removed while backfill builds, before or as the Pursuits commit, makes backfill drop the whole grouping, names, Kinds and lines included, and group again without it. The model is asked again only for the folders whose Sessions changed and for the merge.

## journal/2026-11-16.md

```markdown
---
atlas: journal/1
day: 2026-11-16
active_time: {total: "3:55", by_area: {work: "3:15", health: "0:40"},
              by_pursuit: {p-2x7b: "1:10", p-4k7q: "1:55", one-off: "0:10", p-8h1n: "0:40"}}
evidence: [cx-acdo6557j7vy6xfl, cc-spjgwqujw7ejpbl7, cc-lbjjibk4bsud4ic4, cx-z6ynojpwzlraydaw, gpt-hmacgassx2oybydn]
sealed: sha256:aa03…
---
# Monday 16 November 2026

Active time 3 h 55 min [F counted · active_time · 5 Sessions]
  work    3 h 15 min  Payments CI flakes 1 h 10, Billing export service 1 h 55, one-off 10 min
  health    40 min    Knee rehab plan 40 min

Worked on
- Payments CI flakes: ran test_retry with one worker; 10 of 10 passed. [F Codex · 2026-11-16 08:05 · cx-acdo6557j7vy6xfl]
- Billing export service: per-invoice VAT rounding matches the ledger. [F Claude Code via T3 Code · 2026-11-16 21:40 · cc-spjgwqujw7ejpbl7]
- Knee rehab plan: listed physio exercises for weeks 1 and 2. [F ChatGPT · 2026-11-16 19:10 · gpt-hmacgassx2oybydn]

Decided
- Billing export service: round VAT per invoice, not per line. [F Claude Code via T3 Code · 2026-11-16 21:52 · cc-spjgwqujw7ejpbl7]

Finished
- Steps on Payments CI flakes and Billing export service. [F your Note · 2026-11-16 07:58 · nt-1a9s4d2f]

Evidence: 5 Sessions. Codex 12:30 belongs to both Payments CI flakes and Billing export service. [F counted · sessions · 5 Sessions]
Corrected Wed 18 Nov: added ChatGPT 19:10 (40 min) when your ChatGPT export arrived.
Covers AI Sessions on ubuntu-pc only.
```

The Active Time line is counted by `activetime.day` by a fixed rule, so it is a Fact (D Q9). Its five Sessions are the ones `evidence:` lists, and so are the Evidence line's.

How Atlas writes a Journal day:

- **Which days.** A day with Active Time from a Session filed in a Pursuit or as a one-off. Backfill writes every such day up to its own, that day included. Each tick writes the days that closed since the newest day the Journal has, that day included, so a day backfill wrote before it ended is written again once it has. A Session filed nowhere yet adds no time until it is filed (#10).
- **Active Time.** `activetime.day` over the turns in `session.md`: the owner's own messages in a Session's own conversation, every other turn as the agent's, and no turn of an exchange with Atlas. A Note is never Active Time. Each Area line lists its Pursuits in the order they were first worked that day, a one-off last; `by_pursuit` adds the one-offs of every Area together. In an Area line a Pursuit's name writes a comma, a backslash and the hyphen of a leading `one-off` as `\uXXXX` escapes, so the line reads back.
- **Lines.** For each Session a Pursuit lists that the day's file does not cite yet, the model reads that Session's turns of the day and drafts Worked on, Decided and Finished lines about its Pursuits, each prefixed with the Pursuit's name. A line whose quote is found in one of those turns is a Fact at that turn's time; any other is an Inference on that Session, so a line only ever holds words of the Session it cites. A one-off Session gets no line. A rebuild keeps the lines the file has whose Sessions are all still held, then adds the new ones after them, in the order of their Pursuits in the Area lines. A section with no line is left out.
- **Evidence line.** The day's Sessions counted, then each Session that more than one Pursuit lists, by its Harness and its first time that day.
- **Forget.** A day is committed only while every Session it counts or cites is still held as Atlas read it; a Session removed before the commit makes Atlas plan the day again without it.
- **No model.** A day whose lines the model cannot write is not written. Atlas logs the kind of failure and the day, and the next tick or backfill tries again from that day.

## notes/2026/11/2026-11-15-telegram.md (the owner's conversation with Atlas)

```markdown
---
atlas: notes/1
day: 2026-11-15
via: telegram
entries:
  nt-0p4m7c2a: {platform_ref: "telegram:5550001:8812"}
sealed: sha256:5d1e…
---
# Notes · Sunday 15 November 2026 · Telegram

### 20:39 · nt-0p4m7c2a
You: merge the two greenhouse ones. two folders, one thing
Atlas: Merged into "Greenhouse logger" (project). It keeps both histories, 23 Sessions.

### 20:41 · nt-3k8d1p0x
You: split the staging deploy chores out of Billing export service
Atlas: Split. "Billing export: staging deploy" (project) has 4 Sessions. ...
```

## notes/2026/11/2026-11-17-claude-code.md (Notes from an agent)

```markdown
---
atlas: notes/1
day: 2026-11-17
via: claude_code
entries:
  nt-7v1x0b9e: {derived_from: [cc-7hht4lnaqti7efqr]}
sealed: sha256:91bb…
---
# Notes · Tuesday 17 November 2026 · from Claude Code

### 21:20 · nt-7v1x0b9e · about: Greenhouse logger · cwd: ~/home/greenhouse
Claude Code: Decided to log every 5 minutes instead of every minute, to save the SD card.
```

An agent's Note names the Session it came from in `derived_from` when Atlas could tell which Session called (`sources.bind_caller`), so forgetting that Session removes the Note. The agent's text is masked and at most 400 characters, and it is always the agent's line, never `Atlas:`.

## notes/2026/11/2026-11-17-devin.md (an agent's change Atlas could not confirm)

```markdown
---
atlas: notes/1
day: 2026-11-17
via: devin
entries:
  nt-4q8w2m5r: {pending: {command: EditResumeNote, pursuit: p-6r2t, remove: [], add_decided: [Keep the 32 GB card.], add_left_off: [], add_open: [], next_step: null}}
sealed: sha256:7c21…
---
# Notes · Tuesday 17 November 2026 · from Devin

### 21:40 · nt-4q8w2m5r · about: Greenhouse logger · cwd: ~/home/greenhouse/firmware
Devin, on your words: tell atlas we decided to keep the 32 GB card for now
Atlas: Not applied: I could not confirm these words in that Session. Reply "apply" to make the change.
```

The line names the Harness and quotes the owner's words the agent passed; `pending` keeps the change, which "apply" makes under the owner's authority. Atlas's next answer in any Channel offers it once (`offered: true` then), the owner's Note of that answer names it in `offer`, and "apply" makes the change the latest `offer` names and adds `applied: true`. A Harness has at most three such Notes waiting; Atlas keeps no fourth.

## notes/2026/11/2026-11-20-hand-edits.md

````markdown
---
atlas: notes/1
day: 2026-11-20
via: hand-edits
entries:
  nt-9h5j2k6l: {offered: true, derived_from: [cc-qgce44oeoxhikf3a, cc-wm5ggv3wlhmelyvr, cc-dve7x4dc3bejiqsa, cx-kxmhgr73ardh6q4s, cx-2sx2td2dfmibfk6u]}
sealed: sha256:0c3f…
---
# Notes · Friday 20 November 2026 · hand edits

### 12:47 · nt-9h5j2k6l · profile.md
Someone edited profile.md outside Atlas. Atlas put it back. Reply "apply" to make this change through Atlas.

```diff
-- You skip Steps that need hardware. [I · cc-wm5ggv3wlhmelyvr]
+- I skip greenhouse steps in the morning because it is dark
```

The file as it was edited:

```markdown
---
atlas: profile/1
areas: [work, health, family, home]
patterns:
  - {description: 131 days of Sessions, first: 2026-06-02, last: 2026-11-15, drawn_from: [cx-kxmhgr73ardh6q4s, cx-2sx2td2dfmibfk6u, cc-qgce44oeoxhikf3a]}
sealed: sha256:3a90…
---
# Profile

- You work in two blocks, 07:30 to 10:00 and 21:00 to 23:30. [I · pattern: 131 days of Sessions, 2026-06-02..2026-11-15] <!-- added 2026-11-15 by atlas -->
- I skip greenhouse steps in the morning because it is dark
- You park hardware experiments when parts arrive late. [I · cc-wm5ggv3wlhmelyvr, cc-dve7x4dc3bejiqsa] <!-- added 2026-11-16 by atlas -->
```
````

The gateway sends each `Atlas:` reply, and Hermes v0.23.0 gives Atlas no message id for it, so the Notes file keeps none, and no reply is known to answer it.

Each open, tick and commit checks the seal of every record file outside `evidence/` that has a last-written copy (no stat cache yet: a Snapshot reads those files anyway) and puts back one whose seal fails or that was deleted, from that copy read and written again through the codec, so it comes back masked. An edited file becomes a hand-edit Note in the same commit; a deleted file is put back without one. A file with no last-written copy is one Atlas did not write: the check never reads, moves or deletes it. A path with a link in it, at the file or at a folder above it, is passed over, and nothing is put back while a link stands where the hand-edits Notes file goes. Editor droppings (`.*`, `*~`, `*.swp`, `*.tmp`, `#*#`) are never hand edits. Atlas's next answer offers "apply" once for each hand-edit Note. Not yet: `card.md`, `session.md` and raw parts, which a fold still leaves as they are.

`entries:` holds, by Note id, the fields Atlas keeps for a Note that differ from their defaults: `platform_ref` (`<platform>:<chat>[:<thread>]:<message id>`, a forum's General topic written as the chat itself, the chat included because a Telegram message id is unique only within its chat, so a redelivered message stays one Note), `offered: true` (Atlas has offered "apply"), `offer` (on the owner's Note: the Note Atlas's answer offered to "apply", which a later "apply" makes), `applied: true` ("apply" made the change), `derived_from` (the Evidence the edited file or Atlas's last-written copy of it cites, so Forget of one of those Sessions removes the whole Note, the edit typed there with it) and `pending` (the change "apply" would make, as `{command: <name>, <its fields>}`).

## evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/session.md

```markdown
---
atlas: session/1
id: cx-kxmhgr73ardh6q4s
harness: codex
namespace: local
interface: null
native_key: 0199f2a1-7c3e-7d10-9b2e-3f5a1c8e2d40
title: gpx elevation smoothing
machine: ubuntu-pc
cwd: ~/code/bike-routes
git_branch: main
started: 2026-10-25T19:52:10+01:00
last_at: 2026-10-25T20:31:44+01:00
parts:
  - {name: main.jsonl.zst, sha256: "6c1d…", source: "~/.codex/sessions/2026/10/25/rollout-….jsonl.zst", fingerprint: "41233@1792956704512339871", origin: "34bba96ddf47e307:main"}
regenerated_from_raw: true
---
# Codex · Sun 25 Oct 2026 19:52 · ~/code/bike-routes

**You · 19:52**
The elevation profile has spikes on the downhill. Can we smooth it?

**Codex · 19:55**
Tried a 30-point rolling median over the elevation series, then dropped points that jump more than 8 m from the median. …

**You · 20:14**
That works on the test file.
```

Not sealed: `session.md` is regenerated from `raw/`, so it is restored by regenerating it.

How collection fills it:

- **Identity.** `id` is the Harness's prefix, `-`, and the first 16 base32 characters, in lower case, of the sha256 of `<harness>:<namespace>:<native key>`: 80 bits, so two Sessions never share a folder or a skip sweep. `namespace` is `local` for every store on this machine. v1 reads one machine and never opens a credential file, so the account behind a store is not read. A second copy of a store (Orca's Codex home) shares the namespace, so its copy of a thread is the same Session, and Atlas reads the copy that holds more once decompressed, the first root's on a tie. A Devin `native_key` is its slug, `@`, and the creation time its store keeps (epoch seconds), so a slug used again is another Session.
- **Parts.** Each part has a key, and its file in `raw/` is the key with every run of other characters than letters, digits, `.`, `_` and `-` made one `-`, plus `.jsonl.zst`. `main` is the Session's own transcript. `subagent:<name>` is a subagent conversation: a Claude Code `agent-*.jsonl`, a Codex `thread_spawn` thread, or a Hermes session whose `source` is `subagent` or `tool`. `t3-child:<native id>` is a Session that T3 Code's agent started in a `subagent` thread; a T3 Code fork is the owner's own conversation and stays its own Session. A folded Session's own parts follow its key after `/`. `continued:<id>` is a Hermes Session continued after a compression split; its owner turns stay the owner's. A Hermes session that `/new`, an automatic reset or `/branch` started from another is the owner's own Session.
- **Origins.** A part's `origin` names the native Session it was copied from and its key there: the first 16 hex digits of the sha256 of `<harness>:<native key>`, then `:` and the key. The record matches each part a store lists by its origin, so a part keeps its identity whichever root or file it is read from, wherever a fold puts it, and whatever `source` shows once masked. A Session folded into another stays folded after T3 Code prunes the lineage and after its Harness cleans up the Session that started it: a Session whose own transcript the record holds inside another Session's evidence is put there, under the key it was folded in with. When T3 Code names its thread a fork, or the subagent of another thread, its parts and turns leave that evidence: a fork becomes its own Session, with its owner's turns, and a child of another Session folds into that one.
- **Folds.** When a Session the record holds as its own folds into another, every part it holds moves there: its own parts under its new key, and the Sessions folded into it under their own keys. Its turns become the agent's, and its folder goes. A part is never left behind, so a late fold keeps what a Harness has since cleaned up. A Session a Pursuit lists stays its own, and so does every Session while a Pursuit file Atlas cannot read is there.
- **Turn order.** Turns are in the order they were written. In each part, the turns a collect reads are paired with the turns `session.md` holds that show as them (the same part, role, minute and masked text), as many pairs as keep both orders (a longest common subsequence), and each turn read takes its pair's place. So whichever parts a collect reads, the turns `session.md` showed keep their order, but for those their part no longer has. Each part's turns stay in the order they are read in. A turn read that pairs with no held turn, such as a new turn, a line its Harness rewrote, or a new twin of a held turn, goes in by its time, after the turns held at or before its minute, but never before the turn its part read before it nor after the next one its part read that is paired. The turns of a Session that folds in go in by time the same way.
- **Raw copies.** A file store's part is the file's lines, decompressed for a Codex `.jsonl.zst`. A SQLite store's part is its rows, one JSON object per row, `{"<table>": {<column>: <value>}}`: the session row, then its message rows (for Hermes, without the rows `hermes send` mirrored, nor a row Hermes v0.23.0's alternation repair merged one into: an assistant row that lists `absorbed_message_uids` and has a sent text as one of its newline-bounded pieces). A row matches a sent text as it reads. It matches with its secrets masked too only when an attempt may have sent the text bare, as one made before Atlas masked did, whose file now holds it masked: a brief with an attempt that keeps no `text_sha256` of the text its file holds, and a Note's reply. So an agent's row that repeats a brief except for its secret is a turn. Each line has the identity its Harness gives it, and a raw copy holds each identity once: a part read again adds, verbatim, as a new zstd frame, the lines whose identity the copy lacks, so a raw copy only grows, and only Forget removes from it. `sha256` is of the whole copy, decompressed. Hermes's rows are the ones Hermes shows as the transcript: `active = 1`, and the rows an in-place compaction archived (`active = 0, compacted = 1`). A compaction's summary row (`_compressed_summary`) is no turn, a user handoff row without that flag is a turn of the ask it carries, and rows taken back (`active = 0, compacted = 0`) are not read. Atlas reads a store only through the folder and file it checked. It opens a store's real folder and keeps it only when its device and inode are the ones it listed, then opens a store file from it one name at a time and never through a symlink. A SQLite database is pinned the same way but never opened to read, because closing a descriptor that reads a file drops every POSIX lock the process holds on it, Hermes's own when Atlas runs inside Hermes: Linux pins it with an `O_PATH` descriptor, and elsewhere Atlas takes only its stat. SQLite is given the pin's name, `/proc/self/fd/<n>`, where the system has it, else the real path. SQLite resolves that name to the name the file has at that moment and opens the file by that name. The read counts only when the file SQLite names is the pinned file, under the store's root and by no link. SQLite opens the WAL and its index (`-wal`, `-shm`) itself, by name beside the database, and writes to the index even when it only reads. Python's `sqlite3` cannot give it those files, so Atlas checks them instead. In the folder Atlas checked, each must be absent or a regular file with no other link, both before SQLite opens them and after every read. A link count of 0 still counts: it is a file its Harness is removing as it closes. When SQLite read a WAL (`PRAGMA journal_mode` is `wal`), both must be there after the read, and both must be files this process holds open or maps. Any other read is refused, and that store is skipped for this collect and logged by the name of its refusal, never by a path: `LinkedElsewhere` (a store file is a link, has another link, or is not a regular file) or `SwappedWhileRead` (a folder or file is not the one checked, or SQLite read another file in its place). So a link there to another database's files, or a folder swapped while SQLite opens them, is never read. Neither is a database read under a second hard link while this process has the first name open: SQLite gives every name of one file the index of the name opened first, while each name has its own WAL, and SQLite itself fails that read while the WAL holds frames. A WAL or index hard-linked elsewhere too, as by a hard-link backup, is refused while that link lasts, because a second link cannot be told from another database's file linked in. Atlas looks for the two files first among the few descriptors just above the database's, where SQLite opens a connection's own, then among the files the process maps, where SQLite keeps its index, and lists every descriptor (`/dev/fd`, which Linux and macOS have) only when those miss, so a read costs the same with ten thousand descriptors open. Where the system lists no descriptors, only the names are checked. Limits: a link swapped in after the first check can still take a write to its index before the read is refused. A folder swapped for a link inside SQLite's own open of the database, and put back before that open returns, is read, because SQLite opens by name and every name Atlas checks after the open is the store's own again. So is a folder swapped inside SQLite's open of the WAL while another connection of this process has the store's own WAL open, as Hermes has when Atlas runs inside it: Python cannot name the descriptor one connection holds, so Atlas sees only that the process holds the store's WAL. Both swaps need someone who can already write in the owner's home. Closing them needs SQLite to open those files from the checked folder's descriptor, and Python's `sqlite3` cannot: it registers no VFS, and the Python that Hermes v0.23.0 ships links SQLite in without exporting it, so neither a VFS nor `sqlite3_file_control` is reachable, and a second SQLite loaded into Hermes's process would release the first one's locks. Nor can Atlas tell from the descriptors SQLite took which files it opened, because another thread opening another root's store at that moment looks the same as a swap, and refusing on it would skip real stores. Reading a private copy instead would write a second copy of every conversation in the store to disk on each read (about 400 MiB for a 392 MiB store, 135 ms), and it would be torn, because a copy holds no read lock in the index.
- **Line identities.** Claude Code: a line's `uuid`, or the whole line when it has none. Codex: the whole line, which holds its time; Codex only appends, and compressing a rollout in place keeps its lines. Devin: a message's `message_id` with what its turn is read from (role, content, and whether it is the owner's: `is_user_input` and a subagent or handoff extension), so a compaction's copy of it in another chain, or a copy that gains a `chisel/...` extension, is the same line; a session row's id, creation time, folder and title, so its usage columns (`cogs_json`, `last_activity_at`) add nothing. Hermes: the key Hermes v0.23.0 shows a message once by (role, content, time, tool call and tool name, or the tool calls' ids for an assistant row that has them, and for a user handoff row, where a compaction folded its summary into the owner's ask, the ask's content as `split_user_originated_turn` reads it from the row's content and `display_kind`), which `/retry`, `/undo`, ACP's persist and compaction keep while they delete rows and insert them under new ids, with the `absorbed_message_uids` of a row that merged others; a session row's id, source, parent, start, folder, branch and title. A line whose identity the copy lacks is a new line, so a message a Harness rewrote in place is copied again as it now reads. A Devin or Hermes Session's title, folder and branch are its session row's as the store holds it now, so a title changed back reads as it does now, while the raw copy keeps each title once.
- **Lines that do not parse.** A file store's line ends at a newline (LF), and the raw copy keeps every byte before it, a CR included. A Claude Code or Codex line that does not parse as a JSON object (a Harness that crashed mid-write, a disk error) is still a line of the part. Its identity is its bytes, so the raw copy holds it once, verbatim like every other line. A Harness that crashed mid-line and then resumed appends its next line to the cut one, so when the shortest tail of the line that starts with `{` parses as one whole JSON object, that object is the line's record and its turn shows; the bytes before it are cut. Any other such line adds no turn. The read that copies a line that does not parse, or one with a cut, logs how many it copied, never their content, so a later read of the same copy logs nothing for it. When the Harness later writes a cut line whole, the whole line has an identity the copy lacks, so it is added and its turn shows. A last line with no newline that does not parse is still being written: it is not copied, the rest of the part is, and the next read after the file changes copies it once it ends. The rest of the part is collected, except in a Codex rollout whose first line does not parse: that line's `session_meta` says what the Session is, so the rollout is logged and skipped like a Session that could not be read.
- **Fingerprints.** A part's `fingerprint` changes whenever a line Atlas reads from it does. A file's is `<size>@<mtime in ns>`, taken from the descriptor Atlas read it through. A SQLite part's is `<rows>@<the first 16 hex digits of the sha256 of its lines' identities>`, because Hermes rewrites rows in place, which neither a row count nor the newest row id shows, and a column Atlas does not read changes no identity. A tick reads a Session again when its store lists a part whose `origin` the record does not hold at that fingerprint, or when T3 Code names a Session the record holds as its own as a subagent, or as run through it while its evidence notes no Interface, or names a Session the record holds folded as a fork or as the subagent of a Session it holds elsewhere. So a Session that one tick could not read, or read before its store wrote it again, or did not commit, is read at the next tick. Backfill reads every Session.
- **Masking.** Every turn and every field is masked, as in every record file, but the ids, the times, the namespace and native key, a folded part's key, and the raw parts' names, hashes, fingerprints and origins. A title taken from the owner's first turn, when the Harness keeps none, is that turn's first line masked and then cut to 80 characters, before any mask the cut would split. `raw/` keeps the bytes as they were.
- **Turns.** `session.md` shows every turn its raw copies hold, once each by its line's identity, parsed from the whole copy each time a part is read. So a turn stays readable after its Harness takes it back (Hermes `/undo` and `/retry`), drops it (a transcript cut short) or rewrites it in place, beside what replaced it, and a copy read from another root or compressed in place shows no turn twice. A Hermes row is no turn of its own when rows its part held before it already say its words: Hermes v0.23.0's alternation repair, which runs when `/retry` loads a transcript, writes a merged row under the `message_uid` of the first row it merged, lists the others in `absorbed_message_uids`, and joins their words, each stripped as Hermes loads it, with a blank line for user rows and a newline for assistant rows, an empty one left out. Such a row repeats held rows when, for the uids it names in order, rows of its role held under them, each standing for the uids it names itself, joined that way are its words; any of the words a uid was held with will do, so a repair of an edited row repeats the edit. A row that names only its own uid, such as one the repair wrote back as Hermes loaded it or one whose unanswered tool calls it pruned, repeats a held row of its uid whose words, stripped as Hermes loads them, are its words, so an edit of the whitespace inside the words, such as indentation, is still a turn. The raw copy still holds it, and it is a turn when Atlas never held rows that say its words. A turn's heading is `**<You or the Harness> · <hh:mm>**`, with the day in front of the time when it differs from the turn before. A folded part's turns add ` · <part key>` and are the agent's: the "owner" there was the parent agent. ` · Atlas tools` marks a Hermes exchange that called `atlas_*` tools, and ` · time interpolated` a web-chat time set by interpolation. A text line that starts with `**` or `\` is written with a `\` in front. A turn's time keeps its seconds when they are not zero (`**You · 08:05:12**`), and its offset when the wall time is ambiguous, in the repeated hour of a DST fallback (`**You · 02:05+01:00**`), so Active Time reads every turn back as the instant it was.

## evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/card.md

```markdown
---
atlas: card/1
evidence: cx-kxmhgr73ardh6q4s
one_off_area: null
one_off_day: null
sealed: sha256:44af…
---
- Intent: smooth spikes in a GPX elevation profile. [I · cx-kxmhgr73ardh6q4s]
- Outcome: a 30-point rolling median plus an 8 m spike filter works on one file. [I · cx-kxmhgr73ardh6q4s]
- Unfinished: not tried on a ride through a tunnel. [I · cx-kxmhgr73ardh6q4s]
- Where: ~/code/bike-routes, branch main. [F Codex · 2026-10-25 19:52 · cx-kxmhgr73ardh6q4s]
```

The model writes Intent, Outcome and Unfinished from the Session's own conversation, its first turn, its last and as many turns before the last as fit in 11,000 characters. Where is read from the Session's metadata. `one_off_area` and `one_off_day` are set when backfill's grouping finds the Session is a one-off: Evidence with an Area and no Pursuit (R2 Q5), filed on that day. A card is written only while its `session.md` is as backfill read it, and goes with the Session's folder: a removal deletes every file in the folder, also a card another commit put there after the removal was planned. A card edited by hand keeps its Session from being folded away. A card Atlas cannot read is written again by the next backfill.

## .atlas/imports.json

The web-chat exports the tick read: `[{"harness": "chatgpt" | "claude_ai" | null, "at", "sessions", "deleted", "failure", "file", "broken", "skipped"}]`, every entry of a file still kept in the inbox (the tick drops it once the file leaves or changes) and the newest 50 of the files deleted. `sessions` counts the file's conversations the record holds, `broken` those it could not read or hold, `skipped` those with no text or held twice under one id. `failure` is the type of what went wrong (`NotAnExport`, `TooLarge`, `UnicodeEncodeError`, `Unreadable` for a file that failed in part, `NotAdded` for an export of a web chat the owner has not added, read again once they add it), `file` the kept file's device, inode, size and modified time, so a kept file is read again only once it changes. It holds no file name and no text of a conversation, so Forget leaves it; the sweep ignores `.atlas/`.

## .atlas/cache/<key>.json

One file per model output: `{"evidence": [<ids the input was read from>], "value": "<the output as JSON>"}`. The key is the sha256 of the auxiliary task, the prompt version, the instructions, the JSON schema and the masked input. The input, and every Area the schema names, are masked before the model reads them. Before Atlas reads or keeps the output, it is cut to the properties the schema names, and every text, the keys of a map the schema leaves open included, is put on one line with single spaces, without lone surrogates and noncharacters, and then its secrets are masked. The merge of the folders' drafts is tagged with every Session the group calls read, one-offs included. Only an output Atlas could use is kept: a group answer must put every Session it was shown in a Pursuit or among the one-offs. It is written under the write lock, after any commit a crash left ready is finished, and only while the record holds every Session it names. A commit that removes a Session's evidence, by Forget or by a fold, deletes every output tagged with it. Opening the record and every commit delete the `.<key>.tmp` files of cache writes a crash cut short. When backfill cannot write the Pursuits, it deletes every output read from their Sessions, so the next backfill asks the model again. The folder can be deleted at any time; backfill then asks the model again.

## briefs/2026/11/2026-11-20.md

```markdown
---
atlas: brief/1
day: 2026-11-20
status: sent
sent_at: 2026-11-20T07:30:41+01:00
message_ids: ["8930"]
text_sha256: "42da3d493c0b5be75d85f44f9f05e4b943dc04461b631aa118fbe2507826dfdc"
chat: "telegram:5550001"
follows_up: 2026-11-19
followups:
  - {pursuit: p-6r2t, step: s-20261118-2, state: no_sign}
  - {pursuit: p-5g9d, step: s-20261118-1, state: no_sign}
  - {pursuit: p-8h1n, step: s-20261119-1, state: not_visible_yet}
steps:
  - {n: 1, pursuit: p-4k7q, step: s-20261117-1}
  - {n: 2, pursuit: p-8h1n, step: s-20261119-1}
decision: null
sealed: sha256:7b2c…
---
Atlas · Daily Brief · Friday 20 November

YESTERDAY'S STEPS
    · Reading tracker for the kids, Greenhouse logger: no sign of them in your Sessions. [F]
      Both stay open; I'll offer each once more. [I]
    · Knee rehab plan: no sign yet. You work on it in ChatGPT, whose Sessions reach me late. [F]

STEPS
1 · Billing export service  Switch the PDF export to per-invoice rounding and rerun October. [I]
2 · Knee rehab plan         Ask for a week-3 progression based on how week 2 felt. [I]

NEEDS A DECISION
    Nothing today.

Reply "1 prompt" for a Resume prompt.
```

The body is the exact text sent, so the Hermes adapter can recognise the mirrored row. Step offers are counted from these files.

The text is masked like every record text, and each attempt sends the text as the file holds it, so the file is exactly what was sent and a Hermes mirror row of it matches the sent ledger. The file is written as `status: sending` before each attempt and rewritten with that attempt's outcome after it, `sent` or `arrival_unknown`. `status`, `sent_at`, `message_ids`, `text_sha256` and `chat` describe the last attempt, and `sent_at` is when that attempt began. `text_sha256` is the sha256 of the text the attempt sent, stripped, written once the attempt has an outcome. The file holds it only when it is the hash of the text the file holds, so it shows nothing the text does not. A Hermes mirror row of a brief whose every attempt has it must read as the text. `chat` is where `hermes send` delivered it, `<platform>:<chat>[:<thread>]`, with a forum's General topic written as the chat itself. A Telegram message id is unique only within its chat, so a reply names this brief only in that chat. A file written before Atlas kept `chat` has none, and its ids name it in no chat: the home channel may have moved since it was sent. A message is sent at most twice (D Q13). After a second attempt the file also holds the first, as `first_attempt: {at: 2026-11-20T07:30:00+01:00, status: arrival_unknown, message_ids: [], text_sha256: "42da…"}`, so the ids of both copies stay on record. A `followups:` row holds the pursuit, step and state, plus `ending_proposed: true` when the follow-up proposed an ending. The follow-up's line itself is kept only in the text. A brief whose Steps came from recent work because no Focus was ever picked (D Q10) adds `no_focus_yet: true`.

## reviews/2026/2026-W47.md

```markdown
---
atlas: review/1
week: 2026-W47
covers: {from: 2026-11-16, to: 2026-11-22}
status: sent
sent_at: 2026-11-22T18:00:00+01:00
message_ids: ["8977"]
text_sha256: "58b4d4d0448f16bab6e4b63c0489d353ee776a17b89350d21458b7451dd346c6"
chat: "telegram:5550001"
raised_parked: p-1c5v
asked: [p-3n0w, p-1c5v]
time_proposed: null
drawn_from: {cc-hgont7e3jxgshlgt: [8], cc-j5jztro3lwg5kr3i: [27, 28, 29], cc-uiogx7k2kh4jrsg5: [20], cx-43k6bm6jko7pqwo6: [20], cx-nxvls2s3slklrcbo: [19], dv-sd6crxiebn4hgssc: [24], gpt-kneewkszotwqmapr: [18]}
sealed: sha256:7eb4…
---
Atlas · Review · week of 16 to 22 November

FINISHED
- Payments CI flakes: you marked it done on Thursday. [F]
- 8 Steps done: 3 you told me about, 5 I saw in your Sessions. [F]

DECIDED
- Billing export service: round VAT per invoice. [F Claude Code via T3 Code, Mon 21:52]
- Greenhouse logger: log every 5 minutes. [F Note from Claude Code, Tue 21:20]
- Reading tracker for the kids: tablet app. [F your reply, Tue 20:45]

CHANGED
- Parked Agent-tooling newsletter and Billing export: staging deploy. [F]
- Dropped Bike route planner and continued Home server backups. [F]
- Knee rehab plan added to Focus on Wednesday. [F]

WHAT I DID ON MY OWN
- Created Knee rehab plan (health) from your ChatGPT Sessions. [F]
- Changed Reading tracker for the kids from idea to project: it has its own repository now. [I]
- Filed 2 Sessions under Billing export: staging deploy, by Sunday's split. [F]
  Reply to correct any of these.

FOCUS
- Payments CI flakes left Focus when you closed it, so there is a free slot. Proposed: Home server backups. You said "continue" on Thursday, and its next Step takes ten minutes. [I] Reply "add backups" or "leave it".

PARKED
- Pi-hole DNS loop · problem · quiet 100 days [F]
  Left off: found the loop between Pi-hole and the router; not fixed. [F Claude Code, 14 Aug]
  Continue, keep parked, or drop?
```

A Review file is written and settled as a brief file is: `status: sending` before each attempt, at most two attempts (D Q13), and `text_sha256` only when it is the hash of the text the file holds. There is one file per ISO week, so creating it is the guard against a second Review that week. `covers` is the span it reports on: from the day after the last Review sent, or the week's Monday when none was, to the Review's day, so the first Review after a Pause covers the weeks it missed. `raised_parked` is the parked Pursuit the PARKED section raised: its 90 days count again from this Review, and so silence restarts them. `asked` lists every Pursuit a question of the Review is about (a Focus, ending or reopen proposal, a park by silence, the parked raise): a bare reply word answers only those. `time_proposed` is the Daily Brief time it proposed (G4), after which no time is proposed for four weeks. `drawn_from` maps each Session the text draws on to the numbers of the text's lines that do (from 1, the heading being line 1); Forget of that Session removes those lines and the entry, and the text's hash goes with the change.
