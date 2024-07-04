"""Sets up the translation for the bot and exports the configured `gettext` function.

Attributes
----------
gettext : Callable[[str], str]
    A function that can will retrieve translated versions of the string given to it.
"""

import gettext as gettext_
import pathlib
from typing import Callable

__all__ = ("gettext",)

_DOMAIN = "alfred"
_localedir: pathlib.Path = pathlib.Path(__file__).resolve().parent / "locale"

gettext: Callable[[str], str] = gettext_.translation(
    domain=_DOMAIN,
    localedir=str(_localedir),
    fallback=True,
).gettext
