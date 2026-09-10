#!/usr/bin/env python3
"""Generate Markdown from argparse help without dispatching any command."""
import argparse
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Software/Python"))
from proact_host import cli


def main():
    class Captured(Exception):
        pass
    captured = []
    def stop_at_parser(parser, *args, **kwargs):
        captured.append(parser)
        raise Captured()
    with patch.object(argparse.ArgumentParser, "parse_args", stop_at_parser):
        try:
            cli.main([])
        except Captured:
            pass
    parser = captured[0]
    parser._optionals.title = "options"  # stable across Python 3.9 and 3.10+
    sections = ["# CLI reference\n", "Generated from the current parser. No command was dispatched and no device was accessed.\n",
                "Global options (`--port`, `--no-color`) precede the subcommand.\n",
                "```text\n" + parser.format_help().rstrip() + "\n```\n"]
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, subparser in commands.choices.items():
        subparser._optionals.title = "options"
        sections.extend([f"## {name}\n", "```text\n" + subparser.format_help().rstrip() + "\n```\n"])
    output = ROOT / "docs/CLI_REFERENCE.md"
    output.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {output}; {len(commands.choices)} commands")


if __name__ == "__main__":
    main()
