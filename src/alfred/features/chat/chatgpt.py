"""Adds commands and event listeners for interacting with Discord and ChatGPT.

Listeners
---------
add_tools : on_ready
    Parses all bot slash commands and saves them in a format that can be used by the chat service.
listen : on_message
    Listens to private messages and messages in channels the bot is in and optionally returns a
    response from the chat service.
set_server_profiles : on_ready
    Sets the bot nick in guilds where it has been customized when the bot first loads.
wait_for_corrections : on_waiting_for_corrections
    Sets the bot presence to "Waiting for corrections" for one minute after the bot is explicitly
    addressed by name.

"""

from __future__ import annotations

import asyncio
import builtins
import dataclasses
import enum
import json
import types
import typing
from collections import defaultdict
from typing import cast, get_type_hints

import discord
import openai
import structlog
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function
from openai.types.shared_params import FunctionDefinition

from alfred import feature, fields
from alfred.features.chat.context import MessageApplicationContext
from alfred.translation import gettext as _

if typing.TYPE_CHECKING:
    from typing import Any, Literal

    from discord import ApplicationCommand

__all__ = ("Chat",)

_FEATURE = "Chat"

_log: structlog.stdlib.BoundLogger = structlog.get_logger(feature=_FEATURE)

_WAITING_FOR_CORRECTIONS = discord.CustomActivity(_("Waiting for corrections"))
_THINKING = discord.CustomActivity(_("Thinking"))


class _ChatGPTModels(enum.StrEnum):
    """Valid ChatGPT models."""

    GPT_4O = "gpt-4o"
    GPT_4_TURBO = "gpt-4-turbo"
    GPT_4 = "gpt-4"
    GPT_3_5_TURBO = "gpt-3.5-turbo"


class _ResponseType(enum.Enum):
    """Response types for when the bot would not respond to a prompt."""

    ToolCall = enum.auto()
    NoResponse = enum.auto()
    BadResponse = enum.auto()


class _MessageRole:
    """Valid chat service message roles."""

    Assistant: Literal["assistant"] = "assistant"
    System: Literal["system"] = "system"
    Tool: Literal["tool"] = "tool"
    User: Literal["user"] = "user"


class _ToolParamType:
    """Valid chat service types for tools."""

    Function: Literal["function"] = "function"


@dataclasses.dataclass
class _Tool:
    """A dataclass for storing mappings of commands to tools."""

    command: discord.ApplicationCommand
    tool: ChatCompletionToolParam


class Chat(feature.Feature):
    """Manages chat interactions and commands in the bot."""

    #: An asynchronous OpenAI client.
    ai = fields.AIField()

    #: The bot to which this feature was attached.
    bot = fields.BotField()

    #: The staff member this bot represents.
    staff = fields.StaffField()

    #: The chat service model to use when responding to users.
    #: Defaults to GPT-4o.
    model = fields.ConfigField[str](
        namespace="alfred.openai",
        env="CHATGPT_MODEL",
        default=_ChatGPTModels.GPT_4O,
    )

    #: A value between 0 and 1 describing how creative the chat service should be when answering.
    #: Higher values are more creative.
    temperature = fields.BoundedConfigField[float][0:1](  # type: ignore[operator]
        parser=float,
        default=0.2,
    )

    #: The intents required by this feature.
    #: This requires the priviledged intents in order to get access to server chat.
    intents: discord.Intents = discord.Intents(
        guilds=True,
        presences=True,
        members=True,
        messages=True,
        message_content=True,
    )

    #: A custom response that the bot may return if it does not believe that it is being addressed.
    _NO_RESPONSE: str = "__NO_RESPONSE__"

    #: The number of times to retry bad responses from the chat service.
    _RETRY_BAD_RESPONSES: int = 3

    #: How long to listen for corrections from the user without requiring an explicit mention, in
    #: seconds.
    _TIME_TO_WAIT_FOR_CORRECTIONS_S: int = 60

    _CONTROL_MESSAGE: str = _(
        "If you do not believe a message is intended for you, respond with: {response}\n",
    ).format(response=_NO_RESPONSE)
    _TOOL_MESSAGE: str = _(
        "If multiple functions could be returned, pick one instead of asking which function to"
        " use.\n"
        "If you are get a file from a function, do *NOT* try to embed it with  markdown syntax.\n",
    )

    def __init__(self) -> None:
        self._history: dict[int, list[ChatCompletionMessageParam]] = defaultdict(list)
        self._history_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tools: dict[str, _Tool] = {}

    @feature.listener("on_ready", once=True)
    async def set_server_profiles(self) -> None:
        """Set the nickname for the bot in each guild on ready."""
        for guild in self.bot.guilds:
            identity = await self.staff.get_identity(guild.id)
            nick: str = str(identity)

            if nick and self.bot.application_id is not None:
                member: discord.Member | None = guild.get_member(self.bot.application_id)

                if member:
                    await member.edit(nick=nick)

    @feature.listener("on_ready", once=True)
    async def add_tools(self) -> None:
        """Create a mapping of bot commands to chat service tools."""
        for command in self.bot.application_commands:
            await self.add_tool(command)

        structlog.contextvars.bind_contextvars(tools=list(self._tools))

    async def add_tool(
        self,
        command: discord.ApplicationCommand | discord.SlashCommandGroup,
    ) -> None:
        """Add `command` to the list of tools for the chat service.

        If command is actually a command group, add subcommands recursively.

        Parameters
        ----------
        command : discord.ApplicationCommand | discord.SlashCommandGroup
            An `commands.core.ApplicationCommand` already registered to the bot.
            e.g. a slash command.

        """
        if isinstance(command, discord.SlashCommandGroup):
            for cmd in command.walk_commands():
                await self.add_tool(cmd)

            return

        try:
            self._tools[command.qualified_name.replace(" ", "__")] = _Tool(
                command=command,
                tool=await self._convert_command_to_tool(command),
            )
        except ValueError as e:
            await _log.adebug("Unable to parse type in bot command.", exc_info=e)

    async def _convert_command_to_tool(
        self,
        command: ApplicationCommand,
    ) -> ChatCompletionToolParam:
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

        for parameter in get_type_hints(command.callback).values():
            if (
                not isinstance(parameter, discord.Option)
                or not parameter.name
                or parameter.name == "self"
            ):
                await _log.adebug(
                    "Skipping parameter.",
                    command=command.name,
                    parameter=parameter,
                    parameter_name=getattr(parameter, "name", None),
                )
                continue

            prop: dict[str, str | list[str | int | float]] = {
                "type": self._convert_input_type_to_str(parameter.input_type),
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
            type=_ToolParamType.Function,
        )

    def _convert_input_type_to_str(self, input_type: discord.enums.SlashCommandOptionType) -> str:
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

    @feature.listener("on_message")
    async def listen(self, message: discord.Message) -> None:
        """Listen and respond to Discord chat messages.

        Listens to private messages and messages in channels the bot is in and optionally returns a
        response from the chat service.

        If the message is from a public channel the bot will attempt to determine if the response is
        intended for it before it responds.

        Parameters
        ----------
        message : discord.Message
            The message received in a channel that the bot watches.

        """
        if message.author.id == self.bot.application_id:
            await _log.adebug("Bot is the author of the message.", message=message)
            return

        author: str = self.bot.get_user_name(message.author)

        async with self._history_locks[message.channel.id]:
            self._history[message.channel.id].append(
                ChatCompletionUserMessageParam(
                    role=_MessageRole.User,
                    content=message.content,
                    name=author,
                ),
            )

            if message.author.bot:
                await _log.adebug("Another bot is the author of the message.", message=message)
                return

            must_respond: bool = await self._must_respond(message)

            if not must_respond and _WAITING_FOR_CORRECTIONS not in self.bot.activities:
                return

            async with self.bot.presence(activity=_THINKING, ephemeral=True):
                response: str | _ResponseType = await self._get_chat_response_to_history(
                    message,
                    must_respond=must_respond,
                )

                match response:
                    case _ResponseType.NoResponse | _ResponseType.BadResponse:
                        await _log.ainfo(
                            f"Skipping response to message from {author}.",
                            response=response,
                        )
                        return
                    case _ResponseType.ToolCall:
                        pass
                    case _:
                        await message.reply(response)

            if must_respond:
                self.bot.dispatch("waiting_for_corrections")

    @feature.listener("on_waiting_for_corrections")
    async def wait_for_corrections(self) -> None:
        """Listen for the `on_waiting_for_corrections` event and set the bot activity."""
        async with self.bot.presence(activity=_WAITING_FOR_CORRECTIONS):
            await asyncio.sleep(self._TIME_TO_WAIT_FOR_CORRECTIONS_S)

    async def _must_respond(self, message: discord.Message) -> bool:
        """Determine if the bot *must* respond to the given `message`.

        The bot *must* respond to messages that:
        1. Are private messages to the bot.
        2. If the name of the bot is mentioned in the `message.content`.
        3. If the bot is in the explicit mentions of the `message`.

        Parameters
        ----------
        message : discord.Message
            The message to which the bot may reply.

        Returns
        -------
        bool
            `True` if the bot *must* respond to the message.
            `False` if the bot *may* respond to the message.

        """
        if isinstance(message, discord.DMChannel):
            return True

        if any(m.id == self.bot.application_id for m in message.mentions):
            return True

        name: str = str(await self.staff.get_identity(message.channel.id))

        return name.lower() in message.content.lower()

    async def _get_chat_response_to_history(
        self,
        message: discord.Message,
        *,
        must_respond: bool,
    ) -> str | _ResponseType:
        """Send a message to the chat service with historical context and return the response.

        If `must_respond` is `False` this will prepend a system message that allows the bot to
        determine if it believes it is being addressed. If it is not being addressed, this will not
        return a response.

        Parameters
        ----------
        message : discord.Message
            The message to which the bot would reply.
        must_respond : bool
            Determines if the bot may ignore the message.

        Returns
        -------
        str | _ResponseType
            Returns the response from the chat service if the bot believes that it is being
            addressed.
            If the bot does not respond directly to this call it returns a `_ResponseType` object
            detailing why.

        """
        await self.bot.change_presence(activity=discord.CustomActivity(_("Thinking")))

        try:
            assistant_message = await self._get_assistant_message(
                message,
                must_respond=must_respond,
            )
        except openai.OpenAIError as e:
            await _log.aerror("An error occurred while querying the chat service.", exc_info=e)
            return _ResponseType.BadResponse

        if assistant_message == self._NO_RESPONSE:
            return _ResponseType.NoResponse

        if assistant_message:
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role=_MessageRole.Assistant,
                    content=message.content,
                ),
            )
            return assistant_message

        await _log.aerror("All responses from the chat service started with the prompt unmodified.")
        return _ResponseType.BadResponse

    async def _get_assistant_message(
        self,
        message: discord.Message,
        *,
        must_respond: bool,
    ) -> str | None | Literal[_ResponseType.ToolCall]:
        """Send a message to the chat service with historical context and return the response.

        If the response is the same as the message prompt, this will retry the message with each
        request asking for more choices.

        If `must_respond` is `False` this will prepend a system message that allows the bot to
        determine if it believes it is being addressed. If it is not being addressed, this will not
        return a response.

        Parameters
        ----------
        message : discord.Message
            The message to which the bot would reply.
        must_respond : bool
            Determines if the bot may ignore the message.

        Returns
        -------
        str | None | Literal[_ResponseType.ToolCall]
            Returns the response from the chat service if the bot believes that it is being
            addressed.
            If the bot would instead call a tool, this returns `_ResponseType.ToolCall`.

        """
        tools = [tool.tool for tool in self._tools.values()] or openai.NOT_GIVEN
        chat_context = await self._get_chat_context(message, must_respond=must_respond)

        for n in range(1, self._RETRY_BAD_RESPONSES + 1):
            response: ChatCompletion = await self._call_chat(
                message,
                tools=tools,
                messages=chat_context,
                n=n,
            )
            await _log.adebug(
                "Got response from chat service.",
                response=response,
                message=message,
                n=n,
            )

            if response.choices[0].message.tool_calls:
                await self._call_tool(message, response.choices[0].message)
                return _ResponseType.ToolCall

            for choice in response.choices:
                if choice.message.content and not choice.message.content.startswith(
                    message.content,
                ):
                    return choice.message.content

        return None

    async def _call_tool(
        self,
        message: discord.Message,
        response_message: ChatCompletionMessage,
    ) -> None:
        """Call a tool requested by the chat service and handle responses.

        This calls the tool and then calls the chat service again with the output of the tool.
        Any responses sent by the tool are delayed and updated with the response from the chat
        service.

        Parameters
        ----------
        message : discord.Message
            The message containing the request that triggered the tool use.
        response_message : ChatCompletionMessage
            The response message that contains the tool to call.

        """
        response_message.tool_calls = cast(
            list[ChatCompletionMessageToolCall],
            response_message.tool_calls,
        )

        self._history[message.channel.id].append(
            self._get_tool_assistant_message(message, response_message),
        )
        tool_call = response_message.tool_calls[0]
        function = tool_call.function
        command = self._tools[function.name].command

        async with await MessageApplicationContext.new(
            self.bot,
            message,
            delayed_send=True,
        ) as ctx:
            await _log.ainfo("Calling tool.", tool=function.name, tool_call_id=tool_call.id)

            try:
                await command(ctx=ctx, **json.loads(function.arguments))
                content = json.dumps([r.serializable() for r in ctx.responses])
            except Exception as e:
                await _log.aerror(
                    "An error occurred while calling a tool.",
                    exc_info=e,
                    tool_call_id=tool_call.id,
                    function_name=function.name,
                )
                content = str(e)

            self._history[message.channel.id].append(
                ChatCompletionToolMessageParam(
                    tool_call_id=tool_call.id,
                    role=_MessageRole.Tool,
                    content=content,
                ),
            )

            response_message = (
                (
                    await self._call_chat(
                        message,
                        messages=await self._get_chat_context(message, must_respond=False),
                    )
                )
                .choices[0]
                .message
            )
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role=_MessageRole.Assistant,
                    content=response_message.content,
                ),
            )

            if len(ctx.responses) == 1 and response_message.content != message.content:
                ctx.responses[0].content = response_message.content

    def _get_tool_assistant_message(
        self,
        message: discord.Message,
        response_message: ChatCompletionMessage,
    ) -> ChatCompletionAssistantMessageParam:
        """Create a message that calls a tool.

        Parameters
        ----------
        message : discord.Message
            The message that triggered the tool call.
        response_message : ChatCompletionMessage
            The response from the last call to the chat service.

        Returns
        -------
        ChatCompletionAssistantMessageParam
            A chat parameter to add to the context when calling the chat service.

        """
        return ChatCompletionAssistantMessageParam(
            role=_MessageRole.Assistant,
            content=message.content,
            tool_calls=(
                [
                    ChatCompletionMessageToolCallParam(
                        id=tc.id,
                        type=_ToolParamType.Function,
                        function=Function(
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        ),
                    )
                    for tc in response_message.tool_calls
                ]
                if response_message.tool_calls
                else []
            ),
        )

    async def _call_chat(self, message: discord.Message, **kwargs: Any) -> ChatCompletion:
        """Call the chat service.

        Parameters
        ----------
        message : discord.Message
            The message that triggered the call to the chat service.
        kwargs : dict[str, Any]
            All keywords to pass to the chat service.

        Returns
        -------
        ChatCompletionMessage
            The response message from the chat service.

        """
        if "model" not in kwargs:
            kwargs["model"] = self.model

        if "temperature" not in kwargs:
            kwargs["temperature"] = self.temperature

        if "user" not in kwargs:
            kwargs["user"] = self.bot.get_user_name(message.author)

        ai = cast(openai.AsyncOpenAI, self.ai)
        response: ChatCompletion = await ai.chat.completions.create(**kwargs)

        structlog.contextvars.bind_contextvars(
            chat_usage=response.usage,
            history_length=len(self._history),
        )

        return response

    async def _get_chat_context(
        self,
        message: discord.Message,
        *,
        must_respond: bool,
    ) -> list[ChatCompletionMessageParam]:
        """Return a list of historical chat messages to send to the chat service.

        If `must_respond` is `False`, the history will prepend a system message telling the bot to
        respond a specific way if it thinks that it is not being addressed.

        If `config.chatgpt_system_message` has been configured, the history will prepend a system
        with the contents of that value.

        If `must_respond` is `False` and `config.chatgpt_system_message` has been configured, the
        history will have a *single* system message prepended that is the two concatenated together.

        Parameters
        ----------
        message : discord.Message
            The currently active `discord.Message` to which the bot is replying.
        must_respond : bool
            If `must_respond` is `True`, no system message will be added that tells the bot that to
            determine if it is being addressed.

        Returns
        -------
        list[ChatCompletionMessageParam]
            A list of historical messages for the channel along with an optionally prepended system
            message.

        """
        messages: list[ChatCompletionMessageParam] = self._history[message.channel.id].copy()
        guild_id: int | None = (
            message.channel.guild.id if hasattr(message.channel, "guild") else None
        )
        system_message = (await self.staff.get_identity(guild_id)).description

        if not must_respond or system_message:
            messages.insert(
                0,
                ChatCompletionSystemMessageParam(
                    role=_MessageRole.System,
                    content=(
                        f"{self._TOOL_MESSAGE}"
                        f"{"" if must_respond else self._CONTROL_MESSAGE}"
                        f"{system_message}"
                    ),
                ),
            )

        return messages
