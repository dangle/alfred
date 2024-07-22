"""Common types and type aliases used for the bot.

Attributes
----------
ArgParseAction : type
    The allowed literals for the `argparse` action attribute.
ConfigProcessor : type
    A function that can be used for configuring new attributes for the bot.
ExitCode : type
    The value returned when the program exits.
Presence : type
    A named tuple that holds a bot status and activity.

"""

from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Literal

    import discord

__all__ = (
    "ConfigProcessor",
    "ExitCode",
)

type ArgParseAction = (
    Literal[
        "store",
        "store_const",
        "store_true",
        "store_false",
        "append",
        "append_const",
        "count",
        "help",
        "version",
    ]
)

type ConfigProcessor = Callable[[str], Any]

type ExitCode = int | None


class Presence(typing.NamedTuple):
    """A named tuple that holds a bot status and activity."""

    status: discord.Status | None = None
    activity: discord.BaseActivity | None = None
