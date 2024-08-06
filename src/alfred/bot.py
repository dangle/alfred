"""Contains the main extensible 'Bot' class and helpers for running the event loop."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import time
import typing

import discord
import structlog

from alfred.typing import Presence

if typing.TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

__all__ = ("Bot",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class Bot(discord.Bot):
    """The main 'Bot' class used to deploy 'Staff' to Discord servers.

    Parameters
    ----------
    kwargs : dict[str, Any]
        Keyword arguments to be passed to the 'discord.Bot.__init__' method.

    """

    def __init__(self, **kwargs: Any) -> None:
        self._presence_map: collections.OrderedDict[float, Presence] = collections.OrderedDict()
        self._presence_lock = asyncio.Lock()

        super().__init__(**kwargs)

    @property
    def activities(self) -> set[discord.BaseActivity]:
        """Return a set containing all active bot activities.

        Returns
        -------
        set[discord.BaseActivity]
            A set of all active bot activities.

        """
        return {p.activity for p in self._presence_map.values() if p.activity}

    @property
    def current_presence(self) -> Presence:
        """The current 'Presence' of the bot.

        Returns
        -------
        Presence
            The current 'Presence' of the bot.

        """
        if not self._presence_map:
            return Presence()

        latest_presence_key: float = next(reversed(self._presence_map))
        return self._presence_map[latest_presence_key]

    @contextlib.asynccontextmanager
    async def presence(
        self,
        *,
        status: discord.Status | None = None,
        activity: discord.BaseActivity | None = None,
        ephemeral: bool = False,
    ) -> AsyncIterator:
        """Temporarily set the 'Presence' of the bot.

        Parameters
        ----------
        status : discord.Status | None, optional
            The 'discord.Status' of the 'Presence' to set.
        activity : discord.BaseActivity | None, optional
            The 'discord.BaseActivity' of the 'Presence' to set.
        ephemeral : bool, optional
            Whether or not to store the 'Presence' to be restored if it is replaced.
            By default all 'Presences' are stored for the duration of the context manager.

        Returns
        -------
        AsyncIterator
            The coroutine of the async context manager.

        """
        presence: Presence = Presence(status, activity)
        uid: int = hash(time.time()) ^ hash(presence)

        if not ephemeral:
            async with self._presences() as presences:
                presences[uid] = presence

        await _log.ainfo("Setting bot presence.", presence=presence)
        await self._bot.change_presence(**presence._asdict())

        try:
            yield
        finally:
            if not ephemeral:
                async with self._presences() as presences:
                    del presences[uid]

            await _log.ainfo("Setting presence.", presence=self.current_presence)
            await self._bot.change_presence(**self.current_presence._asdict())

    @contextlib.asynccontextmanager
    async def _presences(self) -> AsyncIterator:
        """Get the lock on the bot presence and return all 'Presences'.

        Returns
        -------
        AsyncIterator
            The coroutine of the context manager.

        Yields
        ------
        Iterator[AsyncIterator]
            The 'Presence' map used by the bot internally.

        """
        async with self._presence_lock:
            yield self._presence_map

    def get_user_name(self, user: discord.User | discord.Member) -> str:
        """Get the nickname or display name of the user or member.

        If the user has an assigned nickname for the guild, use it.

        Otherwise, return the display name for the user. The display name defaults to the username
        if it is not configured.

        Parameters
        ----------
        user : discord.User | discord.Member
            The 'discord.User' or 'discord.Member'.

        Returns
        -------
        str
            The name of the user.

        """
        return user.nick if hasattr(user, "nick") and user.nick else user.display_name

    async def on_application_command_error(
        self,
        _: discord.ApplicationContext,
        exception: discord.DiscordException,
    ) -> None:
        """Catch all application command errors and log them.

        Parameters
        ----------
        _ : discord.ApplicationContext
            The context for the current command.
        exception : discord.DiscordException
            The exception raised from the application command.

        """
        await _log.aerror(
            "An exception occurred while running an application command.",
            exc_info=exception,
        )
