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
import json
import typing
from collections import defaultdict

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
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function

from alfred import bot, db, feature, fields
from alfred.features.chat.activities import THINKING, WAITING_FOR_CORRECTIONS
from alfred.features.chat.constants import (
    MAX_REPLY_LEN,
    NO_RESPONSE,
    NO_RESPONSE_SYSTEM_MESSAGE,
    RETRY_BAD_RESPONSES,
    TIME_TO_WAIT_FOR_CORRECTIONS_S,
    TOOL_SYSTEM_MESSAGE,
)
from alfred.features.chat.context import MessageApplicationContext
from alfred.features.chat.enum import ChatGPTModels, MessageRole, ResponseType, ToolParamType
from alfred.features.chat.tools import get_tools
from alfred.translation import gettext as _

if typing.TYPE_CHECKING:
    from typing import Any, Literal

    from alfred.features.chat.tools import Tool

__all__ = ("Chat",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class Chat(feature.Feature):
    """Manages chat interactions and commands in the bot."""

    #: An asynchronous OpenAI client.
    ai: openai.AsyncOpenAI

    #: The bot to which this feature was attached.
    bot: bot.Bot

    #: The staff member this bot represents.
    staff: db.Staff

    #: The chat service model to use when responding to users.
    #: Defaults to GPT-4o.
    model = fields.ConfigField[str](
        namespace="alfred.openai",
        env="CHATGPT_MODEL",
        default=ChatGPTModels.GPT_4O,
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

    def __init__(self) -> None:
        self._history: dict[int, list[ChatCompletionMessageParam]] = defaultdict(list)
        self._history_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tools: dict[str, Tool] = {}

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
        self._tools = await get_tools(self.bot.application_commands)
        structlog.contextvars.bind_contextvars(tools=list(self._tools))

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
                    role=MessageRole.User,
                    content=message.content,
                    name=author,
                ),
            )

            if message.author.bot:
                await _log.adebug("Another bot is the author of the message.", message=message)
                return

            must_respond: bool = await self._must_respond(message)

            if not must_respond and WAITING_FOR_CORRECTIONS not in self.bot.activities:
                return

            async with self.bot.presence(activity=THINKING, ephemeral=True):
                response: str | ResponseType = await self._get_chat_response_to_history(
                    message,
                    must_respond=must_respond,
                )

                match response:
                    case ResponseType.NoResponse | ResponseType.BadResponse:
                        await _log.ainfo(
                            f"Skipping response to message from {author}.",
                            response=response,
                        )
                        return
                    case ResponseType.ToolCall:
                        pass
                    case _:
                        for chunk in (
                            response[i : i + MAX_REPLY_LEN]
                            for i in range(0, len(response), MAX_REPLY_LEN)
                        ):
                            await message.reply(chunk)

            if must_respond:
                self.bot.dispatch("waiting_for_corrections")

    @feature.listener("on_waiting_for_corrections")
    async def wait_for_corrections(self) -> None:
        """Listen for the `on_waiting_for_corrections` event and set the bot activity."""
        async with self.bot.presence(activity=WAITING_FOR_CORRECTIONS):
            await asyncio.sleep(TIME_TO_WAIT_FOR_CORRECTIONS_S)

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
    ) -> str | ResponseType:
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
            return ResponseType.BadResponse

        if assistant_message == NO_RESPONSE:
            return ResponseType.NoResponse

        if assistant_message:
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role=MessageRole.Assistant,
                    content=message.content,
                ),
            )
            return assistant_message

        await _log.aerror("All responses from the chat service started with the prompt unmodified.")
        return ResponseType.BadResponse

    async def _get_assistant_message(
        self,
        message: discord.Message,
        *,
        must_respond: bool,
    ) -> str | None | Literal[ResponseType.ToolCall]:
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

        for n in range(1, RETRY_BAD_RESPONSES + 1):
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
                return ResponseType.ToolCall

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
        response_message.tool_calls = typing.cast(
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
                    role=MessageRole.Tool,
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
                    role=MessageRole.Assistant,
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
            role=MessageRole.Assistant,
            content=message.content,
            tool_calls=(
                [
                    ChatCompletionMessageToolCallParam(
                        id=tc.id,
                        type=ToolParamType.Function,
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

        response: ChatCompletion = await self.ai.chat.completions.create(**kwargs)

        structlog.contextvars.bind_contextvars(
            chat_usage=(
                {
                    "completion_tokens": response.usage.completion_tokens,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else None
            ),
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
                    role=MessageRole.System,
                    content=(
                        f"{TOOL_SYSTEM_MESSAGE}"
                        f"{"" if must_respond else NO_RESPONSE_SYSTEM_MESSAGE}"
                        f"{system_message}"
                    ),
                ),
            )

        return messages
