"""Contains the management interface for staff members."""

from __future__ import annotations  # noqa: I001

import asyncio
import os
import typing
import warnings
from typing import Annotated

import structlog
import uvloop
from tortoise import Tortoise, transactions

from alfred.core import config
from alfred.core import exceptions as exc
from alfred.core import feature, fields, models
from alfred.services import api
from alfred.util import logging, translation
from alfred.util.autofields import AutoFields
from alfred.util.logging import Canonical
from alfred.util.translation import gettext as _

if typing.TYPE_CHECKING:
    import uuid
    from pathlib import Path
    from typing import Any

    from discord import Intents

    from alfred.core.feature import FeatureRef


__all__ = ("Manor",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

#: Database URL for an in-memory SQLite3 database.
_IN_MEMORY_DB_URL: str = "sqlite://:memory:"

#: Default namespace for program configuration settings.
_ALFRED_NAMESPACE: str = "alfred"

#: Default namespace for database settings for the program.
_DB_NAMESPACE: str = f"{_ALFRED_NAMESPACE}.db"

#: Default namespace for Discord settings for the program.
_DISCORD_NAMESPACE: str = f"{_ALFRED_NAMESPACE}.discord"

#: Default name to which an ephemeral bot will respond.
_DEFAULT_NAME: str = _("Alfred")

#: Default description
_DEFAULT_DESCRIPTION_TEMPLATE: str = _(
    "You are a helpful and formal butler listening in to a chat server.\n"
    "Your name is {nick}.\n"
    "You will respond to the name {name}.\n"
    "You will respond using honorifics.\n"
    "Do not ask follow-up questions.\n",
)


class Manor(Canonical, AutoFields):
    """A class for creating and managing staff members (aka Discord bots).

    Parameters
    ----------
    config_file : Path | str | None
        The path to the configuration file to use when setting up hypercorn for the API.

    """

    #: The features that staff members may be configured to use.
    #: If unconfigured, staff members will be able to use all available features.
    enabled_features: Annotated[
        tuple[str, ...],
        fields.CSVConfigField[str](
            name="features",
            namespace=_ALFRED_NAMESPACE,
            env="ALFRED_ENABLED_FEATURES",
        ),
    ] = ()

    #: The URL to use when connecting to the database.
    #: If unconfigured, the 'Manor' will create an ephemeral database.
    db_url: Annotated[str, fields.ConfigField[str](namespace=_DB_NAMESPACE, env="DATABASE_URL")] = (
        _IN_MEMORY_DB_URL
    )

    #: The Discord token to use when deploying the ephemeral staff member.
    discord_token: Annotated[
        str,
        fields.ConfigField[str](
            name="token",
            namespace=_DISCORD_NAMESPACE,
            env="DISCORD_TOKEN",
        ),
    ]

    #: The tuple of server IDs to use for any commands that do not have any configured.
    guild_ids: Annotated[
        tuple[str, ...],
        fields.CSVConfigField[str](namespace=_DISCORD_NAMESPACE, env="DISCORD_GUILD_IDS"),
    ] = ()

    #: The name to which the ephemeral staff member will respond.
    ephemeral_name: Annotated[
        str,
        fields.ConfigField[str](
            name="name",
            namespace=_ALFRED_NAMESPACE,
            env="ALFRED_NAME",
        ),
    ] = _DEFAULT_NAME

    #: The nickname to set for the ephemeral staff member.
    ephemeral_nick: Annotated[
        str | None,
        fields.ConfigField[str | None](
            name="nick",
            namespace=_ALFRED_NAMESPACE,
            env="ALFRED_NICK",
        ),
    ] = None

    #: The description to use for the ephemeral staff member.
    ephemeral_description: Annotated[
        str,
        fields.ConfigField[str](
            name="description",
            namespace=_ALFRED_NAMESPACE,
            env="ALFRED_DESCRIPTION",
        ),
    ] = _DEFAULT_DESCRIPTION_TEMPLATE

    def __init__(self, config_file: Path | str | None = None) -> None:
        self._config_file = config_file
        self._features: dict[str, FeatureRef] = feature.discover_features()
        self._ephemeral: bool = self.db_url == _IN_MEMORY_DB_URL
        self._start_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._api_task: asyncio.Task | None = None
        self._deployed_staff: dict[str | uuid.UUID, asyncio.Task] = {}
        self._deployed_staff_lock: asyncio.Lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Get a Python representation of the 'Manor'."""
        return (
            "Manor("
            f"running={self.is_running} "
            f"deployed_staff={len(self._deployed_staff)!r} "
            f"api={bool(self._api_task)!r} "
            f"ephemeral={self.ephemeral!r}"
            ")"
        )

    @typing.override
    @property
    def __canonical__(self) -> dict[str, Any]:
        return {
            "running": self.is_running,
            "deployed_staff": len(self._deployed_staff),
            "api": bool(self._api_task),
            "ephemeral": self.ephemeral,
        }

    @staticmethod
    def run(config_file: Path | str | None = None) -> int:
        """Create a new 'Manor' and run it in an event loop and handle any errors.

        Returns
        -------
        int
            An exit code to use for command-line programs.

        """
        translation.bind()
        logging.configure_logging()

        log: structlog.stdlib.BoundLogger = structlog.get_logger()

        try:
            config.init(config_file)
            uvloop.run(Manor(config_file).start())
        except KeyboardInterrupt:
            pass
        except exc.BotError as e:
            log.error(str(e))
            return e.exit_code or os.EX_SOFTWARE
        except Exception as e:
            log.error(str(e), exc_info=e)
            return os.EX_SOFTWARE

        return os.EX_OK

    async def start(self) -> None:
        """Start the 'Manor', create the API, and initialize the database.

        Raises
        ------
        exc.BotError
            Raised if the 'Manor' has already been started.

        """
        if self.is_running:
            raise exc.BotError("Manor is already started.")

        await _log.ainfo("Starting 'Manor'.")

        asyncio.get_event_loop().set_exception_handler(_handle_exception)

        self._start_event.set()
        feature_modules = (ref.imported_module_name for ref in self._features.values())

        await _log.ainfo("Initializing the database.")
        warnings.simplefilter("ignore", RuntimeWarning)

        try:
            await Tortoise.init(
                db_url=self.db_url,
                modules={
                    "models": ("alfred.core.models", "aerich.models", *feature_modules),
                },
            )
        finally:
            warnings.resetwarnings()

        try:
            await Tortoise.generate_schemas()

            async with transactions.in_transaction():
                features: list[models.Feature] = [
                    (await models.Feature.get_or_create(name=feature))[0]
                    for feature in self._features
                ]

                if self.ephemeral and self.discord_token:
                    await self._populate_ephemeral_db(*features)

            if not self.ephemeral:
                await _log.ainfo("Starting the API.")
                self._api_task = asyncio.create_task(api.serve(self._config_file, manor=self))

            await self._deploy_on_load()
            await self._stop_event.wait()
        finally:
            await self._cleanup()

    def stop(self) -> None:
        """Stop the 'Manor' and cleanup the API and any deployed bots."""
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        """Return 'True' if the 'Manor' is running."""
        return self._start_event.is_set()

    @property
    def ephemeral(self) -> bool:
        """Return True if this 'Manor' is using an in-memory database."""
        return self._ephemeral

    async def deploy(self, staff_id: uuid.UUID | str) -> None:
        """Deploy the staff member with the given ID.

        This creates a new asynchronous task containing running a Discord bot running as the
        configured staff member.

        Parameters
        ----------
        staff_id : int
            The ID of the staff member to deploy.

        """
        conf: models.StaffConfig = await models.Staff.Config.get(id=staff_id)

        async def runner() -> None:
            bot_feature_names: tuple[str] = tuple(
                feature.name for feature in await conf.features if feature.name in self._features
            )
            bot_feature_classes: tuple[type[feature.Feature], ...] = tuple(
                self._features[feature].cls for feature in bot_feature_names
            )
            intents: Intents = feature.get_intents(*bot_feature_classes)
            guild_ids: list[str] | None = list(await conf.servers or self.guild_ids) or None
            staff = models.Staff(conf, intents=intents)

            for cls in bot_feature_classes:
                try:
                    await _log.ainfo("ADDING COG", cog=cls.__name__)
                    cls(
                        extra__manor=self,
                        extra__staff=staff,
                        extra__guild_ids=guild_ids,
                    )
                except Exception as e:
                    await _log.aerror(
                        f"An error occurred while loading feature '{cls.__name__}'.",
                        exc_info=e,
                    )

            await _log.ainfo(f"Starting bot for staff: {staff!r}")

            try:
                await staff.start(conf.discord_token)
            finally:
                if not staff.is_closed():
                    await staff.close()

        async with self._deployed_staff_lock:
            await _log.ainfo(f"Deploying {conf!r}")
            self._deployed_staff[staff_id] = asyncio.create_task(runner())

    async def recall(self, staff_id: uuid.UUID | str) -> None:
        """Stop a staff member and remove them from the deployed staff roster.

        Parameters
        ----------
        staff_id : int
            The ID of the staff member to recall.

        """
        async with self._deployed_staff_lock:
            await _log.ainfo(f"Recalling staff: {staff_id}")

            task: asyncio.Task = self._deployed_staff.pop(staff_id)
            if not task.cancelled():
                task.cancel()

    async def _cleanup(self) -> None:
        """Clean up the database and API before stopping."""
        await _log.ainfo("Stopping the 'Manor'.")

        if self._api_task and not self._api_task.cancelled():
            await _log.ainfo("Stopping the API.")
            self._api_task.cancel()

        await _log.ainfo("Closing database connections.")
        await Tortoise.close_connections()

        self._stop_event.clear()
        self._start_event.clear()

    async def _populate_ephemeral_db(self, *features: models.Feature) -> None:
        """Populate an ephemeral database with a default bot.

        Parameters
        ----------
        features : db.Feature
            A tuple of 'db.Feature' objects that will be added to the new ephemeral staff member.

        Raises
        ------
        AttributeError
            Raised if no global Discord token has been configured.

        """
        await _log.ainfo("Populating the ephemeral database.")

        if not self.discord_token:
            raise AttributeError(
                f"An ephemeral configuration requires {_DISCORD_NAMESPACE}.token to be configured.",
            )

        staff = await models.Staff.Config.create(
            discord_token=self.discord_token,
            name=self.ephemeral_name,
            nick=self.ephemeral_nick,
            description=self.ephemeral_description.format(
                name=self.ephemeral_name,
                nick=self.ephemeral_nick,
            ).strip(),
        )
        await staff.features.add(*features)

    async def _deploy_on_load(self) -> None:
        """Deploy all staff marked to be loaded on start-up."""
        await _log.ainfo("Deploying all staff set to deploy on load.")

        async with transactions.in_transaction():
            async for staff in models.Staff.Config.all():
                if staff.load_on_start:
                    await self.deploy(staff.id)


def _handle_exception(_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Log any uncaught exceptions in the loop.

    Parameters
    ----------
    _ : asyncio.AbstractEventLoop
        The loop in which the exception was raised.
    context : dict[str, Any]
        The asyncio exception context containing information related to the cause of the exception.

    """
    _log.exception(context["message"], exc_info=context.get("exception", True))
