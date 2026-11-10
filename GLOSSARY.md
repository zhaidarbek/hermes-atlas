# Atlas

Atlas is a single-user manager that always knows the state of everything its owner has started, and pushes them to continue it. It is a manager first and a searchable memory second.

## Language

**Owner**:
The one person an Atlas install serves. Anyone can install Atlas, and each install has exactly one Owner.
_Avoid_: User, customer, account

**Pursuit**:
Anything the owner has started and might return to: a project, idea, experiment, problem or personal matter. A Pursuit keeps one history as it changes kind, so an idea that becomes a project is still the same Pursuit.
_Avoid_: Project, thread, task, topic

**Kind**:
What sort of thing a Pursuit currently is. A Pursuit's Kind changes as it matures.
_Avoid_: Type, category

**Area**:
The part of the owner's life a Pursuit belongs to, such as work, health or family.
_Avoid_: Workspace, domain, category

**Status**:
Where a Pursuit stands: active, parked, dropped or done. Only the owner can end a Pursuit as done or dropped, directly or through an agent they tell to; Atlas proposes.
_Avoid_: State, stage, phase

**Parked**:
A Pursuit the owner has set aside on purpose, which Atlas raises again only rarely.
_Avoid_: Paused, on hold, backlog

**Dropped**:
A Pursuit the owner has given up, which Atlas never pushes again but keeps findable.
_Avoid_: Archived, deleted, cancelled

**Neglected**:
An active Pursuit that has gone quiet. It is a condition that calls for a decision from the owner, not a Status.
_Avoid_: Stale, abandoned, inactive

**Focus**:
The small set of active Pursuits the owner has chosen to be pushed on.
_Avoid_: Priority, top list, current work

**Step**:
One concrete action on a Pursuit, small enough for one sitting, that Atlas suggests and that ends as done or skipped.
_Avoid_: Task, todo, action item

**Resume Note**:
What a Pursuit carries so the owner can pick it up anywhere: where they left off, what was decided, open questions, the next Step and the Sessions to reopen.
_Avoid_: Handoff, summary, context

**Journal**:
Atlas's account of the owner's days, one entry per day, covering what they worked on, decided and finished and how long they actively worked, with Evidence cited.
_Avoid_: Daily log, timeline, diary

**Active Time**:
Time the owner was engaged with at least one Session, counted once however many Sessions ran in parallel. An agent working alone while the owner is away is not Active Time.
_Avoid_: Duration, tracked time, session length

**Profile**:
What Atlas has learned about the owner that belongs to no single Pursuit, all of it readable and correctable by the owner.
_Avoid_: User model, memory, persona

**Board**:
The view of all Pursuits arranged by Status, with Focus marked.
_Avoid_: Dashboard, kanban

**Source**:
Any place Atlas learns from, such as a Harness, a mailbox, a messenger or the owner's saved posts.
_Avoid_: Channel, connector, integration, feed

**Harness**:
An AI tool the owner converses with, such as Claude Code, Codex, Devin, Hermes, ChatGPT or claude.ai. A Harness is one kind of Source.
_Avoid_: Agent, client, tool

**Interface**:
An app through which the owner runs a Harness, such as T3 Code. A Session run through an Interface belongs to the underlying Harness and is one Session.
_Avoid_: Harness, wrapper, front end

**Evidence**:
One item Atlas has taken in from a Source, such as a Session, an email, a message or a saved post. Every answer and every Pursuit points back to Evidence.
_Avoid_: Record, document, memory, artifact

**Session**:
One conversation between the owner and a Harness. A Session is the Evidence a Harness produces, and any conversation between an agent and its subagents is part of the Session that started it.
_Avoid_: Thread, chat, conversation

**Fact**:
Something Atlas read directly from Evidence, or counted from it by a fixed rule, such as Active Time or quiet days.
_Avoid_: Confirmed, truth

**Inference**:
Something Atlas concluded from Evidence by judgment, such as a model's reading. Atlas always shows the owner whether a statement is a Fact or an Inference.
_Avoid_: Guess, summary, insight

**Note**:
Something the owner, or an agent in a Harness, tells Atlas on purpose. A Note is Evidence like any other.
_Avoid_: Memo, memory, comment

**Pause**:
A period the owner declares themselves away, during which Atlas sends nothing and no Pursuit becomes Neglected. It applies to Atlas as a whole, unlike Parked, which applies to one Pursuit.
_Avoid_: Snooze, mute

**Forget**:
To remove Evidence and everything derived from it permanently, at the owner's request. Unlike a Dropped Pursuit, forgotten material is no longer findable.
_Avoid_: Delete, archive, drop

**Channel**:
A place where Atlas talks to the owner. The same service can be both a Source and a Channel, with separate rules for each role.
_Avoid_: Source, platform

**Daily Brief**:
The one message Atlas sends the owner each day to push them forward on their Pursuits.
_Avoid_: Digest, report, reminder

**Review**:
The one message Atlas sends the owner each week, covering what changed that week and what Atlas did or proposes on its own. It is the only unprompted message besides the Daily Brief.
_Avoid_: Weekly digest, newsletter, summary
