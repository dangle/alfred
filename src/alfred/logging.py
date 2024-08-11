"""Contains functions necessary for configuring logging to be both structured and standardized."""

from __future__ import annotations

import contextlib
import logging
import os
import typing

import structlog

if typing.TYPE_CHECKING:
    from collections.abc import Generator
    from typing import Any

    from structlog.typing import EventDict, Processor, ProcessorReturnValue, WrappedLogger

__all__ = (
    "configure_logging",
    "delay_logging",
)


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
    """Caputre all logs and log them out when the context manager exits."""
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
