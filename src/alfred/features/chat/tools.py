"""Helpers for using tools with the chat feature."""

from __future__ import annotations

import builtins
import dataclasses
import types
import typing

import discord
import structlog
from openai.types.chat import ChatCompletionToolParam
from openai.types.shared_params import FunctionDefinition

from alfred.features.chat.enum import ToolParamType

if typing.TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from discord import ApplicationCommand


__all__ = (
    "Tool",
    "get_tools",
)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclasses.dataclass
class Tool:
    """A dataclass for storing mappings of commands to tools."""

    #: The 'discord.ApplicationCommand' that will be called by the tool.
    command: discord.ApplicationCommand

    #: The tool to be sent to the chat service so that it may invoke the command.
    tool: ChatCompletionToolParam


def get_tools(application_commands: Iterable[discord.ApplicationCommand]) -> dict[str, Tool]:
    """Map bot command names to chat service tools.

    Parameters
    ----------
    application_commands : Iterable[discord.ApplicationCommand]
        The commands to convert into 'Tool' objects.

    Returns
    -------
    dict[str, Tool]
        A mapping of 'discord.ApplicationCommand' names to 'Tool' objects.

    """
    tools: dict[str, Tool] = {}

    def add_tool(command: discord.ApplicationCommand) -> None:
        if isinstance(command, discord.SlashCommandGroup):
            for cmd in command.walk_commands():
                add_tool(cmd)

            return

        try:
            tools[command.qualified_name.replace(" ", "__")] = Tool(
                command=command,
                tool=_convert_command_to_tool(command),
            )
        except ValueError as e:
            _log.debug("Unable to parse type in bot command.", exc_info=e)

    for command in application_commands:
        add_tool(command)

    return tools


def _convert_command_to_tool(command: ApplicationCommand) -> ChatCompletionToolParam:
    """Convert `command` into a tool that the chat service can call.

    Parameters
    ----------
    command : ApplicationCommand
        An `ApplicationCommand` already registered to the bot.
        e.g. a slash command.

    Returns
    -------
    ChatCompletionToolParam
        A tool the chat service is capable of calling.

    Raises
    ------
    ValueError
        Raised when a command cannot be converted to an acceptable format.

    """
    parameters: dict[str, Any] = {
        "type": "object",
        "required": [],
        "properties": {},
    }

    for parameter in typing.get_type_hints(command.callback).values():
        if (
            not isinstance(parameter, discord.Option)
            or not parameter.name
            or parameter.name == "self"
        ):
            _log.debug(
                "Skipping parameter.",
                command=command.name,
                parameter=parameter,
                parameter_name=getattr(parameter, "name", None),
            )
            continue

        prop: dict[str, str | list[str | int | float]] = {
            "type": _convert_input_type_to_str(parameter.input_type),
            "description": parameter.description,
        }

        if parameter.choices:
            prop["enum"] = [choice.value for choice in parameter.choices]

        parameters["properties"][parameter.name] = prop

        if parameter.required:
            parameters["required"].append(parameter.name)

    return ChatCompletionToolParam(
        function=FunctionDefinition(
            name=command.qualified_name.replace(" ", "__"),
            description=command.callback.__doc__ or "",
            parameters=parameters,
        ),
        type=ToolParamType.Function,
    )


def _convert_input_type_to_str(input_type: discord.enums.SlashCommandOptionType) -> str:
    """Convert `input_type` into a string acceptable to the chat service.

    Parameters
    ----------
    input_type : discord.enums.SlashCommandOptionType
        The input type of the parameter.

    Returns
    -------
    str
        The input type of the parameter in a format the chat service will accept.

    Raises
    ------
    ValueError
        Raised when `input_type` cannot be converted to an acceptable format.

    """
    match input_type:
        case builtins.str | discord.enums.SlashCommandOptionType.string:
            return "string"
        case builtins.int | discord.enums.SlashCommandOptionType.integer:
            return "integer"
        case builtins.float | discord.enums.SlashCommandOptionType.number:
            return "number"
        case builtins.bool | discord.enums.SlashCommandOptionType.boolean:
            return "boolean"
        case types.NoneType:
            return "null"
        case builtins.dict:
            return "object"

    raise ValueError(
        f"Unable to convert {input_type!r} to a valid format for the chat service.",
    )
