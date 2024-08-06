"""Sets up the translation for the bot and exports the configured `gettext` function."""

from __future__ import annotations

import gettext as gettext_
import pathlib
import typing

from alfred import __project_package__

if typing.TYPE_CHECKING:
    from collections.abc import Callable

__all__ = (
    "bind",
    "gettext",
)

#: The translation domain for the application.
_DOMAIN = __project_package__

#: The folder which holds locale data.
_LOCALE_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parent / "locale"

#: The configured translation function to be imported by other modules.
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
