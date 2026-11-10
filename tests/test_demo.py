from harness.demo import BUDGET, SCRIPT, SKIPPED_SECONDS, Row, Shot, cropped, frame_counts, plan, read_script, slots, \
    without_redeliveries

TABLE = """\
| Time | Scenes | On screen | Voiceover |
|---|---|---|---|
| 0:00 | none | Title: "Atlas, a Hermes plugin" | Opening line. |
| 0:07 | 1 | `hermes atlas setup` output, from "Found" down | Setup line. |
| 0:15 | 3a, 3b | backfill output | Backfill line. |
| 1:20 | 38, 39 | the card back in Active | Closing line. |
"""


def test_the_script_rows_are_read_in_order_with_their_scenes_and_starts():
    rows = read_script(TABLE)
    assert rows == [Row(0, (), 'Title: "Atlas, a Hermes plugin"', "Opening line."),
                    Row(7, ("1",), 'hermes atlas setup output, from "Found" down', "Setup line."),
                    Row(15, ("3a", "3b"), "backfill output", "Backfill line."),
                    Row(80, ("38", "39"), "the card back in Active", "Closing line.")]


def test_each_row_runs_to_the_next_and_the_last_to_the_budget():
    assert slots(read_script(TABLE)) == [7, 8, 65, BUDGET - 80]


def test_a_row_past_the_budget_is_refused():
    try:
        slots([Row(0, (), "", ""), Row(90, ("1",), "", "")])
    except ValueError:
        return
    raise AssertionError("a row starting after the budget was accepted")


def test_the_demo_script_fits_the_budget_with_every_scene_and_with_none():
    rows = read_script(SCRIPT.read_text())
    every = frozenset(s for row in rows for s in row.scenes)
    assert rows and sum(s.seconds for s in plan(rows, every)) == BUDGET
    assert sum(s.seconds for s in plan(rows, frozenset())) <= BUDGET
    assert sum(frame_counts(plan(rows, every))) == BUDGET * 10


def test_scenes_that_do_not_pass_are_cut_with_a_note_and_share_nothing():
    shots = plan(read_script(TABLE), frozenset({"3a", "1"}))
    assert [(s.scene, s.seconds, s.note) for s in shots] == [
        (None, 7, None),
        ("1", 8, None),
        ("3a", 65, "not passing yet, cut: scene 3b"),
        (None, SKIPPED_SECONDS, "not passing yet, cut: scene 38, 39"),
    ]


def test_frames_follow_the_running_total_so_the_video_never_drifts():
    shots = [Shot(1 / 3, "")] * 3 + [Shot(0.55, "")]
    assert frame_counts(shots, fps=10) == [3, 4, 3, 6]


def test_the_script_crops_output_to_start_at_its_named_line():
    lines = ["Atlas 0.1 on a machine", "Record: ~/atlas", "Found on this machine", "  Codex"]
    assert cropped(lines, 'output, from "Found on this machine" down') == lines[2:]
    assert cropped(lines, "backfill output") == lines


def test_a_redelivery_or_a_repeated_ask_and_its_answer_are_shown_once():
    chat = [("You", "1 prompt"), ("Atlas", "Resuming"), ("You (redelivered)", "1 prompt"), ("Atlas", "Resuming"),
            ("You", "forget"), ("Atlas", "Forgotten."), ("You", "forget"), ("Atlas", "Forgotten.")]
    assert without_redeliveries(chat) == [("You", "1 prompt"), ("Atlas", "Resuming"), ("You", "forget"),
                                          ("Atlas", "Forgotten.")]
