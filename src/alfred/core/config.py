"""Contains functions and attributes for processing config files and environment variables.

Examples
--------
>>> from alfred import config
>>> config.register(name="a_variable", namespace="example", env="A_VARIABLE")
>>> config.register(name="list_variable", namespace="example", env="LIST_VARIABLE")
>>> config.init()
>>> config.example.a_variable
'a value'
>>> config.example.list_variable
['a', 'b', 'c']

"""

from __future__ import annotations

import builtins
import functools
import os
import pathlib
import tomllib
import types
import typing

import dotenv
import structlog

from alfred import __version__
from alfred.core import exceptions as exc
from alfred.core.exceptions import ConfigurationError, RequiredValueError

if typing.TYPE_CHECKING:
    from typing import Any, ClassVar, Self

    from alfred.util.typing import ConfigProcessor


__all__ = (
    "Config",
    "ConfigProxy",
    "get",
    "init",
    "register",
)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class ConfigProxy:
    """A proxy object that represents sub-namespaces in a 'Config' object.

    Parameters
    ----------
    config : dict[tuple[str, ...], Any]
        The internal data from a 'Config' object.
    valid_attrs : set[tuple[str, ...]]
        The set of valid attributes in the 'Config' object.
    namespace : tuple[str]
        A tuple of namespace segments representing the namespace to be proxied.

    """

    __slots__ = (
        "_config",
        "_valid_attrs",
        "_namespace",
    )

    def __init__(
        self,
        config: dict[tuple[str, ...], Any],
        valid_attrs: set[tuple[str, ...]],
        *namespace: str,
    ) -> None:
        self._config: dict[tuple[str, ...], Any] = config
        self._valid_attrs = valid_attrs
        self._namespace = namespace

    @functools.cache
    def __getattr__(self, name: str) -> Any:
        """Get an value from the configuration data in the proxied namespace.

        Parameters
        ----------
        name : str
            The name of the attribute to retrieve.

        Returns
        -------
        Any
            The value of the attribute or another 'ConfigProxy' object representing the next level
            of the namespace.

        Raises
        ------
        AttributeError
            Raised when the attribute is not valid in the given namespace.

        """
        qualified_name: tuple[str, ...] = (*self._namespace, name)

        if qualified_name in self._config:
            return self._config[qualified_name]

        if qualified_name in self._valid_attrs:
            return type(self)(self._config, self._valid_attrs, *qualified_name)

        raise AttributeError(f"'Config' object has no attribute '{".".join(qualified_name)}'")

    def __repr__(self) -> str:
        """Get a string representation of the 'ConfigProxy' object and the proxied namespace."""
        return f"<{type(self).__qualname__}: {".".join(self._namespace)}>"


class _ConfigAttribute[T](typing.NamedTuple):
    """A registered configuration attribute that will store configuration data."""

    #: A tuple representation of the namespace and attribute used for looking up the attribute.
    qualified_name: tuple[str, ...]

    #: An optional environment variable that will be used if set.
    #: Any value in an environment variable will take precedence over values in configuration files.
    env: str | None = None

    #: An optional parser that will be run on the configuration value.
    parser: ConfigProcessor[T] | None = None

    #: Whether or not an 'AttributeError' will be raised if the value is not configured.
    required: bool = False

    @functools.cached_property
    def name(self) -> str:
        """Get the attribute name of the configuration value.

        Returns
        -------
        str
            The attribute name of the configuration value.

        """
        return self.qualified_name[-1]

    @functools.cached_property
    def namespace(self) -> str:
        """Get the namespace in which the attribute is stored.

        Returns
        -------
        str
            The namespace in which the configuration value is stored.

        """
        return ".".join(self.qualified_name[:-1])


@typing.final
class Config:
    """A global configuration singleton to be used by the application and any spawned bots.

    Parameters
    ----------
    config_file : pathlib.Path | str | None
        The path to the TOML configuration file containing the configuration data.

    """

    __slots__ = (
        "_config",
        "_valid_attrs",
    )

    #: The singleton instance of the class.
    __instance: ClassVar[Config | None] = None

    #: The registry of all configurable values.
    _registry: ClassVar[dict[tuple[str, ...], _ConfigAttribute]] = {}

    #: Whether or not the 'Config' has been initialized.
    _initialized: ClassVar[bool] = False

    def __init__(self, config_file: pathlib.Path | str | None = None) -> None:
        self._config: dict[tuple[str, ...], Any] = {}
        self._valid_attrs: set[tuple[str, ...]] = set()

        self._process_env()

        if config_file:
            data: dict[str, Any] = self._load_file(config_file)
            self._process(data)

        for attr in self._registry.values():
            if attr.required and attr.qualified_name not in self._config:
                raise RequiredValueError(name=attr.name, namespace=attr.namespace)

        Config._initialized = True

    def __new__(cls) -> Self:
        """Return the initialized singleton 'Config' instance.

        Returns
        -------
        Self
            The singleton initialized 'Config' instance.

        Raises
        ------
        exc.ConfigurationError
            Raised if 'Config.init' has not been called.

        """
        if cls.__instance is None:
            raise exc.ConfigurationError(
                f"Class '{cls.__qualname__}' has not been initialized. "
                f"Call '{cls.__qualname__}.init()' first.",
            )

        return cls.__instance

    @classmethod
    def init(cls, *args: Any, **kwargs: Any) -> Any:
        """Initialize the new Config class and store it as a singleton.

        Parameters
        ----------
        args : Any
            Positional arguments to pass to 'cls.__init__'.
        kwargs : Any
            Keyword arguments to pass to 'cls.__init__'.

        Returns
        -------
        Any
            The singleton instance of the class 'cls'.

        Raises
        ------
        exc.ConfigurationError
            Raised if an instance of 'cls' has already been created and initialized.

        """
        if cls.__instance is not None:
            raise exc.ConfigurationError(
                f"Class '{cls.__qualname__}' has already been initialized.",
            )

        cls.__instance = super().__new__(Config)
        cls.__instance.__init__(*args, **kwargs)  # type: ignore[misc]

        return cls.__instance

    @functools.cache
    def __getattr__(self, name: str) -> Any:
        """Get the value of the attribute from the configuration.

        Parameters
        ----------
        name : str
            The name of the attribute containing the configuration value.

        Returns
        -------
        Any
            The value stored in the configuration attribute or a 'ConfigProxy' if the attribute
            represents a subnamespace.

        Raises
        ------
        AttributeError
            Raised if the attribute was not configured.

        """
        if (name,) in self._valid_attrs:
            return ConfigProxy(self._config, self._valid_attrs, name)

        raise AttributeError(f"'Config' object has no attribute '{name}'")

    @classmethod
    def get(cls, name: str, namespace: str, *, required: bool = False, default: Any = ...) -> Any:
        """Get the value of the attribute from the namespace.

        Parameters
        ----------
        name : str
            The name of the attribute containing the configuration value.
        namespace : str
            The namespace in which the configuration value is stored.
        required : bool, optional
            Whether or not to raise 'AttributeError' if the value is not found; by default False.
        default : Any, optional
            An optional default value to return if the configuration value is not found.

        Returns
        -------
        Any
            The requested configuration value or None if the value is not required and no default
            value is specified.

        Raises
        ------
        AttributeError
            Raised if the value is required, is not configured, and has no default value.

        """
        self = cls()

        qualified_name: tuple[str, ...] = self._get_qualified_name(name, namespace)

        if qualified_name not in self._config:
            if default is not ...:
                return default

            if required:
                raise AttributeError(name=".".join(qualified_name), obj=self)

            return None

        return self._config[qualified_name]

    @classmethod
    def register[
        T
    ](
        cls,
        name: str,
        namespace: str,
        *,
        env: str | None = None,
        parser: ConfigProcessor[T] | None = None,
        required: bool = False,
    ) -> None:
        """Register a new configuration attribute.

        Parameters
        ----------
        name : str
            The name of the attribute in the configuration file that stores the configuration value.
        namespace : str | None, optional
            The namespace in the configuration file to look for the configuration value.
        env : str | None, optional
            An environment variable that can be used to supply the configuration value.
            Environment variables take precedence over values in a configuration file.
        parser : ConfigProcessor[T] | None, optional
            A parser to be used to convert the value from a 'ConfigValue' to 'T'.
        required : bool, optional
            Whether or not to throw an error if the configuration value cannot be found.

        Raises
        ------
        RequiredValueError
            Raised if the configuration has already been initialized and the configuration value is
            not configured.

        """
        qualified_name: tuple[str, ...] = cls._get_qualified_name(name, namespace)

        if qualified_name in cls._registry:
            return

        attr = _ConfigAttribute(
            qualified_name=qualified_name,
            env=env,
            parser=parser,
            required=required,
        )
        cls._registry[qualified_name] = attr

        if cls._initialized:
            self = cls()

            if env is not None and env in os.environ:
                self._process_env_var(attr)
            elif qualified_name in self._config and parser:
                self._config[qualified_name] = self._freeze(parser(self._config[qualified_name]))

            if required and qualified_name not in self._config:
                raise RequiredValueError(name=attr.name, namespace=attr.namespace)

    @property
    def version(self) -> str:
        """Get the version of the application."""
        return __version__

    def _process_env(self) -> None:
        """Process environment variables."""
        dotenv.load_dotenv()

        for attr in self._registry.values():
            self._process_env_var(attr)

    def _process_env_var(self, attr: _ConfigAttribute[Any]) -> None:
        """Process a '_ConfigAttribute' if it has an environment variable.

        Parameters
        ----------
        attr : _ConfigAttribute[Any]
            The '_ConfigAttribute' to process.

        """
        if attr.qualified_name in self._config:
            return

        if attr.env and attr.env in os.environ:
            value = os.environ[attr.env]

            if attr.parser:
                value = attr.parser(value)

            self._config[attr.qualified_name] = self._freeze(value)

            for i, _ in enumerate(attr.qualified_name):
                self._valid_attrs.add(tuple(attr.qualified_name[: i + 1]))

    def _load_file(self, config_file: pathlib.Path | str) -> dict[str, Any]:
        """Load a TOML configuration file.

        Parameters
        ----------
        config_file : pathlib.Path | str
            The path to the TOML configuration file.

        Returns
        -------
        dict[str, Any]
            The fully processed configuration data.

        Raises
        ------
        ConfigurationError
            Raised if the file cannot be found.

        """
        if isinstance(config_file, str):
            config_file = pathlib.Path(config_file)

        try:
            with config_file.open("rb") as fp:
                return tomllib.load(fp)
        except FileNotFoundError as e:
            raise ConfigurationError(str(e)) from e

    def _process(self, value: Any, *namespace: str) -> None:
        """Process a value from the configuration file.

        Parameters
        ----------
        value : Any
            The value from the configuration file.
        namespace : tuple[str]
            The namespace in which the value is stored.

        """
        for i in range(1, len(namespace) + 1):
            self._valid_attrs.add(tuple(namespace[:i]))

        if namespace in self._config:
            _log.warning(
                "Value already exists for configuration value. Not overwriting it.",
                namespace=namespace,
                value=self._config[namespace],
            )
            return

        if (attr := self._registry.get(namespace, None)) and attr.parser:
            self._config[namespace] = self._freeze(attr.parser(value))
            return

        if isinstance(value, dict):
            for k, v in value.items():
                self._process(v, *namespace, k)

            return

        self._config[namespace] = self._freeze(value)

    def _freeze(self, value: Any) -> Any:
        """Get a nested read-only version of the value.

        Parameters
        ----------
        value : Any
            The value to freeze.

        Returns
        -------
        Any
            A frozen version of the value or the original value.

        """
        match type(value):
            case builtins.dict:
                value_copy = value.copy()
                for k, v in value.items():
                    value_copy[k] = self._freeze(v)
                return types.MappingProxyType(value_copy)
            case builtins.list | builtins.set | builtins.tuple:
                return tuple(self._freeze(v) for v in value)
            case _:
                return value

    @staticmethod
    def _get_qualified_name(name: str, namespace: str) -> tuple[str, ...]:
        """Get the tuple of the namespace and name that represents a configuration value.

        Parameters
        ----------
        name : str
            The name of the attribute containing the configuration value.
        namespace : str
            The namespace in which the configuration value is stored.

        Returns
        -------
        tuple[str, ...]
            A tuple representing a fully qualified configuration attribute.

        Raises
        ------
        ValueError
            Raised if 'name' or 'namespace' are empty strings.

        """
        if not name:
            raise ValueError("`name` must be a non-empty string.")

        if not namespace:
            raise ValueError("`namespace` must be a non-empty string")

        name = name.strip()
        return (*namespace.split("."), name)


def __getattr__(name: str) -> Any:
    """Get the value of the attribute from the configuration singleton.

    Parameters
    ----------
    name : str
        The name of the attribute containing the configuration value.

    Returns
    -------
    Any
        The value stored in the configuration attribute or a 'ConfigProxy' if the attribute
        represents a subnamespace.

    Raises
    ------
    AttributeError
        Raised if the attribute was not configured.

    """
    if name in globals():
        return globals()[name]

    self = Config()

    if (name,) in self._valid_attrs:
        return ConfigProxy(self._config, self._valid_attrs, name)

    raise AttributeError(f"'Config' object has no attribute '{name}'")


#: An alias for 'Config.init'
init = Config.init

#: An alias for 'Config.register'
register = Config.register

#: An alias for 'Config.get'
get = Config.get
