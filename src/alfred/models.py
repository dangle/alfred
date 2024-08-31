"""Models and helpers for accessing the database."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import time
import typing

import discord
import structlog
from tortoise import fields
from tortoise.models import Model, ModelMeta

from alfred.logging import Canonical, canonical
from alfred.typing import Presence, ProtocolMeta

if typing.TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

    from tortoise.fields import (
        BooleanField,
        CharField,
        ForeignKeyRelation,
        ManyToManyRelation,
        TextField,
        UUIDField,
    )


__all__ = (
    "Feature",
    "Identity",
    "Staff",
    "StaffConfig",
    "DiscordServer",
    "DiscordServerAlias",
)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

#: The maximum length of 'description' when displayed as part of '__repr__'.
_DESC_REPR_LEN: int = 50


class _ProtocolModelMeta(ModelMeta, ProtocolMeta):
    """A metaclass to allow 'Model' objects to be used with protocols."""


class _ProtocolModel(Model, metaclass=_ProtocolModelMeta):
    """A base class for 'Model' objects that allows them to be used alongside protocols."""


class Identity(typing.NamedTuple):
    """A name, nick, and description of a staff member."""

    #: The name the bot will respond to in Discord.
    name: str

    #: The description that the bot will use as a system message when connecting to chat services.
    description: str

    #: The display name the bot will use in Discord servers.
    nick: str | None = None

    def __str__(self) -> str:
        """Convert the display name of the 'Identity' to a string suitable for Discord."""
        return str(self.nick or self.name)[:32]


class StaffConfig(_ProtocolModel, Canonical):
    """A bot to be deployed to Discord along with a global name and description."""

    #: A unique ID for the staff member.
    id: UUIDField = fields.UUIDField(primary_key=True)

    #: The Discord token this bot will use to connect to Discord.
    discord_token: CharField = fields.CharField(max_length=100, unique=True)

    #: Whether or not to connect the bot to Discord as soon as the application starts.
    load_on_start: BooleanField = fields.BooleanField(default=True)

    #: The name the bot will respond to in Discord.
    name: CharField = fields.CharField(max_length=32)

    #: The display name the bot will use in Discord servers.
    nick: CharField = fields.CharField(max_length=32, null=True)

    #: The description that the bot will use as a system message when connecting to chat services.
    description: TextField = fields.TextField()

    #: The list of servers on which the bot commands should be available.
    #: If this is empty, the commands will available on all servers.
    servers: ManyToManyRelation = fields.ManyToManyField(
        "models.DiscordServer",
        related_name="staff",
    )

    #: The list of features that this bot is allowed to use when connecting to Discord.
    features: ManyToManyRelation = fields.ManyToManyField("models.Feature", related_name="staff")

    class Meta:
        """Database metadata for this model."""

        table: str = "staff"

    def __str__(self) -> str:
        """Convert the display name of the 'Staff' to a string suitable for Discord."""
        return str(self.nick or self.name)[:32]

    def __repr__(self) -> str:
        """Return a Python representation of the 'Staff' object."""
        desc: str = (
            self.description
            if len(self.description) < _DESC_REPR_LEN
            else f"{self.description[:_DESC_REPR_LEN - 3]}..."
        )

        return (
            f"Staff.Settings("
            f"id='{self.id}', "
            f"load_on_start={self.load_on_start!r}, "
            f"name={self.name!r}, "
            f"nick={self.nick!r}, "
            f"description={desc!r}"
            ")"
        )

    @typing.override
    @property
    def __canonical__(self) -> dict[str, Any]:
        desc: str = (
            self.description
            if len(self.description) < _DESC_REPR_LEN
            else f"{self.description[:_DESC_REPR_LEN - 3]}..."
        )

        return {
            "id": str(self.id),
            "load_on_start": self.load_on_start,
            "name": self.name,
            "nick": self.nick,
            "description": desc,
        }

    async def get_identity(self, server_id: int | None = None) -> Identity:
        """Get the 'Identity' the bot will use on the given 'server_id'.

        Parameters
        ----------
        server_id : int | None, optional
            The server ID for which to retrieve the 'Identity', by default None.

        Returns
        -------
        Identity
            A combination of name, nick, and description that a bot will use on a specific Discord
            server.

        """
        if server_id is not None:
            async for alias in self.aliases:  # type: ignore[attr-defined]
                if alias.server.id == server_id:
                    return Identity(name=alias.name, nick=alias.nick, description=alias.description)

        return Identity(name=self.name, nick=self.nick, description=self.description)


class Feature(Model):
    """Represents 'Feature' objects that a bot can be permitted to use on a Discord server."""

    #: The name of the 'Feature'.
    name: CharField = fields.CharField(primary_key=True, max_length=255)

    def __repr__(self) -> str:
        """Return the 'Feature' name."""
        return str(self.name)


class DiscordServer(Model):
    """A representation of a Discord server (aka guild)."""

    #: The unique ID of the Discord server.
    #: This is technically a Twitter "Snowflake" ID but it can be represented as a 64-bit integer.
    #: See: https://discord.com/developers/docs/reference#snowflakes
    id: int = fields.BigIntField(primary_key=True, generated=False)

    def __repr__(self) -> str:
        """Return a string representation of the 'Server'."""
        return str(self.id)


class DiscordServerAlias(_ProtocolModel, Canonical):
    """A mapping of 'Staff' and 'Server' that contains information for a custom 'Identity'.

    'ServerAlias' stores the data for a custom, non-default, 'Identity' that can be configured for
    individual Discord servers.
    """

    #: A unique ID representing the mapping.
    id: UUIDField = fields.UUIDField(primary_key=True)

    #: The 'Staff' object using the alias.
    staff: ForeignKeyRelation = fields.ForeignKeyField("models.StaffConfig", related_name="aliases")

    #: The 'Server' on which the 'Staff' is using this alias.
    server: ForeignKeyRelation = fields.ForeignKeyField("models.DiscordServer")

    #: The name the bot will respond to in Discord.
    name: CharField = fields.CharField(max_length=32)

    #: The display name the bot will use in Discord servers.
    nick: CharField = fields.CharField(max_length=32, null=True)

    #: The description that the bot will use as a system message when connecting to chat services.
    description: TextField = fields.TextField()

    class Meta:
        """Database metadata for this model."""

        #: The staff ID and server ID must be unique pairs to prevent a staff member from having
        #: multiple identities per server.
        unique_together = ("staff", "server")

    def __str__(self) -> str:
        """Convert the display name of the 'Identity' to a string suitable for Discord."""
        return str(self.nick or self.name)

    def __repr__(self) -> str:
        """Return a Python representation of the 'ServerAlias' object."""
        desc: str = (
            self.description
            if len(self.description) < _DESC_REPR_LEN
            else f"{self.description[:_DESC_REPR_LEN - 3]}..."
        )

        return (
            f"{self.__class__.__qualname__}("
            f"id={self.id!r}, "
            f"name={self.name!r}, "
            f"nick={self.nick!r}, "
            f"description={desc!r}"
            ")"
        )

    @typing.override
    @property
    def __canonical__(self) -> dict[str, Any]:
        desc: str = (
            self.description
            if len(self.description) < _DESC_REPR_LEN
            else f"{self.description[:_DESC_REPR_LEN - 3]}..."
        )

        return {
            "id": str(self.id),
            "name": self.name,
            "nick": self.nick,
            "description": desc,
        }


class Staff(discord.Bot, Canonical):
    """The main 'Bot' class used to deploy 'Staff' to Discord servers.

    Parameters
    ----------
    kwargs : dict[str, Any]
        Keyword arguments to be passed to the 'discord.Bot.__init__' method.

    """

    Config: typing.Final[type[StaffConfig]] = StaffConfig

    def __init__(self, /, conf: StaffConfig, **kwargs: Any) -> None:
        self._config: StaffConfig = conf
        self._presence_map: collections.OrderedDict[float, Presence] = collections.OrderedDict()
        self._presence_lock = asyncio.Lock()

        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Get a Python representation of this object.

        Returns
        -------
        str
            A Python representation of this object.

        """
        return (
            f"{type(self).__qualname__}("
            f"owner_ids={(self.owner_ids or {self.owner_id})!r}, "
            f"presence={self.current_presence!r}, "
            f"is_ready={self.is_ready()!r}, "
            f"cogs={self.cogs!r}, "
            f"settings={self._config!r}"
            ")"
        )

    @typing.override
    @property
    def __canonical__(self) -> dict[str, Any]:
        return {
            "owner_ids": list(self.owner_ids) or [self.owner_id],
            "presence": self.current_presence,
            "is_ready": self.is_ready(),
        } | canonical(self._config)

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

        structlog.contextvars.bind_contextvars(presence=presence)
        await self._bot.change_presence(**presence._asdict())

        try:
            yield
        finally:
            if not ephemeral:
                async with self._presences() as presences:
                    del presences[uid]

            structlog.contextvars.bind_contextvars(presence=self.current_presence)
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

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        """Log any unhandled errors.

        Parameters
        ----------
        event_method : str
            The name of the event that raised the exception.
        args : tuple[Any, ...]
            Positional arguments sent to 'event_method'.
        kwargs : dict[str, Any]
            Keyword arguments sent to 'event_method'.

        """
        try:
            await _log.aexception(f"Ignoring exception in {event_method}.", *args, **kwargs)
        except Exception:
            await _log.aexception(f"Ignoring exception in {event_method}.")

    async def on_application_command_error(
        self,
        context: discord.ApplicationContext,
        exception: discord.DiscordException,
    ) -> None:
        """Log any unhandled application command errors.

        Parameters
        ----------
        context : discord.ApplicationContext
            The context for the current command.
        exception : discord.DiscordException
            The exception raised from the application command.

        """
        if self._event_handlers.get("on_application_command_error", None):
            return

        if (command := context.command) and command.has_error_handler():
            return

        if (cog := context.cog) and cog.has_error_handler():
            return

        await _log.aerror(
            "An exception occurred while running an application command.",
            exc_info=exception,
        )

    def __getattr__(self, key: str) -> Any:
        """Get unknown attributes from the database.

        Parameters
        ----------
        key : str
            The unknown attribute being retrieved.

        Returns
        -------
        Any
            The value of the unknown attribute from the database.

        Raises
        ------
        AttributeError
            Raised if the attribute is not found in the database.

        """
        return getattr(self._config, key)
