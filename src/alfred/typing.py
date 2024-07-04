"""Common types and type aliases used for the bot.

Attributes
----------
ArgParseAction : type
    The allowed literals for the `argparse` action attribute.
ConfigProcessor : type
    A function that can be used for configuring new attributes for the bot.
ExitCode : type
    The value returned when the program exits.
"""

from typing import Any, Callable, Literal

__all__ = (
    "ConfigProcessor",
    "ExitCode",
)

type ArgParseAction = (
    Literal["store"]
    | Literal["store_const"]
    | Literal["store_true"]
    | Literal["append"]
    | Literal["append_const"]
    | Literal["count"]
    | Literal["help"]
    | Literal["version"]
)

type ConfigProcessor = Callable[[str], Any]

type ExitCode = int | None
