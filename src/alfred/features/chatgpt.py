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

import datetime
import enum
from typing import cast

import discord
import openai
import structlog
from discord.ext import commands, tasks
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from .. import bot
from ..config import CommandLineFlag, EnvironmentVariable, config
from ..translation import gettext as _
from . import _ai

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
            "If not supplied, {project_name} will look for the CHATGPT_MODEL environment"
            " variable.\n"
            'If no model is given, the model will default to "{default}".'
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
            "If not supplied, {project_name} will look for the CHATGPT_TEMPERATURE environment"
            " variable.\n"
            "If no temperature is given, the temperature will default to {default}."
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
            " should behave while communicating with users.\n"
            "If not supplied, {project_name} will look for the CHATGPT_SYSTEM_MESSAGE environment"
            " variable."
        ),
    ),
    default=(
        "You are a helpful and formal butler listening in to a chat server.\n"
        f"Your name is {config.bot_name}.\n"
        "You will respond using honorifics.\n"
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

    log.info(
        f'Config does not have the "ai" attribute. Not adding the feature: {__feature__}'
    )


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
        self._history: dict[int, list[ChatCompletionMessageParam]] = {}
        self._control_message: str = (
            "If you do not believe a message is intended for you, respond with:"
            f" {self._NO_RESPONSE}\n"
        )
        self._last_explicit_interaction_time: datetime.datetime = datetime.datetime.now()

    @commands.Cog.listener("on_ready")
    async def begin_status(self) -> None:
        """Set the bot presence to idle on login and start the `self.watch_status` task."""

        if self.watch_status.is_running():
            return

        await self._bot.change_presence(status=discord.enums.Status.idle)

        self.watch_status.start()

    @tasks.loop(minutes=1.0)
    async def watch_status(self) -> None:
        """
        Set the bot presence to idle if it has not been explicitly addressed within the last minute.
        """

        if not await self._is_active():
            return

        if datetime.datetime.now() >= self._last_explicit_interaction_time:
            await self._bot.change_presence(status=discord.enums.Status.idle)

    async def _is_active(self) -> bool:
        """Determine if the bot presence is online.

        Returns
        -------
            `True` if the bot has a presence that is set to `discord.enums.Status.online`.
            `False` if the bot has any other presence value.
        """

        if not self._bot.application_id or not self._bot.guilds:
            return False

        guild = self._bot.guilds[0]
        member = guild.get_member(self._bot.application_id)

        if not member:
            return False

        return member.status == discord.enums.Status.online

    @commands.Cog.listener("on_message")
    async def listen(self, message: discord.Message) -> None:
        """
        Listen to private messages and messages in channels the bot is in and optionally return a
        response from the chat service.

        If the message is from a public channel the bot will attempt to determine if the response is
        intended for it before it responds.

        Parameters
        ----------
        message : discord.Message
            The message received in a channel that the bot watches.
        """

        if message.author.id == self._bot.application_id:
            return

        if message.channel.id not in self._history:
            self._history[message.channel.id] = []

        author: str = self._get_author(message)

        self._history[message.channel.id].append(
            ChatCompletionUserMessageParam(
                role="user",
                content=message.content,
                name=author,
            )
        )

        must_respond: bool = self._must_respond(message)

        if not must_respond and not self._is_active():
            return

        if must_respond:
            self._last_explicit_interaction_time = datetime.datetime.now()
            await self._bot.change_presence(status=discord.enums.Status.online)

        response: str | None = await self._send_message(message, must_respond)

        if response is not None:
            await message.reply(response)

    def _get_author(self, message: discord.Message) -> str:
        """Get the name of the author of the message.

        If the author has an assigned nickname for the guild, use it.

        Otherwise, return the display name for the author. The display name defaults to the username
        if it is not configured.

        Parameters
        ----------
        message : discord.Message
            The message that contains the author.

        Returns
        -------
        str
            The name of the author.
        """

        if hasattr(message.author, "nick") and message.author.nick:
            return message.author.nick

        return message.author.display_name

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

    async def _send_message(self, message: discord.Message, must_respond: bool) -> str | None:
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

        ai = cast(openai.AsyncOpenAI, config.ai)

        try:
            response: ChatCompletion = await ai.chat.completions.create(
                model=config.chatgpt_model,
                temperature=config.chatgpt_temperature,
                messages=self._get_chat_context(message, must_respond),
                user=message.author.name,
            )
        except openai.OpenAIError as e:
            log.error("An error occurred while querying the chat service.", exc_info=e)
            return None

        assistant_message: str | None = response.choices[0].message.content

        if not must_respond and assistant_message == self._NO_RESPONSE:
            return None

        if assistant_message is not None:
            self._history[message.channel.id].append(
                ChatCompletionAssistantMessageParam(
                    role="assistant",
                    content=message.content,
                )
            )

        return assistant_message


    def _get_chat_context(
        self,
        message: discord.Message,
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

        messages: list[ChatCompletionMessageParam] = self._history[
            message.channel.id
        ].copy()

        if not must_respond or config.chatgpt_system_message:
            messages.insert(
                0,
                ChatCompletionSystemMessageParam(
                    role="system",
                    content=(
                        f"{config.chatgpt_system_message or ""}\n"
                        f"{"" if must_respond else self._control_message}"
                    ),
                ),
            )

        return messages
