"""Defines the CLI interface of the bot, configures the bot, and then runs it."""

from __future__ import annotations

import os
import sys

import structlog
import uvloop

from alfred import config, logging, translation
from alfred import exceptions as exc
from alfred.manor import Manor

__all__ = ("main",)


def main() -> None:
    """Configure and run the program."""
    translation.bind()
    logging.configure_logging()

    log: structlog.stdlib.BoundLogger = structlog.get_logger()

    try:
        config.init()
        uvloop.run(Manor().start())
    except KeyboardInterrupt:
        sys.exit(os.EX_OK)
    except exc.BotError as e:
        log.error(str(e))
        sys.exit(e.exit_code)
    except Exception as e:
        log.error(str(e), exc_info=e)
        sys.exit(os.EX_SOFTWARE)


if __name__ == "__main__":
    main()
