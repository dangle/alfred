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

import discord
import structlog
from async_lru import alru_cache as async_cache
from discord import Message

from alfred import bot, db, feature
from alfred.features.chat.activities import THINKING, WAITING_FOR_CORRECTIONS
from alfred.features.chat.client import ChatClient
from alfred.features.chat.constants import MAX_REPLY_LEN, TIME_TO_WAIT_FOR_CORRECTIONS_S

__all__ = ("Chat",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class Chat(feature.Feature):
    """Manages chat interactions and commands in the bot."""

    #: The bot to which this feature was attached.
    bot: bot.Bot

    #: The staff member this bot represents.
    staff: db.Staff

    #: The intents required by this feature.
    #: This requires the privileged intents in order to get access to server chat.
    intents: discord.Intents = discord.Intents(
        guilds=True,
        presences=True,
        members=True,
        messages=True,
        message_content=True,
    )

    @feature.listener("on_ready", once=True)
    async def init(self) -> None:
        """Create the 'ChatClient' instance."""
        self._client = ChatClient(self.staff, self.bot)

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
        must_respond: bool = await self._must_respond(message)
        waiting_for_corrections: bool = WAITING_FOR_CORRECTIONS in self.bot.activities

        async with self.bot.presence(activity=THINKING):
            response: str | None = await self._client.update(
                message,
                must_respond=must_respond,
                allow_implicit=waiting_for_corrections,
            )

            match response:
                case str():
                    for chunk in (
                        response[i : i + MAX_REPLY_LEN]
                        for i in range(0, len(response), MAX_REPLY_LEN)
                    ):
                        await message.reply(chunk)
                case None:
                    return

        if must_respond:
            self.bot.dispatch("waiting_for_corrections")

    @feature.listener("on_waiting_for_corrections")
    async def wait_for_corrections(self) -> None:
        """Listen for the `on_waiting_for_corrections` event and set the bot activity."""
        async with self.bot.presence(activity=WAITING_FOR_CORRECTIONS):
            await asyncio.sleep(TIME_TO_WAIT_FOR_CORRECTIONS_S)

    @async_cache
    async def _must_respond(self, message: Message) -> bool:
        """Determine if the bot *must* respond to the given `message`.

        The bot *must* respond to messages:
        1. that are private messages to the bot.
        2. where the name of the bot is mentioned in the `message.content`.
        3. where the bot is in the explicit mentions of the `message`.

        Parameters
        ----------
        message : Message
            The message to which the bot may reply.

        Returns
        -------
        bool
            True if the bot *must* respond to the message.
            False if the bot *may* respond to the message.

        """
        if isinstance(message, discord.DMChannel):
            return True

        if any(m.id == self.bot.application_id for m in message.mentions):
            return True

        name: str = str(await self.staff.get_identity(message.channel.id)).lower()

        return name in message.content.lower()
