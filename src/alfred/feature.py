"""Contains objects and methods for creating and working with new bot features."""

from __future__ import annotations

import abc
import importlib
import inspect
import sys
import typing
from collections.abc import Callable, Iterable
from typing import Any, Concatenate

import discord
import discord.ext.commands
import structlog
from discord import ApplicationContext, Cog
from discord.ext.commands import Command, Context
from discord.utils import MISSING

if typing.TYPE_CHECKING:
    from types import ModuleType

    from discord.ext.commands._types import Coro

from alfred.autofields import AutoFields
from alfred.logging import Canonical, canonical_event, register_canonical_type

__all__ = (
    "ENTRY_POINT_GROUP",
    "CommandGroup",
    "Feature",
    "FeatureMeta",
    "FeatureRef",
    "SlashCommand",
    "command",
    "discover_features",
    "isfeatureclass",
    "get_intents",
    "name",
    "listener",
)

#: The group name that all new features must belong to in order to be discovered
ENTRY_POINT_GROUP: str = "alfred.features"

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


#: Necessary to make metaclasses usable with protocols.
_ProtocolMeta: type = abc.ABCMeta if typing.TYPE_CHECKING else type(typing.Protocol)


class FeatureMeta(discord.CogMeta, _ProtocolMeta, type):
    """A metaclass that adds stores the feature name, bot, and guild_ids on a class."""

    def __call__(
        cls,
        *args: Any,
        **kwargs: Any,
    ) -> Feature:
        """Return a new instance of the type attached to 'bot'.

        If 'guild_ids' is given then any commands that do not already have 'guild_ids' set and are
        not marked as 'is_global' will be assigned 'guild_ids'.

        Parameters
        ----------
        args : Any
            Positional arguments to pass to the 'Feature' '__init__'.
        kwargs : Any
            Keyword arguments to pass to the 'Feature' '__init__'.

        Returns
        -------
        Any
            A new instance of the 'Feature' that has been attached to 'bot'.

        """
        extra_prefix: str = "extra__"
        prefix_len: int = len(extra_prefix)
        extras: dict[str, Any] = {
            k[prefix_len:]: kwargs.pop(k)
            for k in tuple(kwargs.keys())
            if k.startswith(extra_prefix)
        }

        self = super().__call__(*args, **kwargs)
        self._Feature__extras = extras
        self.__feature_name__ = cls.__name__

        if guild_ids := extras.get("guild_ids"):
            for cmd in self.get_commands():
                if (
                    not getattr(cmd, "is_global", False)
                    and not cmd.guild_ids
                    and not cmd.contexts
                    and not cmd.integration_types
                ):
                    cmd.guild_ids = guild_ids

        if bot := extras.get("bot"):
            bot.add_cog(self)

        return self


class Feature(Cog, Canonical, AutoFields, metaclass=FeatureMeta):
    """A 'Cog' that stores the name and bot and injects 'guild_ids' into commands."""

    __feature_name__: str
    _Feature__extras: dict[str, Any]

    intents: discord.Intents = discord.Intents.none()

    @classmethod
    def name(cls) -> str:
        """Return the name of the 'Feature'.

        Returns
        -------
        str
            The name of the 'Feature'.

        """
        return getattr(cls, "__feature_name__", None) or cls.__name__

    def __str__(self) -> str:
        """Return the name of the 'Feature'.

        Returns
        -------
        str
            The name of the 'Feature'.

        """
        return self.name()

    def __repr__(self) -> str:
        """Return a Python representation of the 'Feature'.

        Returns
        -------
        str
            A string of the Python representation of the 'Feature'.

        """
        return (
            f"{self.__class__.__qualname__}("
            f"name='{self.name()!r}', "
            f"extras={self._Feature__extras!r}"
            ")"
        )

    @typing.override
    @property
    def __canonical__(self) -> dict[str, Any]:
        loggable: dict[str, Any] = {
            "feature": self.name(),
        }
        loggable.update(
            {
                k: (v.__canonical__ if isinstance(v, Canonical) else v)
                for k, v in self._Feature__extras.items()
            },
        )
        return loggable


class FeatureRef(typing.NamedTuple):
    """A class for storing a 'Feature' with the name under which it was imported."""

    #: The 'Feature' subclass.
    cls: type[Feature]

    #: The name of the module from which the 'Feature' was imported.
    imported_module_name: str


def name(name: str) -> Callable[[type[Feature]], type[Feature]]:
    """Update the feature name of a 'Feature' class.

    Parameters
    ----------
    name : str
        The new name of the 'Feature'.

    Returns
    -------
    Callable[[str], type[Feature]]
        A decorator that will update the feature name of the wrapped 'Feature' class.

    """

    def decorator(cls: type[Feature]) -> type[Feature]:
        """Update the feature name of a 'Feature' class.

        Parameters
        ----------
        cls : type[Feature]
            The 'Feature' for which to change the name.

        Returns
        -------
        type[Feature]
            The decorated class.

        """
        cls.__feature_name__ = name  # type: ignore[attr-defined]
        return cls

    return decorator


def isfeatureclass(obj: Any) -> bool:
    """Return True if 'obj' is a subclass of 'Feature'.

    Parameters
    ----------
    obj : Any
        The object to check.

    Returns
    -------
    bool
        True if the object is a subclass of 'Feature'.

    """
    return inspect.isclass(obj) and issubclass(obj, Feature)


def discover_features() -> dict[str, FeatureRef]:
    """Find and load new bot extension modules.

    Returns
    -------
    dict[str, FeatureRef]
        A mapping of 'Feature' names to 'FeatureRef' objects containing the 'Feature' class and the
        name of the module from which it was imported.

    """
    features: dict[str, FeatureRef] = {}

    _log.info("Looking for new features.")

    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            module: ModuleType = (
                importlib.reload(sys.modules[ep.module]) if ep.module in sys.modules else ep.load()
            )
            objects: Iterable = (
                (module,)
                if isfeatureclass(module)
                else (obj for _, obj in inspect.getmembers(module, isfeatureclass))
            )
        except Exception as e:
            _log.error(
                f"An exception occurred while loading 'Feature' module: '{ep.module}'",
                exc_info=e,
            )
            continue

        for obj in objects:
            if not isfeatureclass(obj):
                _log.error(f"{obj.__qualname__} is not a 'Feature'.")
                continue

            name: str = obj.name()

            if name in features:
                original_feature: str = (
                    f"{type(features[name].imported_module_name)}.{features[name].cls.__qualname__}"
                )
                new_feature: str = f"{ep.module}.{obj.__qualname__}"
                _log.warning(
                    f"Found duplicate 'Feature' with name '{name}'.",
                    name=name,
                    original=original_feature,
                    overwriting=new_feature,
                )

            features[name] = FeatureRef(obj, ep.module)
            _log.info(f"Found feature: '{name}'.")

    _log.info("Done looking for new features.")

    return features


def get_intents(*features: type[Feature]) -> discord.Intents:
    """Get the combination of all required intents from the given 'Feature' objects.

    Returns
    -------
    discord.Intents
        The 'discord.Intents' required to use all of the given 'Feature' objects.

    """
    intents: discord.Intents = discord.Intents.none()

    for feature in features:
        intents = intents | feature.intents

    return intents


class SlashCommand[
    CogT: Cog, **P,
    T,
](discord.SlashCommand):
    """A 'discord.SlashCommand' that adds a canonical logging wrapper to the command."""

    @discord.SlashCommand.callback.setter  # type: ignore[attr-defined]
    def callback(
        self,
        function: (
            Callable[Concatenate[CogT, ApplicationContext, P], Coro[T]]
            | Callable[Concatenate[ApplicationContext, P], Coro[T]]
        ),
    ) -> None:
        """Set the callback function for the 'SlashCommand' and wrap it in a canonical logger.

        Parameters
        ----------
        function : Callable[..., Coro[T]]
            A command function to wrap with a canonical logger.

        """
        log_decorator = canonical_event(command=self.qualified_name)
        discord.SlashCommand.callback.fset(  # type: ignore[attr-defined]
            self,
            log_decorator(function),
        )


class CommandGroup(discord.SlashCommandGroup):
    """A subclass of 'discord.SlashCommandGroup' that creates 'SlashCommand' objects."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.is_global: bool = kwargs.pop("is_global", False)
        super().__init__(*args, **kwargs)

    def command[  # type: ignore[override]
        T: discord.SlashCommand
    ](self, **kwargs: Any) -> Callable[[Callable], discord.SlashCommand]:
        """Create a new subgroup command from the given function.

        Returns
        -------
        Callable[[Callable], discord.SlashCommand]
            A decorator that will convert a function into a subcommand of this group.

        """
        return super().command(SlashCommand, **kwargs)


def command[
    ContextT: Context,
    CommandT: Command,
    CogT: Cog, **P,
    T,
](name: str = MISSING, **attrs: Any) -> Callable[
    [
        (
            Callable[Concatenate[ContextT, P], Coro[Any]]
            | Callable[Concatenate[CogT, ContextT, P], Coro[T]]
        ),
    ],
    Command[CogT, P, T] | CommandT,
]:
    """Create a new slash command from the given function.

    Returns
    -------
    Callable[..., Callable[..., Any]]
        A decorator that will convert the given function into a SlashCommand.

    """
    return discord.ext.commands.command(name, cls=SlashCommand, **attrs)  # type: ignore[arg-type]


def listener[
    FuncT: Callable[..., Any]
](name: str = MISSING, *, once: bool = False) -> Callable[[FuncT], FuncT]:
    """Create a new listener from the given function.

    Returns
    -------
    Callable[[FuncT], FuncT]
        A decorator that will convert the given function into a new listener with canonical logging.

    """
    cog_decorator: Callable[[FuncT], FuncT] = Cog.listener(name, once)

    def decorator(func: FuncT) -> FuncT:
        log_decorator = canonical_event(
            listener={
                "event": name if name is not MISSING else func.__qualname__,
                "listener": func.__qualname__,
            },
        )
        return typing.cast(FuncT, cog_decorator(log_decorator(func)))

    return decorator


def _get_canonical_message(message: discord.Message) -> dict[str, Any]:
    """Get a dict object from a 'discord.Message' to use in logging.

    Parameters
    ----------
    message : discord.Message
        A 'discord.Message' object to be logged.

    Returns
    -------
    dict[str, Any]
        A dict representation of a 'discord.Message' to use in logging.

    """
    loggable: dict[str, Any] = {
        "id": message.id,
        "author": {
            "name": message.author.name,
            "bot": message.author.bot,
        },
        "channel": {
            "id": message.channel.id,
        },
    }

    match channel := message.channel:
        case discord.TextChannel(guild=discord.Guild()):
            loggable["channel"].update(
                {
                    "server": {
                        "id": channel.guild.id,
                        "name": channel.guild.name,
                    },
                    "nsfw": channel.nsfw,
                    "news": channel.news,
                    "type": "text",
                },
            )
        case discord.Thread():
            loggable["channel"].update(
                {
                    "parent_id": channel.parent_id,
                    "server": {
                        "id": channel.guild.id,
                        "name": channel.guild.name,
                    },
                    "type": "thread",
                },
            )

            if (
                parent_channel := next(
                    (
                        typing.cast(discord.TextChannel, c)
                        for c in channel.guild.channels
                        if c.id == channel.parent_id
                    ),
                    None,
                )
            ) is not None:
                loggable["channel"].update(
                    {
                        "nsfw": parent_channel.nsfw,
                        "news": parent_channel.news,
                    },
                )
        case discord.DMChannel(recipient=discord.User()) if isinstance(
            channel.recipient,
            discord.User,
        ):
            loggable["channel"].update(
                {
                    "type": "dm",
                    "recipients": [
                        {
                            "id": channel.recipient.id,
                            "name": channel.recipient.name,
                        },
                    ],
                },
            )
        case discord.DMChannel(recipient=None):
            loggable["channel"]["type"] = "dm"
        case discord.GroupChannel():
            loggable["channel"].update(
                {
                    "type": "group",
                    "recipients": [{"id": r.id, "name": r.name} for r in channel.recipients],
                },
            )

    return {"channel_message": loggable}


register_canonical_type(discord.Message, _get_canonical_message)
register_canonical_type(discord.ApplicationContext, lambda _: {})
