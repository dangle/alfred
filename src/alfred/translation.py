"""Sets up the translation for the bot and exports the configured `gettext` function."""

import gettext as gettext_
import pathlib
from typing import Callable

from . import __project_package__

__all__ = ("gettext",)

_DOMAIN = __project_package__
_localedir: pathlib.Path = pathlib.Path(__file__).resolve().parent / "locale"

_gettext: Callable[[str], str] = gettext_.translation(
    domain=_DOMAIN,
    localedir=str(_localedir),
    fallback=True,
).gettext


def gettext(value: str) -> str:
    """Return a translated version of `value`.

    Parameters
    ----------
    value : str
        The value to translate.
    """

    return _gettext(value)
