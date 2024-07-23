"""Adds administrator commands for managing features and reloading the `bot.Bot` when necessary.

Commands
--------
/admin enabled
    Returns the set of all enabled features.

/admin disabled
    Returns the set of all disabled features.

/admin enable feature
    Enables the given feature.

/admin disable feature
    Disables the given feature.

/admin reload [feature]
    If `feature` is given it reloads that feature; otherwise, it reloads the entire `bot.Bot`.

"""

import discord
from discord.ext import commands

from alfred import bot
from alfred.config import config
from alfred.features import features
from alfred.translation import gettext as _

__all__ = (
    "__intents__",
    "setup",
)

# Set the name of the feature.
__feature__: str = "Bot Admin"

# This feature requires the standard `guilds` `discord.Intents` in order to function because it
# requires administrator priviledges to run each command.
__intents__ = discord.Intents(guilds=True)


def setup(bot: bot.Bot) -> None:
    """Add this feature `commands.Cog` to the `bot.Bot`.

    Parameters
    ----------
    bot : bot.Bot
        The `bot.Bot` to which to add the feature.

    """
    bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    """Contains commands and listeners for controlling the state of the bot itself.

    Parameters
    ----------
    bot : Bot
        A Discord bot that will use the commands and listeners in this Cog.

    """

    admin = discord.default_permissions(administrator=True)(
        discord.SlashCommandGroup(
            "admin",
            f"Commands for administering {config.bot_name}.",
            guild_ids=config.admin_guild_ids or config.guild_ids,
        ),
    )

    def __init__(self, bot: bot.Bot) -> None:
        self._bot = bot

    @property
    def all_features(self) -> set[str]:
        """Return all features other than this one.

        Returns
        -------
        set[str]
            The set of all known features other than this one.

        """
        return features.all_features - {__feature__}

    @property
    def enabled_features(self) -> set[str]:
        """Return all enabled features other than this one.

        Returns
        -------
        set[str]
            The set of all enabled features other than this one.

        """
        return self._bot.enabled_features - {__feature__}

    @property
    def disabled_features(self) -> set[str]:
        """Return all disabled features other than this one.

        Returns
        -------
        set[str]
            The set of all disabled features other than this one.

        """
        return self._bot.disabled_features - {__feature__}

    @commands.is_owner()
    @admin.command()
    async def enabled(self, ctx: discord.ApplicationContext) -> None:
        """List all enabled features.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.

        """
        if not self.enabled_features:
            await ctx.respond(_("There are no enabled features."))
            return

        enabled_features = ", ".join(sorted(self.enabled_features))
        await ctx.respond(
            _("The following features are enabled: {enabled_features}").format(
                enabled_features=enabled_features,
            ),
        )

    @commands.is_owner()
    @admin.command()
    async def disabled(self, ctx: discord.ApplicationContext) -> None:
        """List all disabled features.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.

        """
        if not self.disabled_features:
            await ctx.respond(_("There are no disabled features."))
            return

        disabled_features = ", ".join(sorted(self.disabled_features))
        await ctx.respond(
            _("The following features are disabled: {disabled_features}").format(
                disabled_features=disabled_features,
            ),
        )

    @commands.is_owner()
    @discord.option(_("feature"), str, required=True, parameter_name="feature")
    @admin.command()
    async def enable(self, ctx: discord.ApplicationContext, *, feature: str) -> None:
        """Enable a bot feature.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
        feature : str
            The name of the feature to enable.

        """
        if feature not in self.all_features:
            await ctx.respond(_("Feature not found: {feature}").format(feature=feature))
            return

        if feature not in self.enabled_features:
            try:
                await self._bot.enable_feature(feature)
            except Exception:
                await ctx.respond(_("Failed to enable feature: {feature}").format(feature=feature))
                raise

        await ctx.respond(_("Feature enabled: {feature}").format(feature=feature))

    @commands.is_owner()
    @discord.option(_("feature"), str, required=True, parameter_name="feature")
    @admin.command()
    async def disable(self, ctx: discord.ApplicationContext, *, feature: str) -> None:
        """Disable a bot feature.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
        feature : str
            The name of the feature to disable.

        """
        if feature not in self.all_features:
            await ctx.respond(_("Feature not found: {feature}").format(feature=feature))
            return

        if feature not in self.enabled_features:
            await ctx.respond(_("Feature not enabled: {feature}").format(feature=feature))
            return

        try:
            await self._bot.disable_feature(feature)
        except Exception:
            await ctx.respond(_("Failed to disable feature: {feature}").format(feature=feature))
            raise

        await ctx.respond(_("Disabling feature: {feature}").format(feature=feature))

    @commands.is_owner()
    @discord.option(_("feature"), str, required=False, parameter_name="feature")
    @admin.command()
    async def reload(self, ctx: discord.ApplicationContext, *, feature: str | None = None) -> None:
        """Reload a bot feature.

        If no feature is specified, reload the entire bot.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
        feature : str | None, optional
            The name of the feature to reload.
            If not specified, the entire bot will be reloaded.

        """
        if not feature:
            await self._bot.reload()
            await ctx.respond(_("Reloaded bot features"))
            return

        if feature not in self.all_features:
            await ctx.respond(_("Feature not found: {feature}").format(feature=feature))
            return

        try:
            await self._bot.reload_feature(feature)
        except Exception:
            await ctx.respond(_("Failed to reload feature: {feature}").format(feature=feature))
            raise

        await ctx.respond(_("Feature reloaded: {feature}").format(feature=feature))
