"""Utilities for automatically attaching fields on annotated classes."""

from __future__ import annotations

import inspect
import typing

__all__ = ("AutoFields",)

_AnnotatedType: type = type(typing.Annotated[typing.Any, typing.Any])


class AutoFields:
    """A base class for classes to automatically use fields based on type annotations."""

    _field_registry: typing.ClassVar[dict[str, type]] = {}

    def __init_subclass__(cls) -> None:
        """Set fields for annotated class variables."""
        super().__init_subclass__()

        for attr, value in inspect.get_annotations(cls, eval_str=True).items():
            match typing.get_origin(value):
                case typing.ClassVar:
                    val = typing.get_args(value)[0]
                case _:
                    val = value

            match val:
                case _AnnotatedType():  # type: ignore[misc]
                    typ, field = typing.get_args(val)
                case type():
                    fq_name: str = f"{val.__module__}.{val.__qualname__}"
                    field_type = cls.get_field_by_annotation(fq_name)

                    if not field_type:
                        continue

                    typ = t[0] if (t := typing.get_args(field_type)) else str
                    field = field_type()
                case _:
                    continue

            # Imply arguments for 'ConfigField' from annotations.
            if hasattr(field, "default") and field.default is ... and attr in vars(cls):
                field.default = vars(cls)[attr]

            if hasattr(field, "parser") and field.parser is ...:
                field.parser = typ

            # Attach the field to the class
            if hasattr(field, "__set_name__"):
                field.__set_name__(cls, attr)

            setattr(cls, attr, field)

    @classmethod
    def register_field_to_annotation(cls, annotation: str, field: type) -> None:
        """Use the registered field to replace class attributes with the given annotation.

        This will not replace class attributes if they are assigned a value.

        Parameters
        ----------
        annotation : str
            The annotation to look for on new objects.
        field : type
            The field to assign to the class attribute

        """
        cls._field_registry[annotation] = field

    @classmethod
    def get_field_by_annotation(cls, annotation: str) -> type | None:
        """Get a registered field for an annotation.

        Parameters
        ----------
        annotation : str
            An annotation attached to a class attribute to be replaced with a field.

        Returns
        -------
        type | None
            If a field type has been registered for the given annotation, that field type will be
            returned.

        """
        return cls._field_registry.get(annotation, None)
