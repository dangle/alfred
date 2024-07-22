"""Sets up the translation for the bot and exports the configured `gettext` function."""

import gettext as gettext_
import pathlib
from collections.abc import Callable

from alfred import __project_package__

__all__ = (
    "bind",
    "gettext",
)

_DOMAIN = __project_package__
_LOCALE_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parent / "locale"

_gettext: Callable[[str], str] = gettext_.translation(
    domain=_DOMAIN,
    localedir=str(_LOCALE_DIR),
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


def bind() -> None:
    """Bind the global `gettext` domain to the bot domain."""
    if gettext_.find(_DOMAIN, localedir=_LOCALE_DIR):
        gettext_.bindtextdomain(_DOMAIN, _LOCALE_DIR)
        gettext_.textdomain(_DOMAIN)
