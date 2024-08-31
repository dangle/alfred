"""Defines the CLI interface of the bot, configures the bot, and then runs it."""

from __future__ import annotations

from alfred.services.manor import Manor

__all__ = ("main",)


def main() -> None:
    """Run the program."""
    raise SystemExit(Manor.run())


if __name__ == "__main__":
    main()
