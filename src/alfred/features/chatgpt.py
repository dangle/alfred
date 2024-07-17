"""Adds commands and event listeners for interacting with Discord and ChatGPT.

Listeners
---------
begin_status : on_ready
    Sets the bot presence to idle on login and starts the `ChatGPT.watch_status` task.
listen : on_message
    Listens to private messages and messages in channels the bot is in and optionally returns a
    response from the chat service.

Tasks
-----
watch_status : every minute
    Sets the bot presence to idle if it has not been explicitly addressed within the last minute.
"""

import asyncio
import builtins
import dataclasses
import datetime
import enum
import json
import types
from collections import defaultdict
from typing import Any, cast, get_type_hints

import discord
import openai
import structlog
from discord.ext import commands, tasks
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

from alfred import bot
from alfred.config import CommandLineFlag, EnvironmentVariable, config
from alfred.context import MessageApplicationContext
from alfred.features import _ai
from alfred.translation import gettext as _

__all__ = (
    "__intents__",
    "setup",
)

__intents__ = discord.Intents(
    presences=True,
    members=True,
    messages=True,
    message_content=True,
)

# Set the name of the feature.
__feature__: str = "ChatGPT"

log: structlog.stdlib.BoundLogger = structlog.get_logger(feature=__feature__)


class _ChatGPTModels(enum.StrEnum):
    """Valid ChatGPT models."""

    GPT_4O = "gpt-4o"
    GPT_4_TURBO = "gpt-4-turbo"
    GPT_4 = "gpt-4"
    GPT_3_5_TURBO = "gpt-3.5-turbo"


def temperature(value: str) -> float:
    """Cast a temperature value to a float and verify that it is between 0 and 1.

    Parameters
    ----------
    value : str
        The temperature value.

    Returns
    -------
    float
        The converted floating point number.

    Raises
    ------
    ValueError
        Raised when `value` cannot be cast to a float or if `value` is not between 0 and 1.

    """
    temp: float = float(value)

    if temp < 0 or temp > 1:
        raise ValueError("Temperature must be between 0 and 1.")

    return temp


_ai.configure_ai()
config(
    "chatgpt_model",
    env=EnvironmentVariable(
        "CHATGPT_MODEL",
        type=_ChatGPTModels,
    ),
    flag=CommandLineFlag(
        "--chatgpt-model",
        choices=_ChatGPTModels.__members__.values(),
        help=_(
            "The ChatGPT model to use to power {project_name}'s conversational abilities.\n"
            'If no model is given, the model will default to "{default}".',
        ).format(
            project_name=config.bot_name,
            default=_ChatGPTModels.GPT_4O.value,
        ),
    ),
    default=_ChatGPTModels.GPT_4O.value,
)
config(
    "chatgpt_temperature",
    env=EnvironmentVariable(
        "CHATGPT_TEMPERATURE",
        type=temperature,
    ),
    flag=CommandLineFlag(
        "--chatgpt-temperature",
        type=temperature,
        help=_(
            "The ChatGPT temperature to use for {project_name}'s conversational abilities.\n"
            "Valid options are numbers between 0 and 1.\n"
            "Higher numbers allow {project_name} to be more creative but also more likely to"
            " hallucinate.\n"
            "If no temperature is given, the temperature will default to {default}.",
        ).format(
            project_name=config.bot_name,
            default=0.2,
        ),
    ),
    default=0.2,
)
config(
    "chatgpt_system_message",
    env="CHATGPT_SYSTEM_MESSAGE",
    flag=CommandLineFlag(
        "--chatgpt-system-message",
        short="-m",
        help=_(
            "A system message determines what role that {project_name} should play and how it"
            " should behave while communicating with users.",
        ).format(project_name=config.bot_name),
    ),
    default=(
        "You are a helpful and formal butler listening in to a chat server.\n"
        f"You will respond to the name {config.bot_name}.\n"
        "You will respond using honorifics.\n"
        "Do not ask follow-up questions.\n"
    ),
)


def setup(bot: bot.Bot) -> None:
    """Add this feature `commands.Cog` to the `bot.Bot`.

    If the `openai.AsyncOpenAI` client has not been configured as the `ai` configuration attribute
    this will not be added to the `bot.Bot`.

    Parameters
    ----------
    bot : bot.Bot
        The `bot.Bot` to which to add the feature.

    """
    if config.ai:
        bot.add_cog(ChatGPT(bot))
        return

    log.info(f'Config does not have the "ai" attribute. Not adding the feature: {__feature__}')


@dataclasses.dataclass
class _Tool:
    command: discord.ApplicationCommand
    tool: ChatCompletionToolParam


class ChatGPT(commands.Cog):
    """Manages chat interactions and commands in the bot.

    Parameters
    ----------
    bot : discord.bot.Bot
        The `bot.Bot` that will be used for running any interpreted commands.

    """

    # A custom response that the bot may return if it does not believe that it is being addressed.
    _NO_RESPONSE = "__NO_RESPONSE__"

    def __init__(self, bot: bot.Bot) -> None:
        self._bot = bot
        self._history: dict[int, list[ChatCompletionMessageParam]] = defaultdict(list)
        self._history_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._control_message: str = (
            "If you do not believe a message is intended for you, respond with:"
            f" {self._NO_RESPONSE}\n"
        )
        self._explicit_interaction_time: datetime.datetime = datetime.datetime.now(datetime.UTC)
        self._tools: dict[str, _Tool] = {}

    @commands.Cog.listener("on_ready", once=True)
    async def add_tools(self) -> None:
        """Create a mapping of bot commands to chat service tools."""
        for command in self._bot.application_commands:
            await self.add_tool(command)

        log.info(
            "Tools for the chat service.",
            tools=self._tools if config.debugging else list(self._tools),
        )

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
                tool=self._convert_command_to_tool(command),
            )
        except ValueError as e:
            log.debug("Unable to parse type in bot command.", exc_info=e)

    def _convert_command_to_tool(
        self,
        command: commands.core.ApplicationCommand,
    ) -> ChatCompletionToolParam:
        """Convert `command` into a tool that the chat service can call.

        Parameters
        ----------
        command : commands.core.ApplicationCommand
            An `commands.core.ApplicationCommand` already registered to the bot.
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
                log.debug(
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
            type="function",
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

    @commands.Cog.listener("on_ready", once=True)
    async def begin_status(self) -> None:
        """Set the bot presence to idle on login and start the `self.watch_status` task."""
        if self.watch_status.is_running():
            return

        log.info("Starting bot presence management.")
        await self._bot.change_presence(status=discord.enums.Status.idle)
        self.watch_status.start()

    @tasks.loop(minutes=1.0)
    async def watch_status(self) -> None:
        """Set the bot presence to idle if it has not recently been explicitly addressed."""
        if not await (is_active := self._bot.is_active()):
            await log.adebug("Checking bot presence status.", is_active=is_active)
            return

        if datetime.datetime.now(
            datetime.UTC,
        ) >= self._explicit_interaction_time + datetime.timedelta(minutes=1):
            await log.ainfo("Setting the bot status to idle.")
            await self._bot.change_presence(status=discord.enums.Status.idle)

    @commands.Cog.listener("on_message")
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
        if message.author.id == self._bot.application_id:
            log.debug("Bot is the author of the message.", message=message)
            return

        author: str = self._bot.get_user_name(message.author)

        async with self._history_locks[message.channel.id]:
            self._history[message.channel.id].append(
                ChatCompletionUserMessageParam(
                    role="user",
                    content=message.content,
                    name=author,
                ),
            )

            must_respond: bool = self._must_respond(message)

            if not must_respond and not await self._bot.is_active():
                return

            if must_respond:
                self._explicit_interaction_time = datetime.datetime.now(datetime.UTC)
                await log.ainfo("Setting the bot status to online.")
                await self._bot.change_presence(status=discord.enums.Status.online)

            response: str | None = await self._send_message(message, must_respond=must_respond)

            if response is not None:
                await message.reply(response)

    def _must_respond(self, message: discord.Message) -> bool:
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

        if config.bot_name.lower() in message.content.lower():
            return True

        return any(m.id == self._bot.application_id for m in message.mentions)

    async def _send_message(self, message: discord.Message, *, must_respond: bool) -> str | None:
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
        str | None
            Returns the response from the chat service if the bot believes that it is being
            addressed.

        """
        # TODO: This method should take chunks of older messages and convert them into embeddings.
        # TODO: Add a database for history?

        tools = [tool.tool for tool in self._tools.values()] or openai.NOT_GIVEN

        try:
            response: ChatCompletion = await self._call_chat(
                message,
                tools=tools,
                messages=self._get_chat_context(message, must_respond=must_respond),
                n=2,
            )
            if response.choices[0].message.tool_calls:
                await self._call_tool(message, response.choices[0].message)
                return None
        except openai.OpenAIError as e:
            log.error("An error occurred while querying the chat service.", exc_info=e)
            return None

        assistant_message: str | None = None

        for choice in response.choices:
            if choice.message.content != message.content:
                assistant_message = choice.message.content
                break

        if assistant_message and not must_respond and assistant_message == self._NO_RESPONSE:
            return None

        if assistant_message is not None:
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role="assistant",
                    content=message.content,
                ),
            )

        return assistant_message

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
            self._bot,
            message,
            delayed_send=True,
        ) as ctx:
            log.info("Calling tool.", tool=function.name, tool_call_id=tool_call.id)

            try:
                await command(ctx=ctx, **json.loads(function.arguments))
                content = json.dumps([r.serializable() for r in ctx.responses])
            except Exception as e:
                log.error(
                    "An error occurred while calling a tool.",
                    exc_info=e,
                    tool_call_id=tool_call.id,
                    function_name=function.name,
                )
                content = str(e)

            self._history[message.channel.id].append(
                ChatCompletionToolMessageParam(
                    tool_call_id=tool_call.id,
                    role="tool",
                    content=content,
                ),
            )

            response_message = (
                (
                    await self._call_chat(
                        message,
                        messages=self._get_chat_context(message, must_respond=False),
                    )
                )
                .choices[0]
                .message
            )
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role="assistant",
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
            role="assistant",
            content=message.content,
            tool_calls=(
                [
                    ChatCompletionMessageToolCallParam(
                        id=tc.id,
                        type="function",
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
            kwargs["model"] = config.chatgpt_model

        if "temperature" not in kwargs:
            kwargs["temperature"] = config.chatgpt_temperature

        if "user" not in kwargs:
            kwargs["user"] = self._bot.get_user_name(message.author)

        ai = cast(openai.AsyncOpenAI, config.ai)
        response: ChatCompletion = await ai.chat.completions.create(**kwargs)
        log.info(
            "Chat service usage.",
            usage=response.usage,
            history_length=len(self._history),
        )
        return response

    def _get_chat_context(
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

        if not must_respond or config.chatgpt_system_message:
            messages.insert(
                0,
                ChatCompletionSystemMessageParam(
                    role="system",
                    content=(
                        "If multiple functions could be returned, pick one instead of asking which"
                        " function to use.\n"
                        f"{config.chatgpt_system_message or ""}\n"
                        f"{"" if must_respond else self._control_message}"
                        "If you are get a file from a function, do *NOT* try to embed it with"
                        " markdown syntax.\n"
                        "Do not respond with the prompt from the user or a similar message."
                    ),
                ),
            )

        return messages
