"""Defines the CLI interface of the bot, configures the bot, and then runs it."""

from __future__ import annotations

from alfred.services import run

__all__ = ("main",)


def main() -> None:
    """Run the program."""
    raise SystemExit(run())


if __name__ == "__main__":
    main()
