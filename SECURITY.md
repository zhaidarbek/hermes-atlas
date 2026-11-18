# Security

Atlas reads your AI conversations and keeps copies of them on your disk, so a flaw in it can expose private text. Please report one privately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository: open the **Security** tab of [zhaidarbek/hermes-atlas](https://github.com/zhaidarbek/hermes-atlas/security) and choose **Report a vulnerability**. Only the maintainer sees the report. Please do not open a public issue or pull request for it.

Include:

- the Atlas version (`version` in `atlas/plugin.yaml`) or the commit, your Hermes version and your Python version;
- what an attacker can do, and what they need first: for example a message in a Session, a file in the inbox, or access to the machine;
- the steps or a small script that shows it, with invented data;
- the fix you suggest, if you have one.

Leave real conversations, keys and passwords out of the report. Atlas masks only the secrets it detects, so check anything you copy from your record folder before you send it.
