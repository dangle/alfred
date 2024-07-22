"""Exceptions for errors that occur during bot usage."""

import os
import typing

from alfred.typing import ExitCode

__all__ = (
    "BotError",
    "ImageDownloadError",
    "ConfigurationError",
    "ReadonlyConfigurationError",
    "FlagError",
    "EnvironmentVariableError",
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

    def __reduce__[T: BotError](self) -> tuple[T, tuple[typing.Any, ...]]:  # type: ignore[name-defined]
        """Allow instances of this exception to be serialized.

        Returns
        -------
        tuple[T, tuple[typing.Any, ...]]
            A tuple containing the class of this specific instance and another tuple containing all
            attributes that can be serialized.

        """
        return (self.__class__, tuple(vars(self).values()))


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
    context : dict[str, typing.Any]
        Optional extra information relevant to why the download failed.

    """

    def __init__(self, image_uri: str, **context: typing.Any) -> None:
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


class ReadonlyConfigurationError(ConfigurationError):
    """Raised when a function an attempt to change the configuration is made while it is read-only.

    Parameters
    ----------
    message : str
        The message to log that describes the function called while the configuration was read-only.

    Attributes
    ----------
    message : str
        The message to log that describes the function called while the configuration was read-only.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.

    """


class FlagError(ConfigurationError):
    """Raised when a required command-line flag is missing.

    Parameters
    ----------
    flag_name : str
        The name of the missing command-line flag.

    Attributes
    ----------
    message : str
        The message that will be logged describing which flag is missing.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.
    flag_name : str
        The name of the missing command-line flag.

    """

    def __init__(self, flag_name: str) -> None:
        super().__init__(f"{flag_name} is a required command line flag")
        self.var_name: str = flag_name


class EnvironmentVariableError(ConfigurationError):
    """Raised when a required environment variable is missing.

    Parameters
    ----------
    env_name : str
        The name of the missing environment variable.

    Attributes
    ----------
    message : str
        The message that will be logged describing which environment variable is missing.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.
    env_name : str
        The name of the missing environment variable.

    """

    def __init__(self, env_name: str) -> None:
        super().__init__(f"{env_name} is a required environment variable")
        self.env_name: str = env_name


class RequiredValueError(ConfigurationError):
    """Raised when a required configuration value is missing.

    This should be raised when both an environment variable and a command-line flag are specified
    and the value is marked as required, but neither value is found.

    Parameters
    ----------
    name : str
        The name of the missing config attribute.
    flag_name : str
        The name of the missing command-line flag.
    env_name : str
        The name of the missing environment variable.

    Attributes
    ----------
    message : str
        The message that will be logged describing which configuration attribute was missing and the
        names of the command-line flag and environment variable checked.
    exit_code : ExitCode
        An integer return code that should be returned when the program exits.
        This is always `os.EX_CONFIG`.
    name : str
        The name of the missing config attribute.
    flag_name : str
        The name of the missing command-line flag.
    env_name : str
        The name of the missing environment variable.

    """

    def __init__(self, name: str, flag_name: str, env_name: str) -> None:
        super().__init__(
            f"Unable to get required attribute {name}"
            f" from flag `{flag_name} or environment variable `{env_name}`",
        )
        self.name: str = name
        self.flag_name: str = flag_name
        self.env_name: str = env_name


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
