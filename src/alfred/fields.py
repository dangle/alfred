"""Descriptor fields to help develop bot features."""

from __future__ import annotations

import builtins
import pathlib
import sys
import types
import typing
from abc import ABC, abstractmethod
from typing import Any

import openai

from alfred import config, feature

if typing.TYPE_CHECKING:
    from types import EllipsisType

    from alfred.typing import ConfigProcessor

__all__ = (
    "AIField",
    "BotField",
    "BoundedConfigField",
    "ConfigField",
    "ExtrasField",
    "FeatureField",
    "ManorField",
    "StaffField",
)


class ConfigField[T]:
    """A descriptor that returns a value from the global configuration.

    Parameters
    ----------
    name : str | None, optional
        The name of the attribute in the configuration file that stores the configuration value.
        By default, the name of the variable to which the descriptor is attached will be the name.
    namespace : str | None, optional
        The namespace in the configuration file to look for the configuration value.
        By default, it will use the qualified name of the module in which the field is being used.
    env : str | None, optional
        An environment variable that can be used to supply the configuration value.
        Environment variables take precedence over values in a configuration file.
    parser : ConfigProcessor[T] | None, optional
        A parser to be used to convert the value from a 'ConfigValue' to 'T'.
    required : bool, optional
        Whether or not to throw an error if the configuration value cannot be found.
    default : Any, optional
        The value to return if the value is not configured.

    """

    __slots__ = (
        "_storage_name",
        "_namespace",
        "_key",
        "_env",
        "_parser",
        "_required",
        "_default",
    )

    def __init__(
        self,
        *,
        name: str | None = None,
        namespace: str | None = None,
        env: str | None = None,
        parser: ConfigProcessor[T] | None = None,
        required: bool = False,
        default: Any = ...,
    ) -> None:
        self._storage_name: str
        self._namespace: str | None = namespace
        self._key: str | None = name
        self._env: str | None = env
        self._parser: ConfigProcessor[T] | None = parser
        self._required: bool = required
        self._default: Any = default

    def __set_name__(self, owner: type, name: str) -> None:
        """Set the name of the descriptor.

        This registers the configuration value and forces the 'Config' to update values using either
        the environment variable and/or the parser.

        Parameters
        ----------
        owner : type
            The class in which the descriptor was used.
        name : str
            The name of the attribute to which the descriptor was assigned.
            This is used to store the value on individual instances.

        """
        self._storage_name = self._key or name
        self._namespace = self._namespace or self._get_module_name(owner)

        config.register(
            self._storage_name,
            self._namespace,
            env=self._env,
            parser=self._parser,
            required=self._required,
        )

    def __get__(self, instance: Any, owner: type) -> T:
        """Get the configuration value for a specific instance.

        Parameters
        ----------
        instance : Any
            The instance on which to store the configuration value.
        owner : type
            The class in which the descriptor was used.

        Returns
        -------
        T
            The configuration value of type 'T'.

        """
        if self._storage_name not in vars(instance):
            namespace: str = typing.cast(str, self._namespace)
            vars(instance)[self._storage_name] = config.get(
                self._storage_name,
                namespace,
                default=self._default,
            )

        return vars(instance)[self._storage_name]

    def _get_module_name(self, owner: type) -> str:
        """Get the module name of a type.

        Parameters
        ----------
        owner : type
            The class in which the descriptor was used.

        Returns
        -------
        str
            The qualified name of the module in which the 'owner' class was defined.

        """
        name = owner.__module__

        if name != "__main__":
            return name

        filename = typing.cast(str, sys.modules[name].__file__)
        file = pathlib.Path(filename)
        return file.resolve().stem


class _BoundedType[T](type):
    """A metaclass that allows a 'type' to be sliced."""

    def __getitem__(cls: _BoundedType[T], bounds: T | slice | tuple[slice, ...]) -> _BoundedType[T]:
        """Return a subclass of the sliced type with a '_bounds' class variable to hold the slice.

        Parameters
        ----------
        cls : _BoundedType[T]
            The sliced class.
        bounds : T | slice | tuple[slice, ...]
            The bounds, represented as value of type 'T', a slice, or a tuple of slices to store on
            the new subclass.

        Returns
        -------
        _BoundedType[T]
            A new subclass of 'cls' that stores the bounds of the class.

        """

        class Bounded(cls):  # type: ignore[misc, valid-type]
            _bounds: T | slice | tuple[slice, ...] | EllipsisType = ...

        Bounded._bounds = bounds  # noqa: SLF001
        Bounded.__name__ = cls.__name__
        Bounded.__qualname__ = cls.__qualname__
        Bounded.__module__ = cls.__module__
        Bounded.__doc__ = cls.__doc__
        Bounded.__annotations__ = cls.__annotations__

        return typing.cast(_BoundedType[T], Bounded)


class BoundedConfigField[T](ConfigField[T], metaclass=_BoundedType):
    """A descriptor that gets a configuration value and validates it using bounds."""

    #: The bounds to use when validating the configuration value.
    _bounds: T | slice | tuple[slice, ...] | EllipsisType = ...

    def __get__(self, instance: Any, owner: type) -> T:
        """Get the configuration value for a specific instance and validate it.

        Parameters
        ----------
        instance : Any
            The instance on which to store the configuration value.
        owner : type
            The class in which the descriptor was used.

        Returns
        -------
        T
            The configuration value of type 'T'.

        Raises
        ------
        ValueError
            Raised if the configuration value is not within the specified bounds.

        """
        value: T = super().__get__(instance, owner)

        match type(self._bounds):
            case types.EllipsisType:
                pass
            case builtins.slice:
                bounds: slice = typing.cast(slice, self._bounds)

                if bounds.start is not None and value < bounds.start:
                    raise ValueError(
                        f"'{owner.__qualname__}.{self._storage_name}'{value!r} which is below the "
                        f"minimum value of {bounds.start!r}",
                    )

                if bounds.stop is not None and value > bounds.stop:
                    raise ValueError(
                        f"'{owner.__qualname__}.{self._storage_name}' has value {value!r} which is "
                        f"above the maximum value of {bounds.start!r}",
                    )
            case _:
                if value != self._bounds:
                    raise ValueError(
                        f"'{owner.__qualname__}.{self._storage_name}' has value {value!r} but is "
                        f"required to be {self._bounds!r}",
                    )

        return value


class AIField(ConfigField[openai.AsyncOpenAI]):
    """A field that returns an 'openai.AsyncOpenAI' client.

    This looks for the 'alfred.openai.openai_api_key' configuration value and uses it to create a
    new 'openai.AsyncOpenAI' client.

    Every instance used by this descriptor will have a separte client.

    Parameters
    ----------
    required : bool, optional
        Whether or not to throw an error if the configuration value cannot be found.

    """

    def __init__(self, *, required: bool = False) -> None:
        super().__init__(
            name="openai_api_key",
            namespace="alfred.openai",
            env="OPENAI_API_KEY",
            required=required,
        )

    def __get__(self, instance: Any, owner: type) -> openai.AsyncOpenAI:
        """Get the 'openai.AsyncOpenAI' client for the specified instance.

        Parameters
        ----------
        instance : Any
            The instance on which to store the OpenAI client.
        owner : type
            The class in which the descriptor was used.

        Returns
        -------
        openai.AsyncOpenAI
            An asynchronous OpenAI client.

        """
        if self._storage_name not in vars(instance):
            vars(instance)[self._storage_name] = openai.AsyncOpenAI(
                api_key=config.alfred.openai.openai_api_key,
            )

        return vars(instance)[self._storage_name]


class FeatureField(ABC):
    """A base class for accessing attributes on a 'feature.Feature'."""

    def __set_name__(self, owner: type[feature.Feature], name: str) -> None:
        """Store the name of the class attribute used for the descriptor.

        Parameters
        ----------
        owner : type[feature.Feature]
            The class on which the descriptor was used.
        name : str
            The name of the attribute used on 'owner' for the descriptor.

        Raises
        ------
        TypeError
            Raised if the 'owner' is not a 'feature.Feature' class.

        """
        if not issubclass(owner, feature.Feature):
            raise TypeError("'{owner.__name__}' can only be used in 'feature.Feature' objects")

        self._storage_name = name

    @abstractmethod
    def __get__(self, instance: feature.Feature, _: type[feature.Feature]) -> Any:
        """Get a value from a 'feature.Feature' instance.

        This must be overridden by subclasses.

        Parameters
        ----------
        instance : feature.Feature
            The 'feature.Feature' instance.
        _ : type[feature.Feature]
            The 'feature.Feature' class on which the descriptor was used.

        Returns
        -------
        Any
            The value to be returned by subclasses.

        """


class ExtrasField(FeatureField):
    """A field used for accessing any extra data stored when a 'feature.Feature' is created.

    Parameters
    ----------
    name : str | None, optional
        The name of the value to retrieve from the extras data on the 'feature.Feature'.
        If not supplied, it will attempt to retrieve data using the name of the attribute to which
        this descriptor was assigned in the 'feature.Feature'.

    """

    def __init__(self, name: str | None = None) -> None:
        self._extras_name = name

    def __get__(self, instance: feature.Feature, _: type[feature.Feature]) -> Any:
        """Get the value from the 'feature.Feature' extras on the instance.

        Parameters
        ----------
        instance : feature.Feature
            The 'feature.Feature' that has the extra data.
        _ : type[feature.Feature]
            The 'feature.Feature' class on which the descriptor was used.

        Returns
        -------
        Any
            The value stored in the 'feature.Feature' extras.

        Raises
        ------
        KeyError
            Raised if the attribute does not exist in the 'feature.Feature' extras.

        """
        return instance._Feature__extras[self._extras_name or self._storage_name]  # noqa: SLF001


class BotField(ExtrasField):
    """Sets the bot attribute from the 'feature.Feature' extras."""

    def __init__(self) -> None:
        super().__init__("bot")


class ManorField(ExtrasField):
    """Sets the manor attribute from the 'feature.Feature' extras."""

    def __init__(self) -> None:
        super().__init__("manor")


class StaffField(ExtrasField):
    """Sets the staff attribute from the 'feature.Feature' extras."""

    def __init__(self) -> None:
        super().__init__("staff")