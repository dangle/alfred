"""Contains a class that acts as an ApplicationContext created from a message."""

from __future__ import annotations

import dataclasses
import typing

import discord
import discord.types.member
import discord.types.monetization
import discord.types.user
from discord.interactions import Interaction, WebhookMessage
from discord.types.interactions import Interaction as InteractionPayload
from discord.types.interactions import InteractionContextType

if typing.TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any, Literal

    from alfred.core import models

__all__ = (
    "MessageApplicationContext",
    "Response",
)

_MESSAGE_COMPONENT: Literal[3] = 3
_ENTITLEMENT: Literal[8] = 8


@dataclasses.dataclass
class Response:
    """An object containing the parameters available to `discord.ApplicationContext.respond`."""

    content: str | None = None
    embed: discord.Embed | None = None
    embeds: list[discord.Embed] | None = None
    view: discord.ui.View | None = None
    tts: bool = False
    ephemeral: bool = False
    allowed_mentions: discord.AllowedMentions | None = None
    file: discord.File | None = None
    files: list[discord.File] | None = None
    poll: discord.Poll | None = None
    delete_after: float | None = None

    def serializable(self) -> dict[str, Any]:
        """Return a version of the `Response` that can be serialized.

        Returns
        -------
        dict[str, Any]
            A dictionary containing serializable versions of the `Response` attributes.

        """
        data = vars(self).copy()

        file: discord.File | None = data.pop("file")
        if file:
            data["file"] = file.filename

        files: list[discord.File] | None = data.pop("files")
        if files:
            data["files"] = [f.filename for f in files]

        return data


class MessageApplicationContext(discord.ApplicationContext):
    """A subclass of `discord.ApplicationContext` that can be created from a `discord.Message`.

    Notes
    -----
    Most `discord.ApplicationContext` features are not supported because they require calling the
    `Interaction` object methods.

    Parameters
    ----------
    bot : db.Bot
        The bot handling the `discord.Message`.
    interaction : Interaction
        The `Interaction` to pass to the parent constructor.
        This will be faked since there is no active `Interaction`.
    message: discord.Message
        The `discord.Message` being used to create a new `discord.ApplicationContext`.

    """

    def __init__(
        self,
        bot: models.Staff,
        interaction: Interaction,
        message: discord.Message,
        *,
        delayed_send: bool = False,
    ) -> None:
        super().__init__(bot, interaction)
        self._responses: list[Response] = []
        self._delayed_send: bool = delayed_send
        self._message = message

    @classmethod
    async def new(
        cls,
        bot: models.Staff,
        message: discord.Message,
        *,
        delayed_send: bool = False,
    ) -> MessageApplicationContext:
        """Create a new `MessageApplicationContext` asynchronously.

        Parameters
        ----------
        bot : db.Bot
            The bot handling the `discord.Message`.
        message : discord.Message
            The `discord.Message` being used to create a new `discord.ApplicationContext`.
        delayed_send : bool, optional
            This will not send any responses sent through the context until it has been entered as a
            context manager if `True`.
            The default is `False`.

        Returns
        -------
        MessageApplicationContext
            A new instance of `MessageApplicationContext` made from the given `message`.

        """
        payload = InteractionPayload(
            id=message.id,
            application_id=bot.application_id or "",
            context=cls._get_interaction_context_type(message),
            type=_MESSAGE_COMPONENT,
            version=0,
            token="",
            entitlements=await cls._get_entitlements(message),
            authorizing_integration_owners=cls._get_authorizing_integration_owners(message),
            channel_id=message.channel.id,
            guild_locale=(
                message.guild.preferred_locale
                if message.guild and message.guild.preferred_locale
                else ""
            ),
        )

        if message.guild:
            payload["guild_id"] = message.guild.id

        payload["user"] = cls._get_user(bot, message.author)

        if isinstance(message.author, discord.Member):
            payload["member"] = cls._get_member(message.author)
            payload["member"]["user"] = payload["user"]

        return cls(
            bot,
            Interaction(data=payload, state=message._state),  # noqa: SLF001
            message,
            delayed_send=delayed_send,
        )

    @property
    def responses(self) -> list[Response]:
        """Return all responses sent through this context.

        Returns
        -------
        list[Response]
            A list of all responses sent through this context.

        """
        return self._responses

    @classmethod
    def _get_user(
        cls,
        bot: models.Staff,
        user: discord.User | discord.Member,
    ) -> discord.types.user.User:
        """Convert `user` to a format suitable for `Interaction`.

        If `user` is a `discord.Member` the user will be retrieved using the given `bot`.

        Parameters
        ----------
        bot : db.Bot
            The bot to use to retrieve a `discord.User` from a `discord.Member`.
        user : discord.User | discord.Member
            The message author.

        Returns
        -------
        discord.types.user.User
            A user suitable for an `Interaction`.

        """
        if isinstance(user, discord.Member):
            user = typing.cast(discord.User, bot.get_user(user.id))

        return discord.types.user.User(
            id=user.id,
            username=user.name,
            discriminator=user.discriminator,
            global_name=user.global_name,
            avatar=str(user.avatar) if user.avatar else None,
            public_flags=user.public_flags.value,
            system=user.system,
            bot=user.bot,
        )

    @classmethod
    def _get_member(cls, member: discord.Member) -> discord.types.member.Member:
        """Convert `member` to a format suitable for `Interaction`.

        Parameters
        ----------
        member : discord.Member
            The author of the `discord.Message`.

        Returns
        -------
        discord.types.member.Member
            A version of the author compatibile with `Interaction`.

        """
        return discord.types.member.Member(
            roles=list(member._roles),  # noqa: SLF001
            joined_at=str(
                (member.joined_at.timestamp() if member.joined_at else ""),
            ),
            deaf=bool(member.communication_disabled_until),
            mute=bool(member.communication_disabled_until),
            flags=member.flags.value,
            nick=member.nick or "",
            pending=member.pending,
        )

    @property
    def defer(self) -> Callable[..., Awaitable[None]]:
        """An override of `discord.ApplicationContext.defer` that returns a noop function.

        This is done because the `Interaction` cannot make API calls.
        """

        async def defer(**_: bool) -> None:
            pass

        return defer

    @property
    def respond(self) -> Callable[..., Awaitable[Interaction | WebhookMessage]]:
        """An override of `discord.ApplicationContext.respond` that can delay sending messages.

        If `delay_send` is `False` it will send messages immediately.

        This uses `discord.Message.reply` to send the messages.

        Returns
        -------
        Callable[..., Awaitable[Interaction | WebhookMessage]]
            The `Interaction` created from the original `discord.Message`.

        """

        async def respond(*args: Any, **kwargs: Any) -> Interaction | WebhookMessage:
            self._responses.append(Response(*args, **kwargs))

            if not self._delayed_send and self.interaction.channel_id:
                await self._message.reply(*args, **kwargs)

            return self.interaction

        return respond

    async def __aenter__(self) -> MessageApplicationContext:
        """Optionally delay context responses in this `MessageApplicationContext`.

        Returns
        -------
        MessageApplicationContext
            This `MessageApplicationContext`.

        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Reply with all responses if they were delayed."""
        if self._delayed_send:
            for response in self._responses:
                data = vars(response).copy()
                del data["ephemeral"]
                await self._message.reply(**data)

    @classmethod
    def _get_interaction_context_type(cls, message: discord.Message) -> InteractionContextType:
        """Get the interaction context type for `Interaction`.

        Parameters
        ----------
        message : discord.Message
            The message containing the channel used to send the message object.

        Returns
        -------
        InteractionContextType
            A `Literal[0, 1, 2]`.
                0 is a guild channel.
                1 is a direct message.
                2 is a private channel.

        """
        if isinstance(message.channel, discord.DMChannel):
            return discord.enums.InteractionContextType.bot_dm.value

        if hasattr(message.channel, "is_private") and message.channel.is_private():
            return discord.enums.InteractionContextType.private_channel.value

        return discord.enums.InteractionContextType.guild.value

    @classmethod
    def _get_authorizing_integration_owners(
        cls,
        message: discord.Message,
    ) -> dict[Literal["0", "1"], str | int]:
        """Get the mapping of authorizing integration owners for `Interaction`.

        Returns
        -------
        dict[Literal["0", "1"], str | int]
            A dictionary with two keys.
                Key "0" is the guild ID if available.
                Key "1" is the author ID.

        """
        return {
            "0": (
                message.author.guild.id
                if hasattr(message.author, "guild") and message.author.guild
                else 0
            ),
            "1": message.author.id,
        }

    @classmethod
    async def _get_entitlements(
        cls,
        message: discord.Message,
    ) -> list[discord.types.monetization.Entitlement]:
        """Convert a list of entitlements into a format for a `discord.Interaction`.

        Parameters
        ----------
        message : discord.Message
            The message will be used to check the entitlements of the author.

        Returns
        -------
        list[discord.types.monetization.Entitlement]
            A list of entitlements suitable for a `discord.Interaction`.

        """
        if isinstance(message.author, discord.User):
            return [
                discord.types.monetization.Entitlement(
                    id=e.id,
                    sku_id=e.sku_id,
                    application_id=e.application_id,
                    user_id=e.user_id,
                    type=_ENTITLEMENT,
                    deleted=e.deleted,
                    starts_at=str(e.starts_at.timestamp()),
                    ends_at=str(e.ends_at.timestamp()),
                    guild_id=e.guild_id,
                )
                async for e in message.author.entitlements()
            ]

        return []
