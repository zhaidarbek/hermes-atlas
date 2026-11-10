---
name: atlas
description: How agents read Atlas, leave it Notes, and change it when told
---

# Atlas

Atlas keeps the owner's Pursuits: what they started, where they left off, and the next Step.

- Read Atlas with `atlas_read`: `{"pursuit": "<name>"}` gives that Pursuit's Resume Note, and `{"day": "YYYY-MM-DD"}` what the Journal holds for that day. A `[F]` line is a Fact, `[I]` an Inference.
- Tell Atlas something on purpose with `atlas_note`: `{"text": "...", "pursuit": "<name>"}`. You may add Notes unasked, such as a decision you and the owner reached.
- Change a Pursuit's Resume Note with `atlas_update` only when the owner told you to in this Session, in a message that names Atlas ("tell atlas ..."). Pass that whole message exactly as they typed it in `owner_words`, and ask for the change as `{"remove": ["<your words for the Open question they settled>"], "add_decided": true}`. Atlas adds the owner's message itself as the one decision, and removes the Open question only when the message says a question is settled and the Pursuit has just one. One message makes one change. If Atlas cannot find the message in this Session, it keeps the change as a Note and asks the owner; tell them so.
- Atlas never contacts other people.
