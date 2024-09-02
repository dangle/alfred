"""Exceptions for errors that occur during bot usage."""

from __future__ import annotations

import os
import typing

if typing.TYPE_CHECKING:
    from typing import Any

    from alfred.util.typing import ExitCode

__all__ = (
    "BotError",
    "ImageDownloadError",
    "ConfigurationError",
    "RequiredValueError",
    "FeatureError",
    "FeatureNotFoundError",
)


class BotError(Exception):
    """A base class used for all errors occurring during bot usage.

    Parameters
    ----------
    message : str
        The message to log out to the user that describes the error.
    exit_code : ExitCode, optional
        The integer return code to return when the program exits.

    Attributes
    ----------
    message : str
        The message that will be logged to the user describing the error.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.

    """

    def __init__(self, message: str, exit_code: ExitCode = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.exit_code: ExitCode = exit_code

    def __reduce__(self) -> tuple[type[BotError], tuple[Any, ...]]:
        """Allow instances of this exception to be serialized.

        Returns
        -------
        tuple[T, tuple[Any, ...]]
            A tuple containing the class of this specific instance and another tuple containing all
            attributes that can be serialized.

        """
        return (type(self), tuple(vars(self).values()))


class ImageDownloadError(BotError):
    """Raised when an image failed to download.

    Parameters
    ----------
    image_uri : str
        The URI of the image that failed to download.
    context : bt.BotExceptionContext
        Any extra context relevant to explaining why the image failed to download.

    Attributes
    ----------
    message : str
        The message that will be logged describing the download failure.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `None`.
    image_uri : str
        The URI of the image that failed to download.
    context : dict[str, Any]
        Optional extra information relevant to why the download failed.

    """

    def __init__(self, image_uri: str, **context: Any) -> None:
        super().__init__(f"Unable to download image at URI: {image_uri}")
        self.image_uri: str = image_uri
        self.context = context


class ConfigurationError(BotError):
    """A base class used for all errors occurring from misconfiguration of the bot.

    Parameters
    ----------
    message : str
        The message to log that describes the configuration error.

    Attributes
    ----------
    message : str
        The message that will be logged describing the configuration error.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.

    """

    def __init__(self, message: str) -> None:
        super().__init__(message, os.EX_CONFIG)


class RequiredValueError(ConfigurationError):
    """Raised when a required configuration value is missing.

    Parameters
    ----------
    name : str
        The name of the missing configuration value.
    namespace : str
        The namespace where the configuration value was expected to be found.

    Attributes
    ----------
    message : str
        The message that will be logged describing the configuration error.
    name : str
        The name of the missing configuration value.
    namespace : str
        The namespace where the configuration value was expected to be found.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.

    """

    def __init__(self, name: str, namespace: str) -> None:
        super().__init__(
            f"Required configuration value `{name}` is missing from namespace {namespace}.",
        )
        self.name = name
        self.namespace = namespace


class FeatureError(BotError):
    """A base class used for all errors occurring from failures while loading features.

    Parameters
    ----------
    feature : str
        The name of the feature that failed to load.
    message : str
        The message to log that explains why the feature could not be loaded.

    Attributes
    ----------
    message : str
        The message that will be logged explaining why the feature could not be loaded.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_SOFTWARE`.
    feature : str
        The name of the feature that failed to load.

    """

    def __init__(self, feature: str, message: str) -> None:
        super().__init__(message, os.EX_SOFTWARE)
        self.feature = feature


class FeatureNotFoundError(FeatureError):
    """Raised when a feature is requested to be loaded but cannot be found.

    Parameters
    ----------
    feature : str
        The name of the feature that could not be found.

    Attributes
    ----------
    message : str
        The message that will be logged explaining that the feature was not found.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_SOFTWARE`
    feature : str
        The name of the feature that could not be found.

    """

    def __init__(self, feature: str) -> None:
        super().__init__(feature, f"Feature not found: {feature}")
