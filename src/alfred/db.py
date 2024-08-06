"""Models and helpers for accessing the database."""

from __future__ import annotations

import typing

from tortoise import fields
from tortoise.models import Model

if typing.TYPE_CHECKING:
    from tortoise.fields import (
        BooleanField,
        CharField,
        ForeignKeyRelation,
        ManyToManyRelation,
        TextField,
    )

__all__ = (
    "Feature",
    "Identity",
    "Staff",
    "Server",
    "ServerAlias",
)


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


class Staff(Model):
    """A bot to be deployed to Discord along with a global name and description."""

    #: A unique ID for the staff member.
    id: int = fields.BigIntField(primary_key=True)

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
    servers: ManyToManyRelation = fields.ManyToManyField("models.Server", related_name="staff")

    #: The list of features that this bot is allowed to use when connecting to Discord.
    features: ManyToManyRelation = fields.ManyToManyField("models.Feature", related_name="staff")

    def __str__(self) -> str:
        """Convert the display name of the 'Staff' to a string suitable for Discord."""
        return str(self.nick or self.name)[:32]

    def __repr__(self) -> str:
        """Return a Python representation of the 'Staff' object."""
        return (
            f"{self.__class__.__name__}("
            f"id={self.id!r}, "
            f"load_on_start={self.load_on_start!r}, "
            f"name={self.name!r}, "
            f"nick={self.nick!r}, "
            f"description={self.description!r}"
            ")"
        )

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

    def __str__(self) -> str:
        """Return the 'Feature' name."""
        return str(self.name)


class Server(Model):
    """A representation of a Discord server (aka guild)."""

    #: The unique ID of the Discord server.
    #: This is technically a Twitter "Snowflake" ID but it can be represented as a 64-bit integer.
    #: See: https://discord.com/developers/docs/reference#snowflakes
    id: int = fields.BigIntField(primary_key=True, generated=False)

    def __str__(self) -> str:
        """Return a string representation of the 'Server'."""
        return str(self.id)


class ServerAlias(Model):
    """A mapping of 'Staff' and 'Server' that contains information for a custom 'Identity'.

    'ServerAlias' stores the data for a custom, non-default, 'Identity' that can be configured for
    individual Discord servers.
    """

    #: A unique ID representing the mapping.
    id: int = fields.BigIntField(primary_key=True)

    #: The 'Staff' object using the alias.
    staff: ForeignKeyRelation = fields.ForeignKeyField("models.Staff", related_name="aliases")

    #: The 'Server' on which the 'Staff' is using this alias.
    server: ForeignKeyRelation = fields.ForeignKeyField("models.Server")

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
        return (
            f"{self.__class__.__name__}("
            f"id={self.id!r}, "
            f"name={self.name!r}, "
            f"nick={self.nick!r}, "
            f"description={self.description!r}"
            ")"
        )
