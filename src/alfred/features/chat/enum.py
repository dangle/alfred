"""Enums for the chat feature."""

from __future__ import annotations

import enum
import typing

if typing.TYPE_CHECKING:
    from typing import Literal

__all__ = (
    "ChatGPTModels",
    "MessageRole",
    "ResponseType",
    "ToolParamType",
)


class ChatGPTModels(enum.StrEnum):
    """Valid ChatGPT models."""

    GPT_4O = "gpt-4o"
    GPT_4_TURBO = "gpt-4-turbo"
    GPT_4 = "gpt-4"
    GPT_3_5_TURBO = "gpt-3.5-turbo"


class MessageRole:
    """Valid chat service message roles."""

    Assistant: Literal["assistant"] = "assistant"
    System: Literal["system"] = "system"
    Tool: Literal["tool"] = "tool"
    User: Literal["user"] = "user"


class ResponseType(enum.Enum):
    """Response types for when the bot would not respond to a prompt."""

    ToolCall = enum.auto()
    NoResponse = enum.auto()
    BadResponse = enum.auto()


class ToolParamType:
    """Valid chat service types for tools."""

    Function: Literal["function"] = "function"
