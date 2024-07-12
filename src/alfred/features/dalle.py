"""Adds commands for generating images using DALL-E.

Commands
--------
/dall-e-2 prompt [size] [quality]
    Generates an image using DALL-E-2 with the specified parameters.
/dall-e-3 prompt [size] [quality]
    Generates an image using DALL-E-3 with the specified parameters.
"""

import ast
import enum
import io
import os.path
import urllib.parse
from typing import cast

import aiohttp
import discord
import openai
import structlog
from discord.ext import commands

from .. import exceptions as exc
from ..config import config
from ..translation import gettext as _
from . import _ai

__all__ = ("setup",)

# Set the name of the feature.
__feature__: str = "Dall-E"

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
        bot.add_cog(DallE())
        return

    log.info(
        f'Config does not have the "ai" attribute. Not adding the feature: {__feature__}'
    )


class _Model(enum.StrEnum):
    """DALL-E models that can be used through the API."""

    DALL_E_2 = "dall-e-2"
    DALL_E_3 = "dall-e-3"


class _ImageQuality(enum.StrEnum):
    """Allowable quality values for image generation."""

    STANDARD = enum.auto()
    HD = enum.auto()


class _DallE2Sizes(enum.StrEnum):
    """Valid DALL-E-2 image sizes."""

    SMALL = "256x256"
    MEDIUM = "512x512"
    LARGE = "1024x1024"


class _DallE3Sizes(enum.StrEnum):
    """Valid DALL-E-3 image sizes."""

    SQUARE = "1024x1024"
    WIDE = "1792x1024"
    TALL = "1024x1792"


class DallE(commands.Cog):
    """Manages AI art interactions and commands in the bot."""

    @commands.slash_command(name=_Model.DALL_E_3.value, guild_ids=config.guild_ids)
    @discord.option(
        _("prompt"),
        str,
        required=True,
        parameter_name="prompt",
    )
    @discord.option(
        _("size"),
        str,
        required=False,
        choices=_DallE3Sizes.__members__,
        parameter_name="size",
    )
    @discord.option(
        _("quality"),
        str,
        required=False,
        choices=_ImageQuality.__members__,
        parameter_name="quality",
    )
    async def dalle3(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: str = _DallE3Sizes.SQUARE,
        quality: str = _ImageQuality.STANDARD,
    ) -> None:
        """Generate an image using DALL-E 3.

        Parameters
        ----------
        prompt : str
            The prompt to use when generating the image.
        size : str, optional
            The size of the image to generate.
            This must be one of the following: "1024x1024", "1024x1792", "1792x1024".
            If not specified it will default to "1024x1024".
        quality : str, optional
            The quality of the image to generate.
            This must be one of the following: "standard" or "hd".
            If this is not specified it will default to "standard".
        """

        await self._generate_image(
            ctx, prompt=prompt, size=size, quality=quality, model=_Model.DALL_E_3.value
        )

    @commands.slash_command(name=_Model.DALL_E_2.value, guild_ids=config.guild_ids)
    @discord.option(
        _("prompt"),
        str,
        required=True,
        parameter_name="prompt",
    )
    @discord.option(
        _("size"),
        str,
        required=False,
        choices=_DallE2Sizes.__members__,
        parameter_name="size",
    )
    @discord.option(
        _("quality"),
        str,
        required=False,
        choices=_ImageQuality.__members__,
        parameter_name="quality",
    )
    async def dalle2(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: str = _DallE2Sizes.LARGE,
        quality: str = _ImageQuality.STANDARD,
    ) -> None:
        """Generate an image using DALL-E 2.

        Parameters
        ----------
        prompt : str
            The prompt to use when generating the image.
        size : str, optional
            The size of the image to generate.
            This must be one of the following: "256x256", "512x512", "1024x1024".
            If not specified it will default to "1024x1024".
        quality : str, optional
            The quality of the image to generate.
            This must be one of the following: "standard" or "hd".
            If this is not specified it will default to "standard".
        """

        await self._generate_image(
            ctx, prompt=prompt, size=size, quality=quality, model=_Model.DALL_E_2.value
        )

    async def _generate_image(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: str = _DallE3Sizes.SQUARE.value,
        quality: str = _ImageQuality.STANDARD.value,
        model: str = _Model.DALL_E_3.value,
    ) -> None:
        """Generate an image using DALL-E.

        Parameters
        ----------
        prompt : str
            The prompt to use when generating the image.
        size : str, optional
            The size of the image to generate.
            This must be one of the following:
                "256x256", "512x512", "1024x1024", "1024x1792", "1792x1024".
            If not specified it will default to "1024x1024".
        quality : str, optional
            The quality of the image to generate.
            This must be one of the following: "standard" or "hd".
            If this is not specified it will default to "standard".
        model : str, optional
            The model to use when generating the image.
            This must be one of the following: "dall-e-2" or "dall-e-3".
            If this is not specified it will default to "dall-e-3".
        """

        await ctx.defer()

        log: structlog.stdlib.BoundLogger = structlog.get_logger(feature=__feature__)

        try:
            response: openai.types.ImagesResponse = config.ai.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
            url: str = cast(str, response.data[0].url)
            image: discord.File = await self._get_image(url)
            await ctx.respond(file=image)
        except openai.OpenAIError as e:
            await log.awarning(str(e), exc_info=e)
            message: str = self._parse_openai_error(e)
            await ctx.respond(message)
        except exc.ImageDownloadException as e:
            await log.awarning(str(e), exc_info=e)
            await ctx.respond(_("Unable to download generated image."))

    async def _get_image(self, uri: str) -> discord.File:
        """Download the image from the URI.

        Parameters
        ----------
        uri : str
            The URI of the image.

        Returns
        -------
        discord.File
            A `discord.File` image that can be sent in the response to the user.
        """

        async with aiohttp.ClientSession() as session:
            async with session.get(uri) as resp:
                if resp.status != 200:
                    raise exc.ImageDownloadException(uri, response=resp)

                data: bytes = await resp.read()
                name: str = self._get_image_name(uri)
                return discord.File(io.BytesIO(data), name)

    def _parse_openai_error(self, error: openai.OpenAIError) -> str:
        """Parse the extra error information from an `openai.OpenAIError` object.

        Parameters
        ----------
        error : openai.OpenAIError
            The `openai.OpenAIError` returned by the service.
            These often have extra data that looks JSON-esque but can be evaluated as a Python dict.

        Returns
        -------
        str
            Any extra data from inside the `error` or, failing that, the `error` cast to a `str`.
        """

        message: str = str(error)
        data_start: int = message.find("{")
        if data_start > -1:
            data = ast.literal_eval(message[data_start:])
            if "error" in data and "message" in data["error"]:
                return data["error"]["message"]
        return message

    def _get_image_name(self, uri: str) -> str:
        """Get the name of the image from the given URI.

        Parameters
        ----------
        uri : str
            The URI of the image.

        Returns
        -------
        str
            The name of the image file.
        """

        parsed: urllib.parse.ParseResult = urllib.parse.urlparse(uri)
        path: str = urllib.parse.unquote_plus(parsed.path)
        filename: str = os.path.basename(path)
        return filename
