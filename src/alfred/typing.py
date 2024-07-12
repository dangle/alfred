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
