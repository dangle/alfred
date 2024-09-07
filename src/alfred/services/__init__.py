"""Functionality for running the project as a service."""

from __future__ import annotations

import os
import typing

import structlog
import uvloop

from alfred.core import config
from alfred.core import exceptions as exc
from alfred.services.manor import Manor
from alfred.util import logging, translation

if typing.TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "api",
    "manor",
    "run",
)


def run(config_file: Path | str | None = None) -> int:
    """Create a new 'Manor' and run it in an event loop and handle any errors.

    Parameters
    ----------
    config_file : Path | str | None
        An optional path to a configuration TOML file.
        Defaults to None.

    Returns
    -------
    int
        An exit code to use for command-line programs.

    """
    translation.bind()
    logging.configure_logging()

    log: structlog.stdlib.BoundLogger = structlog.get_logger()

    try:
        config.init(config_file)
        uvloop.run(Manor(config_file).start())
    except KeyboardInterrupt:
        pass
    except exc.BotError as e:
        log.error(str(e))
        return e.exit_code or os.EX_SOFTWARE
    except Exception as e:
        log.error(str(e), exc_info=e)
        return os.EX_SOFTWARE

    return os.EX_OK
