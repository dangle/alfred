"""Constants used by the chat feature."""

from __future__ import annotations

from alfred.translation import gettext as _

__all__ = (
    "MAX_REPLY_LEN",
    "NO_RESPONSE",
    "NO_RESPONSE_SYSTEM_MESSAGE",
    "RETRY_BAD_RESPONSES",
    "TIME_TO_WAIT_FOR_CORRECTIONS_S",
    "TOOL_SYSTEM_MESSAGE",
)

#: The maximum length of a Discord message.
MAX_REPLY_LEN: int = 2000

#: A custom response that the bot may return if it does not believe that it is being addressed.
NO_RESPONSE: str = "__NO_RESPONSE__"

#: The number of times to retry bad responses from the chat service.
RETRY_BAD_RESPONSES: int = 3

#: How long to listen for corrections from the user without requiring an explicit mention, in
#: seconds.
TIME_TO_WAIT_FOR_CORRECTIONS_S: int = 60

#: A command to add to the system message that allows the bot to not respond.
NO_RESPONSE_SYSTEM_MESSAGE: str = _(
    "If you do not believe a message is intended for you, respond with: {response}\n",
).format(response=NO_RESPONSE)

#: A command to add to the system message that helps the bot use tools more naturally.
TOOL_SYSTEM_MESSAGE: str = _(
    "If multiple functions could be returned, pick one instead of asking which function to"
    " use.\n"
    "If you are get a file from a function, do *NOT* try to embed it with  markdown syntax.\n",
)
