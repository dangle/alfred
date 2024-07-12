"""Common methods for managing AI between multiple features."""

import openai

from ..config import CommandLineFlag, EnvironmentVariable, config
from ..translation import gettext as _

__all__ = ("configure_ai",)


def configure_ai(required: bool = False) -> None:
    """Configure the OpenAI client.

    Parameters
    ----------
    required : bool, optional
        If required, the program will exit if the OpenAI client cannot be configured.
        The default is `False`.
    """

    config(  # Create the OpenAI client if it has been configured.
        "ai",
        env=EnvironmentVariable(name="OPENAI_API_KEY", type=lambda _: openai.OpenAI()),
        flag=CommandLineFlag(
            name="--openai-api-key",
            metavar="OPENAI_API_KEY",
            help=_(
                "The OpenAI API key for authenticating to the your OpenAI project.\n"
                "This is necessary if you want Alfred to parse messages to run commands and respond"
                "conversationally.\n"
                "If not supplied, Alfred will also look for the OPENAI_API_KEY environment"
                " variable."
            ),
            type=lambda _: openai.OpenAI(),
        ),
        required=required,
    )
