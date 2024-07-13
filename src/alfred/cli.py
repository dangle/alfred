"""The entry point of the bot.

Defines the CLI interface of the bot, configures the bot, and then runs it.
"""

import argparse
import os
import sys

import structlog

from . import __project_package__, bot
from . import exceptions as exc
from . import logging, translation
from .config import CommandLineFlag, config
from .features import features
from .translation import gettext as _

__all__ = ("run",)

config(
    "version",
    flag=CommandLineFlag(
        name="--version",
        short="-v",
        action="version",
        version=f"{config.bot_name} {config.version}",
    ),
)


def run() -> None:
    """Configure and start the program."""

    translation.bind()
    logging.configure_logging()

    log: structlog.stdlib.BoundLogger = structlog.get_logger()

    try:
        with logging.delay_logging():
            features.discover_features()
            config.load(
                prog=__project_package__,
                description=_(
                    "{project_name} is an extensible Discord bot that can use ChatGPT to respond"
                    " conversationally and run commands on behalf of the server users."
                ).format(project_name=config.bot_name),
                formatter_class=argparse.RawTextHelpFormatter,
            )
        bot.run()
    except exc.BotException as e:
        log.error(str(e))
        sys.exit(e.exit_code)
    except Exception as e:
        log.error(str(e), exc_info=e)
        sys.exit(os.EX_SOFTWARE)


if __name__ == "__main__":
    run()
