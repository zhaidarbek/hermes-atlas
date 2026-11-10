"""The fake `hermes` executable. The harness copies this file onto PATH as `hermes`.

Every call is appended to $FAKE_HERMES_DIR/calls.jsonl. Each subcommand has a default
behaviour; $FAKE_HERMES_DIR/script.json can replace any of them with a scripted result
{"exit": n, "stdout": "...", "stderr": "..."}, keyed as in ROUTES. Cron jobs created here
persist in jobs.json, so `cron list` shows them, as Hermes's does.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_HERMES_DIR"])
HOME_CHANNELS = {"platforms": {"telegram": [{"id": "5550001", "name": "Owner (home)"}]}}
STARTUP = "[stage2] Starting Hermes in its container\nSyncing bundled skills ... done\n"


def _jobs() -> list[dict]:
    path = STATE / "jobs.json"
    return json.loads(path.read_text()) if path.exists() else []


def send_list(args):
    return 0, json.dumps(HOME_CHANNELS), ""


def send(args):
    """As Hermes v0.23.0 under its Docker install: container start-up lines, then the result as
    indented JSON (hermes_cli/send_cmd.py, tools/send_message_senders.py), naming the chat it
    resolved the target to. FAKE_HERMES_SEND_SECONDS makes the send slow."""
    time.sleep(float(os.environ.get("FAKE_HERMES_SEND_SECONDS", "0")))
    sent = STATE / "sent.jsonl"
    n = sum(1 for _ in sent.open()) if sent.exists() else 0
    message_id = str(8900 + n)
    with sent.open("a") as fh:
        fh.write(json.dumps({"argv": args, "message_id": message_id}) + "\n")
    platform, _, chat = args[args.index("--to") + 1].partition(":")
    home = not chat
    chat_id = HOME_CHANNELS["platforms"][platform][0]["id"] if home else chat.split(":")[0]
    result = {"success": True, "platform": platform, "chat_id": chat_id, "message_id": message_id}
    if home:
        result["note"] = f"Sent to {platform} home channel (chat_id: {chat_id})"
    return 0, STARTUP + json.dumps(result, indent=2) + "\n", ""


def cron_list(args):
    jobs = _jobs()
    if not jobs:
        return 0, "No scheduled jobs in profile 'default'.\nCreate one with 'hermes cron create ...'.\n", ""
    lines = ["Scheduled Jobs (profile: default)"]
    for job in jobs:
        lines += [f"  {job['id']} [active]", f"    {'Name:':<11}{job['name']}", f"    {'Schedule:':<11}{job['schedule']}",
                  f"    {'Script:':<11}{job['script']}", ""]
    return 0, "\n".join(lines) + "\n", ""


def cron_create(args):
    """Parsed as Hermes v0.23.0 parses it (hermes_cli/subcommands/cron.py): the schedule is
    positional, and an unknown flag such as --schedule is a usage error."""
    parser = argparse.ArgumentParser(prog="hermes cron create")
    parser.add_argument("schedule")
    parser.add_argument("prompt", nargs="?")
    for flag in ("--name", "--deliver", "--script"):
        parser.add_argument(flag)
    parser.add_argument("--catch-up", choices=("on", "off"))
    parser.add_argument("--no-agent", action="store_true")
    try:
        ns = parser.parse_args(args[2:])
    except SystemExit:
        return 2, "", "hermes cron create: usage error\n"
    jobs = _jobs()
    job = {"id": f"job{len(jobs) + 1}", "name": ns.name or "(unnamed)", "schedule": ns.schedule,
           "script": ns.script, "no_agent": ns.no_agent, "catch_up": ns.catch_up, "deliver": ns.deliver}
    (STATE / "jobs.json").write_text(json.dumps(jobs + [job]))
    return 0, f"Created job {job['id']}\n", ""


ROUTES = (
    ("send -l", lambda a: a[:1] == ["send"] and ("-l" in a or "--list" in a), send_list),
    ("send", lambda a: a[:1] == ["send"], send),
    ("cron list", lambda a: a[:2] == ["cron", "list"], cron_list),
    ("cron create", lambda a: a[:2] == ["cron", "create"], cron_create),
)


def main(args):
    with (STATE / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps(args) + "\n")
    scripted = json.loads((STATE / "script.json").read_text()) if (STATE / "script.json").exists() else {}
    for key, matches, default in ROUTES:
        if matches(args):
            if key in scripted:
                r = scripted[key]
                code, out, err = r.get("exit", 0), r.get("stdout", ""), r.get("stderr", "")
            else:
                code, out, err = default(args)
            sys.stdout.write(out)
            sys.stderr.write(err)
            return code
    sys.stderr.write(f"fake hermes: no route for {' '.join(args)}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
