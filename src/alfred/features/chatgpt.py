"""Adds commands and event listeners for interacting with Discord and ChatGPT.

Commands
--------

"""

import discord
import structlog
from discord.ext import commands

from ..config import config
from . import _ai

__all__ = ("setup",)

# Set the name of the feature.
__feature__: str = "ChatGPT"

log: structlog.stdlib.BoundLogger = structlog.get_logger(feature=__feature__)

_ai.configure_ai()


def setup(bot: discord.Bot) -> None:
    """Add this feature `commands.Cog` to the `bot.Bot`.

    If the `openai.OpenAI` client has not been configured as the `ai` configuration attribute this
    will not be added to the `bot.Bot`.

    Parameters
    ----------
    bot : bot.Bot
        The `bot.Bot` to which to add the feature.
    """

    if config.ai:
        bot.add_cog(ChatGPT(bot))
        return

    log.info(
        f'Config does not have the "ai" attribute. Not adding the feature: {__feature__}'
    )


class ChatGPT(commands.Cog):
    """Manages chat interactions and commands in the bot.

    Parameters
    ----------
    bot : discord.bot.Bot
        The `bot.Bot` that will be used for running any interpreted commands.
    """

    def __init__(self, bot: discord.Bot) -> None:
        self._bot = bot
