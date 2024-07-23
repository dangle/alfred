"""A REST API for interacting with the 'Manor'."""

from __future__ import annotations

import typing
from pathlib import Path

from fastapi import FastAPI
from hypercorn import Config
from hypercorn.asyncio import serve as serve_

if typing.TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

    from hypercorn.typing import Framework

__all__ = (
    "app",
    "serve",
)

app = FastAPI()


async def serve(path: Path | str | None = None, **extra: Any) -> Coroutine[Any, Any, None]:
    """Start a REST API server for interacting with 'Manor'.

    Parameters
    ----------
    path : Path | str | None, optional
        The path to the configuration file to be used by hypercorn, by default None
    extra : Any
        Extra objects to be stored on the ASGI app object that can be accessed by routes.

    Returns
    -------
    Coroutine[Any, Any, None]
        A coroutine that runs the HTTP server.

    """
    app.extra = extra
    config = Config()

    if isinstance(path, str):
        path = Path(path)

    if path and path.is_file():
        config.from_toml(str(path))

    return serve_(typing.cast(Framework, app), config)
