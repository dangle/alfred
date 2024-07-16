"""Contains the main extensible `Bot` class and helpers for running the event loop."""

import importlib
import sys
from collections.abc import Iterable
from typing import Any

import discord
import structlog

from .config import CommandLineFlag, EnvironmentVariable, config, csv
from .exceptions import FeatureNotFoundError
from .features import features as features_
from .translation import gettext as _

__all__ = (
    "Bot",
    "run",
)

log: structlog.stdlib.BoundLogger = structlog.get_logger()

config(  # The token used by the bot to authenticate with Discord.
    "discord_token",
    env="DISCORD_TOKEN",
    flag=CommandLineFlag(
        "--discord-token",
        help=_(
            "The Discord authentication token for {project_name}.",
        ).format(project_name=config.bot_name),
    ),
    required=True,
)
config(
    "guild_ids",
    env=EnvironmentVariable(  # A comma-separated list of server IDs for commands.
        name="DISCORD_GUILD_IDS",
        type=lambda x: set(csv(x)),
    ),
    flag=CommandLineFlag(
        name="--guild-ids",
        nargs="*",
        short="-g",
        help=_(
            "The guild (server) IDs of servers on which to enable {project_name} commands.\n"
            "If no guild IDs are given, commands will be registered as global and will take up to"
            " an hour to become usable.",
        ).format(project_name=config.bot_name),
    ),
    required=False,
)
config(
    "bot_enabled_features",
    env=EnvironmentVariable(  # A comma-separated list of features to enable at bot creation.
        name="ALFRED_ENABLED_FEATURES",
        type=csv,
    ),
    flag=CommandLineFlag(
        name="--enabled-features",
        nargs="*",
        short="-f",
        help=_(
            "{project_name} features to enable.\n"
            "If no features are given, all features will be enabled by default.",
        ).format(project_name=config.bot_name),
        metavar="FEATURES",
    ),
    required=False,
)
config(
    "disable_admin_commands",
    flag=CommandLineFlag(
        name="--disable-admin-commands",
        action="store_true",
        help=_("Disable the Discord commands for administrating {project_name}.").format(
            project_name=config.bot_name,
        ),
    ),
)


class Bot(discord.Bot):
    """The main `Bot` class used for managing bot extension modules.

    Parameters
    ----------
    features : Iterable[str] | None, optional
        The list of features to enable when the bot first starts up.
        If not supplied all found features will be enabled.
    disable_admin_commands : bool, optional
        Whether or not to disable the admin commands.
        Defaults to `False`
    intents : discord.Intents | None, optional
        If this is not supplied the bot will create the `discord.Intents` object using the sum of
        the `__intents__` attributes from all of the features.
    kwargs : dict[str, Any]
        Keyword arguments to be passed to the `discord.Bot.__init__` method.

    """

    def __init__(
        self,
        *,
        features: Iterable[str] | None = None,
        disable_admin_commands: bool = False,
        **kwargs: Any,
    ) -> None:
        from .features.admin import __feature__

        features_to_enable: set[str] = set(
            features if features is not None else features_.all_features,
        ) | (set() if disable_admin_commands else {__feature__})

        for feature in features_to_enable:
            if feature not in features_.all_features:
                raise FeatureNotFoundError(feature=feature)

        feature_modules: set[str] = {
            features_.get_module_name_by_feature(feature) for feature in features_to_enable
        }

        if "intents" not in kwargs:
            kwargs["intents"] = self._get_intents_from_features(feature_modules)

        super().__init__(**kwargs)

        log.info(f"Loading bot extension modules: {", ".join(sorted(feature_modules))}")

        try:
            with config.readonly:
                self.load_extensions(*feature_modules)
        finally:
            log.info("Done loading bot extension modules.")

    def _get_intents_from_features(self, feature_modules: Iterable[str]) -> discord.Intents:
        """Get the `discord.Intents` necessary to run all enabled features.

        Parameters
        ----------
        feature_modules : Iterable[str]
            The names of the feature modules that will be loaded.

        Returns
        -------
        discord.Intents
            A `discord.Intents` value that containts the total of all of the `__intents__` from each
            feature ORed together.

        """
        intents: discord.Intents = discord.Intents.none()

        for module_name in feature_modules:
            module = sys.modules[module_name]

            if hasattr(module, "__intents__") and isinstance(module.__intents__, discord.Intents):
                intents = intents | module.__intents__

        return intents

    @property
    def enabled_features(self) -> set[str]:
        """Return the set of all enabled features.

        Returns
        -------
        set[str]
            The set of all enabled features.

        """
        enabled_features: set[str] = {
            features_.get_feature_by_module_name(module_name) for module_name in self.extensions
        }
        log.debug(f"Enabled features: {", ".join(enabled_features)}")
        return enabled_features

    @property
    def disabled_features(self) -> set[str]:
        """Return the set of all disabled features.

        Returns
        -------
        set[str]
            The set of all disabled features.

        """
        disabled_features: set[str] = features_.all_features.difference(self.enabled_features)
        log.debug(f"Disabled features: {", ".join(disabled_features)}")
        return disabled_features

    async def enable_feature(self, feature: str) -> None:
        """Enable the requested feature.

        Parameters
        ----------
        feature : str
            The name of the feature to enable.

        Raises
        ------
        FeatureNotFoundException
            Raised when the requested feature cannot be found to be enabled.

        """
        if feature in self.enabled_features:
            await log.adebug(f"Feature already enabled: {feature}", feature=feature)
            return

        if feature not in features_.all_features:
            raise FeatureNotFoundError(feature)

        module_name: str = features_.get_module_name_by_feature(feature)
        await log.ainfo(
            f"Enabling feature: {feature}",
            feature=feature,
            bot_extension_module=module_name,
        )

        with config.readonly:
            self.load_extension(module_name)

    async def disable_feature(self, feature: str) -> None:
        """Disable the requested feature.

        Parameters
        ----------
        feature : str
            The name of the feature to disable.

        Raises
        ------
        FeatureNotFoundException
            Raised when the requested feature cannot be found to be disabled.

        Notes
        -----
        This does not *actually* work because of a bug in pycord.
        See: https://github.com/Pycord-Development/pycord/issues/2015

        """
        if feature in self.disabled_features:
            await log.adebug(f"Feature already disabled: {feature}", feature=feature)
            return

        if feature not in features_.all_features:
            raise FeatureNotFoundError(feature)

        module_name: str = features_.get_module_name_by_feature(feature)
        await log.ainfo(
            f"Disabling feature: {feature}",
            feature=feature,
            bot_extension_module=module_name,
        )
        self.unload_extension(module_name)

    async def reload_feature(self, feature: str) -> None:
        """Reload the requested feature.

        Parameters
        ----------
        feature : str
            The name of the feature to reload.

        Raises
        ------
        FeatureNotFoundException
            Raised when the requested feature is not currently enabled and cannot be found to be
            enabled.

        """
        if feature in self.enabled_features:
            module_name: str = features_.get_module_name_by_feature(feature)
            await log.ainfo(
                f"Reloading feature: {feature}",
                feature=feature,
                bot_extension_module=module_name,
            )

            importlib.reload(sys.modules[module_name])
            config.load()

            with config.readonly:
                self.reload_extension(feature)

            return

        await self.enable_feature(feature)

    async def reload(self, features: set[str] | None = None) -> None:
        """Reload the entire bot and enable only the requested features.

        This method unloads all bot extension modules, searches for new bot extension modules,
        and reloads any bot extension modules that were already loaded.

        If no features are given the bot tries to re-enable all features that were enabled prior to
        the reload.

        One crucial difference between the initial load and a reload is that the intents **CANNOT**
        be updated once the bot is running.
        Any new features may not function if they require an intent that was not already set.

        Parameters
        ----------
        features : set[str] | None, optional
            The set of features to enable after reloading the bot.
            If not given the bot will try to re-enable the features that were enabled before the
            reload.

        """
        await log.ainfo("Reloading all bot extensions.")

        for module_name in tuple(self.extensions.keys()):
            await log.ainfo(f"Unloading bot extension module: {module_name}")
            self.unload_extension(module_name)

        if features is None:
            features = self.enabled_features

        features_.discover_features()
        feature_modules: set[str] = {
            features_.get_module_name_by_feature(feature)
            for feature in features or features_.all_features
            if feature in features_.all_features
        }
        config.load()

        await log.ainfo(f"Loading bot extension modules: {", ".join(sorted(feature_modules))}")

        with config.readonly:
            self.load_extensions(*feature_modules)

    async def is_active(self) -> bool:
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

    def get_user_name(self, user: discord.User | discord.Member) -> str:
        """Get the nickname or display name of the user or member.

        If the user has an assigned nickname for the guild, use it.

        Otherwise, return the display name for the user. The display name defaults to the username
        if it is not configured.

        Parameters
        ----------
        user : discord.User | discord.Member
            The `discord.User` or `discord.Member`.

        Returns
        -------
        str
            The name of the user.

        """
        return user.nick if hasattr(user, "nick") and user.nick else user.display_name

    async def on_application_command_error(
        self,
        ctx: discord.ApplicationContext,
        exception: discord.DiscordException,
    ) -> None:
        """Catch all application command errors and log them.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The context for the current command.
        exception : discord.DiscordException
            The exception raised from the application command.

        """
        if self._event_handlers.get("on_application_command_error", None):
            return

        if (command := ctx.command) and command.has_error_handler():
            return

        if (cog := ctx.cog) and cog.has_error_handler():
            return

        await log.aexception(
            "An exception occurred while running an application command.",
            exc_info=exception,
        )


def run(**kwargs: Any) -> None:
    """Create a new `Bot` and run the event loop.

    This is a shortcut function for creating a new bot and starting an event loop using a registered
    `discord_token` configuration attribute.

    Parameters
    ----------
    kwargs : dict[str, Any]
        Keyword values to be passed to the `Bot` when it is initialized.

    """
    Bot(
        features=config.bot_enabled_features,
        disable_admin_commands=config.disable_admin_commands,
        **kwargs,
    ).run(config.discord_token)
