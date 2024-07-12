"""
Contains functions and attributes for processing command-line flags and environment variables.

Attributes
----------
config : _Config
    An instance of `_Config` that can be globally accessed for sharing processed attributes from
    command-line flags and environment variables.
csv : Callable[[str], list[str]]
    A simple processor for environment variables that will parse a comma-separated list of strings.
    This can be used by passing it into the `type` attribute of `EnvironmentVariable`.
CommandLineFlag : type
    A dataclass containing a subset of the arguments for `argparse.add_argument` that can be used to
    configure a value and update the available command-line flags.
EnvironmentVariable : type
    A dataclass containing the name of an environment variable and an optional `type` attribute that
    will be used to process the environment variable.

Examples
--------
>>> from alfred.config import CommandLineFlag, EnvironmentVariable, config, csv
>>> config(name="a_variable", env="A_VARIABLE")
>>> config(
    name="list_variable",
    env=EnvironmentVariable(
        name="list_VARIABLE",
        type=csv,
    )
    flag=CommandLineFlag(
        name="--list-variable",
        nargs="*",
        short="-l",
    ),
    required=True,
)
...
>>> config.load()
...
>>> config.a_variable
"a value"
>>> config.list_variable
['a', 'b', 'c']
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import os
from typing import Any, Container, Generator, Iterable, Literal, cast

import dotenv
import structlog

from .exceptions import (
    ConfigurationException,
    EnvironmentVariableException,
    FlagException,
    ReadonlyConfigurationException,
    RequiredValueException,
)
from .typing import ArgParseAction, ConfigProcessor

__all__ = (
    "config",
    "csv",
    "CommandLineFlag",
    "EnvironmentVariable",
)

log: structlog.stdlib.BoundLogger = structlog.get_logger()


def csv(value: str) -> list[str]:
    """Return a parsed list of comma-separated values from the given value.

    Parameters
    ----------
    value : str
        A string containing comma-separated list of values.

    Returns
    -------
    list[str]
        A list of all values in the comma-separated string.
    """

    return [v.strip() for v in value.split(",")] if value else []


@dataclasses.dataclass
class EnvironmentVariable:
    """A dataclass for storing the name of an environment variable and an optional result type."""

    name: str
    type: ConfigProcessor | None = None


@dataclasses.dataclass
class CommandLineFlag:
    """A dataclass for storing the supported subset of `argparse` argument parameters.

    `name` and `short` are the positional parameters to pass to `argparse.add_argument`.

    See: https://docs.python.org/3/library/argparse.html#quick-links-for-add-argument
    """

    # The command flag to add to `argparse`.
    # This can be a required flag, an optional value ("--flag"), or a short optional flag ("-f").
    name: str

    # An alias of the command.
    # This is often the short optional flag ("-f").
    short: str | None = None

    # Supported `argparse.add_argument` flags.
    action: ArgParseAction = "store"
    choices: Container[Any] | None = None
    const: Any = None
    help: str | None = None
    metavar: str | None = None
    nargs: int | Literal["*"] | Literal["?"] | Literal["+"] | None = None
    type: ConfigProcessor | None = None


@dataclasses.dataclass
class _ConfigValue:
    """A configuration value that will be parsed when `config.load()` is called.

    Attributes
    ----------
    name : str
        The name of the configuration attribute that will be assigned the computed value.
    env : str | None, optional
        The name of the environment variable, if given.
    flag : str | None, optional
        The name of the command-line flag, if given.
    required : bool, optional
        Whether or not this configuration attribute is required.
        Defaults to False.
    default : Any, optional
        The value to return if neither the environment variable nor the command-line flag were
        given.
    """

    name: str
    env: EnvironmentVariable | None = None
    flag: CommandLineFlag | None = None
    required: bool = False
    default: Any = None
    _is_computed: bool = False
    _computed_value: Any = None

    @property
    def value(self) -> Any:
        """Process and return the configuration attribute value.

        Returns
        -------
        Any
            The processed configuration attribute.

        Raises
        ------
        ConfigurationException
            Raised when the an attempt to access the value is made before the configuration has been
            loaded, it is a required value, and there is no default value set.
        """

        if self._is_computed:
            return self._computed_value

        if self.default is not None:
            return self.default

        if self.required:
            raise ConfigurationException(
                f"Configuration attribute `{self.name}` is required but the configuration has not"
                " yet been loaded."
            )

    @value.setter
    def value(self, value: Any) -> None:
        """Set the processed value.

        Parameters
        ----------
        value : Any
            The processed value to return.
        """

        self._is_computed = True
        self._computed_value = value


class _Config:
    """A configuration mapping that loads and manages global configuration for the bot."""

    def __init__(self) -> None:
        vars(self)["_config"] = {}
        vars(self)["_is_readonly"] = False
        vars(self)["_is_loaded"] = False

    def __getattr__(self, name: str) -> Any:
        """Return the processed value of the configuration attribute.

        Parameters
        ----------
        name : str
            The name of the configuration attribute to retrieve.

        Returns
        -------
        Any
            The processed value of the configuration attribute.

        Raises
        ------
        AttributeError
            Raised if the attribute is not in the configuration map.
        ConfigurationException
            Raised when the an attempt to access the value is made before the configuration has been
            loaded, it is a required value, and there is no default value set.
        """

        if name not in vars(self)["_config"]:
            raise AttributeError(name=name, obj=self)

        return vars(self)["_config"][name].value

    def __setattr__(self, name: str, value: _ConfigValue) -> None:
        """Assign a `_ConfigValue` to an an attribute.

        Parameters
        ----------
        name : str
            The name of the attribute to set.
        value : _ConfigValue
            The configured value that will be stored in the attribute.
        """

        if vars(self)["_is_readonly"]:
            raise ReadonlyConfigurationException(
                f"Unable to set configuration attribute {name} while the configuration is"
                " read-only."
            )

        vars(self)["_config"][name] = value

    def __getitem__(self, name: str) -> Any:
        """Return the processed value of the configuration attribute.

        Parameters
        ----------
        name : str
            The name of the configuration attribute to retrieve.

        Returns
        -------
        Any
            The processed value of the configuration attribute.

        Raises
        ------
        AttributeError
            Raised if the attribute is not in the configuration map.
        ConfigurationException
            Raised when the an attempt to access the value is made before the configuration has been
            loaded, it is a required value, and there is no default value set.
        """

        return vars(self)["__getattr__"](name)

    def __setitem__(self, name: str, value: _ConfigValue) -> None:
        """Assign a `_ConfigValue` to an an attribute.

        Parameters
        ----------
        name : str
            The name of the attribute to set.
        value : _ConfigValue
            The configured value that will be stored in the attribute.
        """

        vars(self)["__setattr__"](name, value)

    @property
    def config(self) -> dict[str, Any]:
        """Return the dictionary of the raw `_ConfigValue` objects.

        Returns
        -------
        dict[str, Any]
            A map of configuration attribute names to `_ConfigValue` objects
        """

        return vars(self)["_config"]

    def __call__(
        self,
        name: str,
        *,
        flag: CommandLineFlag | str | None = None,
        env: EnvironmentVariable | str | None = None,
        default: Any = None,
        required: bool = False,
    ) -> None:
        """Register a new configuration attribute.

        If currently in a `config.readonly` context block, this will silently do nothing.

        Parameters
        ----------
        name : str
            The name of the configuration attribute to set.
        env : str | None, optional
            The name of the environment variable, if given.
        flag : str | None, optional
            The name of the command-line flag, if given.
        default : Any, optional
            The value to return if neither the environment variable nor the command-line flag are
            found by the time `config.load()` has been called.
        required : bool, optional
            Whether or not this configuration attribute is required.
            Defaults to False.

        Raises
        ------
        ValueError
            Raised if `name` is an empty string or neither`flag` nor `env` are specified.
        """

        if vars(self)["_is_readonly"]:
            return

        if not name:
            raise ValueError("`name` must not be empty.")

        if not flag and not env:
            raise ValueError("At least one of `flag` or `env` must be given.")

        if isinstance(env, str):
            env = EnvironmentVariable(name=env) if env else None

        if isinstance(flag, str):
            flag = CommandLineFlag(name=flag) if flag else None

        setattr(
            self,
            name,
            _ConfigValue(
                name=name, env=env, flag=flag, required=required, default=default
            ),
        )

    @property
    @contextlib.contextmanager
    def readonly(self) -> Generator[None, None, None]:
        """A context manager that will lock the configuration so that no changes can occur.

        This is useful for loading features because `discord.Bot.load_extension` executes the module
        code again after the module has been loaded for configuring the bot.

        Setting the configuration to read-only prevents computed values from being overwritten by
        non-computed values.
        """

        vars(self)["_is_readonly"] = True
        try:
            yield
        finally:
            vars(self)["_is_readonly"] = False

    @property
    def is_loaded(self) -> bool:
        """Return a boolean value representing if the configuration has been loaded.

        Returns
        -------
        bool
            `True` if the configuration has been loaded, else `False`.
        """

        return vars(self)["_is_loaded"]

    def load(self, *args: Any, **kwargs: Any) -> None:
        """Load all environment variables and parse the command-line arguments.

        Parameters
        ----------
        args : Any
            All positional arguments are passed to `argparse.ArgumentParser`.
        kwargs : Any
            All keyword arguments are passed to `argparse.ArgumentParser`.

        See: https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        """

        if vars(self)["_is_readonly"]:
            raise ReadonlyConfigurationException(
                "Unable to load the configuration while the configuration is read-only."
            )

        dotenv.load_dotenv()

        self._load_flags(*args, **kwargs)
        self._load_env()

        uncomputed_config_values: Iterable[_ConfigValue] = (
            urcv for urcv in self.config.values() if not urcv._is_computed
        )
        for cv in uncomputed_config_values:
            if cv.default is not None:
                cv.value = cv.default
                continue

            if cv.required:
                if cv.env and cv.flag:
                    raise RequiredValueException(
                        name=cv.name,
                        env_name=cv.env.name,
                        flag_name=cv.flag.name,
                    )
                if cv.env:
                    raise EnvironmentVariableException(cv.env.name)
                if cv.flag:
                    raise FlagException(cv.flag.name)

        vars(self)["_is_loaded"] = True

    def _load_flags(self, *args: Any, **kwargs: Any) -> None:
        """Load and parse the command-line arguments.

        Parameters
        ----------
        args : Any
            All positional arguments are passed to `argparse.ArgumentParser`.
        kwargs : Any
            All keyword arguments are passed to `argparse.ArgumentParser`.

        See: https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        """

        kwargs["exit_on_error"] = not vars(self)["_is_loaded"]
        parser = argparse.ArgumentParser(*args, **kwargs)

        flag_config_values: tuple[_ConfigValue, ...] = tuple(
            cast(_ConfigValue, cv) for cv in self.config.values() if cv.flag
        )

        for cv in flag_config_values:
            # Inform mypy that this cannot be None
            cv.flag = cast(CommandLineFlag, cv.flag)

            kw: dict[str, Any] = copy.copy(vars(cv.flag))
            del kw["name"]
            del kw["short"]

            for k in set(kw.keys()):
                if getattr(cv.flag, k) is None:
                    del kw[k]

            if cv.required and not cv.env and cv.flag.name.startswith("-"):
                kw["required"] = True

            parameters: tuple[str] | tuple[str, str] = (
                (cv.flag.short, cv.flag.name) if cv.flag.short else (cv.flag.name,)
            )
            parser.add_argument(*parameters, dest=cv.name, **kw)

        parsed_args: argparse.Namespace = parser.parse_args()

        for cv in flag_config_values:
            value: Any = getattr(parsed_args, cv.name)
            if value is not None:
                cv.value = value

    def _load_env(self) -> None:
        """Load and parse all configured environment variables."""

        env_config_values: Iterable[_ConfigValue] = (
            cv for cv in self.config.values() if cv.env and not cv._is_computed
        )
        for cv in env_config_values:
            # Inform mypy that this cannot be None
            cv.env = cast(EnvironmentVariable, cv.env)

            if cv.env.name and cv.env.name in os.environ:
                if cv.env.type is None:
                    cv.value = os.environ[cv.env.name]
                    continue

                try:
                    cv.value = cv.env.type(os.environ[cv.env.name])
                except Exception as e:
                    message: str = (
                        f"Unable to process environment variable: {cv.env.name}"
                    )
                    if cv.required:
                        raise ValueError(message) from e
                    log.warning(message, exc_info=e)

    def unload(self) -> None:
        """Reset the configuration mapping.

        Raises
        ------
        ReadonlyConfigurationException
            Raised if this is called while in the `config.readonly` context manager.
        """

        if vars(self)["_is_readonly"]:
            raise ReadonlyConfigurationException(
                "Unable to unload the configuration while the configuration is read-only."
            )

        del vars(self)["_config"]
        vars(self)["_config"] = {}
        vars(self)["_is_loaded"] = False

    def __delete__(self, name: str) -> None:
        """Delete the given configuration attribute.

        Parameters
        ----------
        name : str
            The name of the configuration attribute to delete.
        """

        del vars(self)["_config"][name]


# The globally accessed configuration instance
config = _Config()