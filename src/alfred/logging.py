"""Contains functions necessary for configuring logging to be both structured and standardized."""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import typing
import uuid
from abc import abstractmethod

import structlog

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from typing import Any

    from structlog.typing import EventDict, Processor, ProcessorReturnValue, WrappedLogger

__all__ = (
    "Canonical",
    "canonical",
    "configure_logging",
    "delay_logging",
    "register_canonical_type",
)

_canonical_registry: dict[type, Callable[[Any], dict[str, Any]]] = {}


@typing.runtime_checkable
class Canonical(typing.Protocol):
    """A protocol that allows events to log a canonical form of any objects that implement it."""

    @property
    @abstractmethod
    def __canonical__(self) -> dict[str, Any]:
        """Get a dict to use when logging this object..

        Returns
        -------
        dict[str, Any]
            A dictionary representation of this object to be used when logging.

        """


def canonical(obj: Canonical) -> dict[str, Any]:
    """Get the canonical dict of an object.

    Parameters
    ----------
    obj : Canonical
        The object of which to get the canonical dictionary.

    Returns
    -------
    dict[str, Any]
        The canonical dictionary of the object.

    Raises
    ------
    TypeError
        Raised if 'obj' does not implement the 'Canonical' protocol.

    """
    cls: type = type(obj)

    if hasattr(cls, "__canonical__"):
        return obj.__canonical__

    if cls in _canonical_registry:
        return _canonical_registry[cls](obj)

    for registered_cls, func in _canonical_registry.items():
        if issubclass(cls, registered_cls):
            return func(obj)

    raise TypeError(f"Expected an object implementing 'Canonical' protocol; got type '{cls}'")


def register_canonical_type(cls: type, func: Callable[[Any], dict[str, Any]]) -> None:
    """Register a virtual subclass of 'Canonical' with a method for getting the canonical dict.

    Parameters
    ----------
    cls : type
        The class to register as a virtual subclass of 'Canonical'.
    func : Callable[[Any], dict[str, Any]]
        The function that 'canonical' will call when converting this type to a dict.

    """
    Canonical.register(cls)
    _canonical_registry[cls] = func


register_canonical_type(dict, lambda x: x)


def canonical_event(func: Callable[..., Any], **extras: Any) -> Callable[..., Any]:
    """Add a canonical logger to a given function.

    This wrapper will extract 'Feature' and 'discord.Message' objects so that they can be displayed
    in a useful manner in the canonical log line.

    Returns
    -------
    Callable[..., Any]
        Returns a wrapper function around 'func' that emits a canonical log line once the function
        has completed.

    """

    @functools.wraps(func)
    async def wrapper[**P](*args: P.args, **kwargs: P.kwargs) -> Any:
        logged_args: dict[int, Any] = dict(enumerate(args))

        if "trace_id" not in structlog.contextvars.get_contextvars():
            extras["trace_id"] = str(uuid.uuid4())

        for i, arg in enumerate(args):
            if isinstance(arg, Canonical):
                extras.update(canonical(arg))
                del logged_args[i]

        if logged_args:
            extras["args"] = list(logged_args.values())

        with structlog.contextvars.bound_contextvars(
            **extras,
            **kwargs,
        ):
            try:
                return await func(*args, **kwargs)
            finally:
                structlog.get_logger().info("canonical-log-line")

    return wrapper


def _rename_event_key(
    _: WrappedLogger,
    __: str,
    event_dict: EventDict,
) -> ProcessorReturnValue:
    """Rename the `event` key of the `event_dict` to `message`.

    Parameters
    ----------
    _: WrappedLogger
        Unused.
    __: str
        Unused.
    event_dict: EventDict
        The structlog event dictionary representing the current status of the logging object
        that will be output once processing has completed.

    Returns
    -------
    ProcessorReturnValue
        `event_dict` modified by renaming the `event` key to `message`.

    """
    event_dict["message"] = event_dict.pop("event")
    return event_dict


def _remove_locals(
    _: WrappedLogger,
    __: str,
    event_dict: EventDict,
) -> ProcessorReturnValue:
    """Remove local variables from exception frames in logs.

    Parameters
    ----------
    _: WrappedLogger
        Unused.
    __: str
        Unused.
    event_dict: EventDict
        The structlog event dictionary representing the current status of the logging object
        that will be output once processing has completed.

    Returns
    -------
    ProcessorReturnValue
        `event_dict` modified by removing locals from any exception frames.

    """
    for exception in event_dict.get("exception", {}):
        for frame in exception["frames"]:
            if hasattr(frame, "locals"):
                del frame["locals"]

    return event_dict


#: Common logging processors for both structlog and logging
_SHARED_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
    structlog.processors.CallsiteParameterAdder(
        [
            structlog.processors.CallsiteParameter.FILENAME,
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        ],
    ),
    _rename_event_key,
    structlog.processors.dict_tracebacks,
]


def configure_logging(min_level: int | None = None) -> None:
    """Configure the both logging and structlog to output structured JSON logs.

    This method configures the structlog logging module to output JSON and add several common log
    attributes.

    It also configures the logging module to match the structlog output and to raise PythonWarnings
    as exceptions instead of printing out to the console directly.

    Additionally, this renames the `event` key in the final JSON to `message`.

    Parameters
    ----------
    min_level : int, optional
        The minimum logging level to log.
        These are values from the stdlib logging package, such as `logging.INFO`.

    """
    structlog.reset_defaults()

    if min_level is None:
        requested_log_level: str = os.getenv("LOG_LEVEL", "INFO")
        min_level = getattr(logging, requested_log_level, logging.INFO)

    shared_processors = _SHARED_PROCESSORS

    if min_level > logging.DEBUG:
        shared_processors = [*_SHARED_PROCESSORS, _remove_locals]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()

    while root_logger.hasHandlers():
        root_logger.removeHandler(root_logger.handlers[0])

    root_logger.addHandler(handler)
    root_logger.setLevel(min_level)
    logging.captureWarnings(capture=True)


class LogCapture:
    """Class for capturing log messages in its entries list.

    Attributes
    ----------
    entries : A list of processed `EventDict` objects.

    See Also
    --------
    structlog.testing.LogCapture :
        This object was based on `structlog.testing.LogCapture`. It is not possible to use
        `structlog.testing.LogCapture` in place of this because `structlog.testing.LogCapture`
        alters `event_dict` with the assumption that it is the only processor.

    """

    def __init__(self) -> None:
        self.entries: list[Any] = []

    def __call__(
        self,
        _: WrappedLogger,
        __: str,
        event_dict: EventDict,
    ) -> ProcessorReturnValue:
        """Append the `event_dict` to `self.entries` and drop the event.

        Parameters
        ----------
        _: WrappedLogger
            Unused.
        __: str
            Unused.
        event_dict: EventDict
            The structlog event dictionary representing the current status of the logging object
            that would have been output had the event not been dropped.

        """
        self.entries.append(event_dict)
        raise structlog.DropEvent


@contextlib.contextmanager
def delay_logging() -> Generator[None, None, None]:
    """Capture all logs and log them out when the context manager exits."""
    processors: list[Processor] = structlog.get_config()["processors"].copy()
    min_level: int = logging.getLogger().level

    capture: LogCapture = LogCapture()
    structlog.configure(processors=[*processors, capture])
    is_exiting = False

    try:
        yield
    except SystemExit:
        is_exiting = True
        raise
    finally:
        if not is_exiting:
            configure_logging(min_level)
            logger = structlog.get_logger().bind()._logger  # noqa: SLF001

            for ed in capture.entries:
                level_name: str = ed[0][0]["level"]
                getattr(logger, level_name)(*ed[0], **ed[1])
