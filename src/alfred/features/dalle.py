"""Adds commands for generating images using DALL-E.

Commands
--------
/draw dall-e-2 prompt [size] [quality]
    Generates an image using DALL-E-2 with the specified parameters.

/draw dall-e-3 prompt [size] [quality]
    Generates an image using DALL-E-3 with the specified parameters.

"""

from __future__ import annotations

import ast
import enum
import http
import io
import pathlib
import typing
import urllib.parse

import aiohttp
import discord
import openai
import structlog

from alfred import bot, feature
from alfred import exceptions as exc
from alfred.translation import gettext as _

if typing.TYPE_CHECKING:
    from typing import Literal

__all__ = ("DallE",)

#: The name of the feature.
_FEATURE: str = "Dall-E"

_log: structlog.stdlib.BoundLogger = structlog.get_logger(feature=_FEATURE)


class _DrawPresence(enum.Enum):
    """Presence messages for drawing with different models."""

    DALL_E_2 = discord.CustomActivity("Drawing an image with DALL-E 2")
    DALL_E_3 = discord.CustomActivity("Drawing an image with DALL-E 3")


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


#: Type alias to make the AI client happy.
type _DallESizes = Literal["256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"]

#: Type alias to make the AI client happy.
type _DallEImageQuality = Literal["standard", "hd"]


@feature.name(_FEATURE)
class DallE(feature.Feature):
    """Manages AI art interactions and commands in the bot."""

    #: An asynchronous OpenAI client.
    ai: openai.AsyncOpenAI

    #: The bot to which this feature is attached.
    bot: bot.Bot

    #: The intents required by this feature.
    intents: discord.Intents = discord.Intents(guilds=True)

    #: The command group all commands in this feature must be under.
    draw = feature.CommandGroup("draw", "Commands for drawing images using DALL-E.")

    @draw.command(name=_Model.DALL_E_3)
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
        choices=_DallE3Sizes.__members__.values(),
        parameter_name="size",
    )
    @discord.option(
        _("quality"),
        str,
        required=False,
        choices=_ImageQuality.__members__.values(),
        parameter_name="quality",
    )
    async def dalle3(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: _DallE3Sizes = _DallE3Sizes.SQUARE,
        quality: _ImageQuality = _ImageQuality.STANDARD,
    ) -> None:
        """Generate an image using DALL-E 3.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
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
        async with self.bot.presence(activity=_DrawPresence.DALL_E_3.value):
            await self._generate_image(
                ctx,
                prompt=prompt,
                size=size,
                quality=quality,
                model=_Model.DALL_E_3,
            )

    @draw.command(name=_Model.DALL_E_2)
    @discord.option(_("prompt"), str, required=True, parameter_name="prompt")
    @discord.option(
        _("size"),
        str,
        required=False,
        choices=_DallE2Sizes.__members__.values(),
        parameter_name="size",
    )
    @discord.option(
        _("quality"),
        str,
        required=False,
        choices=_ImageQuality.__members__.values(),
        parameter_name="quality",
    )
    async def dalle2(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: _DallE2Sizes = _DallE2Sizes.LARGE,
        quality: _ImageQuality = _ImageQuality.STANDARD,
    ) -> None:
        """Generate an image using DALL-E 2.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
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
        async with self.bot.presence(activity=_DrawPresence.DALL_E_2.value):
            await self._generate_image(
                ctx,
                prompt=prompt,
                size=size,
                quality=quality,
                model=_Model.DALL_E_2,
            )

    async def _generate_image(
        self,
        ctx: discord.ApplicationContext,
        *,
        prompt: str,
        size: _DallE2Sizes | _DallE3Sizes = _DallE3Sizes.SQUARE,
        quality: _ImageQuality = _ImageQuality.STANDARD,
        model: _Model = _Model.DALL_E_3,
    ) -> None:
        """Generate an image using DALL-E.

        Parameters
        ----------
        ctx : discord.ApplicationContext
            The discord context for the current command.
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

        try:
            response: openai.types.ImagesResponse = await self.ai.images.generate(
                model=model,
                prompt=prompt,
                size=typing.cast(_DallESizes, size),
                quality=typing.cast(_DallEImageQuality, quality),
                n=1,
            )
            url: str = typing.cast(str, response.data[0].url)
            image: discord.File = await self._get_image(url)
            await ctx.respond(file=image)
        except openai.OpenAIError as e:
            await _log.awarning(str(e), exc_info=e)
            message: str = self._parse_openai_error(e)
            await ctx.respond(message)
        except exc.ImageDownloadError as e:
            await _log.awarning(str(e), exc_info=e)
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
        async with aiohttp.ClientSession() as session, session.get(uri) as resp:
            if resp.status != http.HTTPStatus.OK:
                raise exc.ImageDownloadError(uri, response=resp)

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
        filename: str = pathlib.Path(path).name
        return filename
