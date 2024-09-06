"""Features for managing user requests."""

from __future__ import annotations

from alfred.core import feature

__all__ = ("request_command",)

#: The command group all commands in this feature must be under.
request_command = feature.CommandGroup("request", "Commands for requesting media.")
