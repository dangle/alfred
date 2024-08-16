"""Custom chat activities to use when setting bot presence."""

from __future__ import annotations

import discord

from alfred.translation import gettext as _

__all__ = (
    "WAITING_FOR_CORRECTIONS",
    "THINKING",
)

WAITING_FOR_CORRECTIONS = discord.CustomActivity(_("Waiting for corrections"))

THINKING = discord.CustomActivity(_("Thinking"))
