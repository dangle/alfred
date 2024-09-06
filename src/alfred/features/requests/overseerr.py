"""A feature for sending requests to Overseerr."""

from __future__ import annotations

import discord
import openai
import structlog

from alfred.core import feature, models
from alfred.features.requests import request_command

__all__ = ("Overseerr",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class Overseerr(feature.Feature):
    """Manages AI art interactions and commands in the bot."""

    #: An asynchronous OpenAI client.
    ai: openai.AsyncOpenAI

    #: The bot to which this feature is attached.
    bot: models.Staff

    #: The intents required by this feature.
    intents: discord.Intents = discord.Intents(guilds=True)

    @request_command.command()
    async def request_movie(self) -> None:
        pass
