"""`hermes atlas mcp` as Hermes runs it: one process that registers the plugin with a fake context
and runs the CLI command the plugin registered. The harness starts it the way a Harness starts an
MCP server, as a child process talking over stdin and stdout."""
import argparse
import sys

import atlas

from .home import FakePluginContext


def main() -> int:
    ctx = FakePluginContext()
    atlas.register(ctx)
    setup, handler = ctx.cli_commands["atlas"]
    parser = argparse.ArgumentParser(prog="hermes atlas")
    setup(parser)
    return handler(parser.parse_args(["mcp"]))


if __name__ == "__main__":
    sys.exit(main())
