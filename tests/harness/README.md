# The proof harness

The harness proves Atlas against [the tutorial](../../docs/tutorial/a-week-with-atlas.md), one scene at a time. A scene passes when the plugin, driven only through what `register(ctx)` registered, prints, sends and writes what the tutorial shows. Scene ids are those of [atlas-v1.md §1](../../docs/architecture/atlas-v1.md), with scene 3 checked as 3a, 3b and 3c.

## Run it locally

```
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[test]'
cd tests && ../.venv/bin/python -m harness
```

The report has one line per scene: `pass`, `fail (<reason>)` or `not claimed`. Week mode follows. It replays Sunday to Sunday from an empty home and is reported but never fails the run. The command exits non-zero only when a claimed scene fails. `python -m pytest` runs the claimed scenes too, alongside the unit tests.

Nothing touches your machine. Each run gets a temporary folder holding HOME, the Harness stores, Hermes's home and a fake `hermes` on PATH. No test calls a model or the network.

## What a scene runs in

- **The fixture world** (`world.py`). The tutorial's invented week at reduced scale. Where the tutorial gives a count at full scale ("412 Sessions"), the scene checks the world's own count instead.
- **The fake clock.** `home.set_clock(t)` sets `ATLAS_NOW`, which every shell reads. It also rewrites the stores (`stores.py`), so a Session appears in its store only once the clock has passed its start.
- **The fake Hermes** (`home.py`, `hermes_bin.py`). A plugin context records every registration. `home.cli("atlas", "setup")` runs a CLI command the plugin registered. The fake `hermes` logs each call (`home.hermes_calls()`), keeps cron jobs it creates (`home.cron_jobs()`), and returns scripted results: `home.script_hermes("send -l", stdout=...)`. Its `send` prints what Hermes v0.23.0 prints under its Docker install, start-up lines and then indented JSON, and `home.sent()` lists each message it delivered.
- **The fake Llm** (`fake_llm.py`). Scripted structured outputs per auxiliary task. A call with nothing scripted fails the scene.

## Seed a scene

A scene in `scenes.py` is a `Scene(at, seed, step)`. `seed` runs in scene mode only and builds the state the scene starts from. It writes through `atlas.record`, never with hand-written bytes:

```python
def _seed_board(home):
    home.seed_record(settings, profile)
    home.seed(lambda snap: snap.changes().put_pursuit(greenhouse).put_pursuit(billing))
```

Seed states that several scenes or tests share live in `seeds.py`, such as `seeds.after_scene_6(root)`, the record as scene 6 leaves it. Until collection (#5) writes Sessions into the record, a seed lends its Snapshot the fixture world's Sessions, so Pursuit files can name them.

`step` drives entry points and raises `Failed` with the first difference from the tutorial. `expect_lines(expected, printed)` compares after whitespace normalisation. Read expected text from the docs (`tutorial.fenced_block(...)`), not from a copy in the test.

## Claim a scene

Add the scene to `SCENES` under its id, then add the id to `claimed` in `manifest.toml`. CI runs on every pull request and fails when a claimed scene fails, so a claimed scene stays passing.
