"""CLI entrypoint wrapper."""

import os

# Set CLI context for logging configuration — must happen before hive imports
os.environ["HIVE_CLI_CONTEXT"] = "1"

from .typer_app import run as _run_typer


def main(argv: list[str] | None = None):
    """Run the Hive CLI."""
    _run_typer(argv)


if __name__ == "__main__":
    main()
