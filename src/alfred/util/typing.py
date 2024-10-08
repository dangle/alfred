"""Common types and type aliases used for the bot."""

from __future__ import annotations

import typing
from abc import ABCMeta, abstractmethod
from typing import Protocol

if typing.TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Literal

    import discord

__all__ = (
    "ArgParseAction",
    "Comparable",
    "ConfigValue",
    "ConfigProcessor",
    "ExitCode",
    "Presence",
    "ProtocolMeta",
)

#: The allowed literals for the `argparse` action attribute.
type ArgParseAction = Literal[
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

#: A value that can be passed to a ConfigParser.
type ConfigValue = str | list[str] | dict[str, Any]

#: A function that can be used for configuring new attributes for the bot.
type ConfigProcessor[T] = Callable[[ConfigValue], T]

#: The value returned when the program exits.
type ExitCode = int | None


class Presence(typing.NamedTuple):
    """A named tuple that holds a bot status and activity."""

    #: The Discord status portion of the 'Presence' such as "online", "offline", or "idle".
    status: discord.Status | None = None

    #: The Discord activity portion of the 'Presence' that can be used to customize the message
    #: shown by Discord to indicate what the bot is doing.
    activity: discord.BaseActivity | None = None


class Comparable[T](Protocol):
    """Protocol for annotating comparable types."""

    @abstractmethod
    def __lt__(self: T, other: T) -> bool:  # noqa: D105
        pass


#: Necessary to make metaclasses usable with protocols.
ProtocolMeta: type = ABCMeta if typing.TYPE_CHECKING else type(Protocol)
