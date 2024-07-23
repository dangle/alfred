"""Contains objects and methods for creating and working with new bot features."""

from __future__ import annotations

import importlib
import inspect
import sys
import typing

import discord
import discord.ext.commands
import structlog

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import ModuleType
    from typing import Any


__all__ = (
    "CommandGroup",
    "Feature",
    "FeatureMetaclass",
    "FeatureRef",
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

#: An alias for 'discord.Cog.listener'.
listener = discord.Cog.listener


def command(*args: Any, **kwargs: Any) -> Callable[..., discord.ApplicationCommand]:
    """Wrap 'discord.ext.commands.command' and add an 'is_global' attribute to created commands.

    Returns
    -------
    Callable[..., discord.ApplicationCommand]
        The 'discord.ApplicationCommand' returned by 'discord.ext.commands.command'.

    """
    is_global: bool = kwargs.pop("is_global") if "is_global" in kwargs else False
    ac = discord.ext.commands.command(*args, **kwargs)
    ac.is_global = is_global  # type: ignore[attr-defined]
    return ac


class CommandGroup(discord.SlashCommandGroup):
    """A subclass of 'discord.SlashCommandGroup' that adds an 'is_global' attribute."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.is_global: bool = kwargs.pop("is_global") if "is_global" in kwargs else False
        super().__init__(*args, **kwargs)


class FeatureMetaclass(discord.CogMeta):
    """A metaclass that adds stores the feature name, bot, and guild_ids on a class."""

    def __new__(
        metacls: type[FeatureMetaclass],  # noqa: N804
        name: str,
        bases: tuple[type],
        classdict: dict[str, Any],
    ) -> FeatureMetaclass:
        """Create a new metaclass that enhances 'discord.cog.CogMeta'.

        Parameters
        ----------
        metacls : type[FeatureMetaclass]
            The type of the new class.
        name : str
            The name of the new class.
        bases : tuple[type]
            All base classes for the new class.
        classdict : dict[str, Any]
            Class attributes to add to the new class.

        Returns
        -------
        FeatureMetaclass
            The new class that was created.

        """
        classdict["__feature_name__"] = name
        cls: type = super().__new__(metacls, name, bases, classdict)

        #: Tell mypy that this is a new instance of 'FeatureMetaclass' and not an instance of
        #: whatever is returned by 'super().__new__'.
        return typing.cast(FeatureMetaclass, cls)

    def __call__(
        cls,  # noqa: N805
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


class Feature(discord.Cog, metaclass=FeatureMetaclass):
    """A 'discord.Cog' that stores the name and bot and injects 'guild_ids' into commands."""

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
                _log.error(f"{obj.__name__} is not a 'Feature'.")
                continue

            name: str = obj.name()

            if name in features:
                original_feature: str = (
                    f"{type(features[name].imported_module_name)}.{features[name].cls.__name__}"
                )
                new_feature: str = f"{ep.module}.{obj.__name__}"
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
