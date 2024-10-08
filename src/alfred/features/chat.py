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
from async_lru import alru_cache as async_cache
from discord import Message

from alfred.chat import ChatClient
from alfred.core import feature, models
from alfred.util.translation import gettext as _

__all__ = ("Chat",)


#: The maximum length of a Discord message.
_MAX_REPLY_LEN: int = 2000

#: How long to listen for corrections from the user without requiring an explicit mention, in
#: seconds.
_TIME_TO_WAIT_FOR_CORRECTIONS_S: int = 60

WAITING_FOR_CORRECTIONS = discord.CustomActivity(_("Waiting for corrections"))

THINKING = discord.CustomActivity(_("Thinking"))


class Chat(feature.Feature):
    """Manages chat interactions and commands in the bot."""

    #: The bot to which this feature was attached.
    staff: models.Staff

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
        self._client = ChatClient(self.staff)

    @feature.listener("on_ready", once=True)
    async def set_server_profiles(self) -> None:
        """Set the nickname for the bot in each guild on ready."""
        for guild in self.staff.guilds:
            identity = await self.staff.get_identity(guild.id)
            nick: str = str(identity)

            if nick and self.staff.application_id is not None:
                member: discord.Member | None = guild.get_member(self.staff.application_id)

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
        waiting_for_corrections: bool = WAITING_FOR_CORRECTIONS in self.staff.activities

        async with self.staff.presence(activity=THINKING):
            response: str | None = await self._client.update(
                message,
                must_respond=must_respond,
                allow_implicit=waiting_for_corrections,
            )

            match response:
                case str():
                    for chunk in (
                        response[i : i + _MAX_REPLY_LEN]
                        for i in range(0, len(response), _MAX_REPLY_LEN)
                    ):
                        await message.reply(chunk)
                case None:
                    return

        if must_respond:
            self.staff.dispatch("waiting_for_corrections")

    @feature.listener("on_waiting_for_corrections")
    async def wait_for_corrections(self) -> None:
        """Listen for the `on_waiting_for_corrections` event and set the bot activity."""
        async with self.staff.presence(activity=WAITING_FOR_CORRECTIONS):
            await asyncio.sleep(_TIME_TO_WAIT_FOR_CORRECTIONS_S)

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

        if any(m.id == self.staff.application_id for m in message.mentions):
            return True

        name: str = str(await self.staff.get_identity(message.channel.id)).lower()

        return name in message.content.lower()
