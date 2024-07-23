"""Contains the management interface for staff members."""

from __future__ import annotations

import asyncio
import typing
import warnings

import structlog
from tortoise import Tortoise, transactions

from alfred import api, db, feature, fields
from alfred import bot as bot_
from alfred import exceptions as exc
from alfred.translation import gettext as _

if typing.TYPE_CHECKING:
    from pathlib import Path

    from discord import Intents

    from alfred.feature import FeatureRef
    from alfred.typing import ConfigValue


__all__ = ("Manor",)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

#: Something
_IN_MEMORY_DB_URL: str = "sqlite://:memory:"

#: Something
_ALFRED_NAMESPACE: str = "alfred"

#: Something
_DB_NAMESPACE: str = f"{_ALFRED_NAMESPACE}.db"

#: Something
_DISCORD_NAMESPACE: str = f"{_ALFRED_NAMESPACE}.discord"

#: Something
_DEFAULT_NAME: str = _("Alfred")

#: Default description
_DEFAULT_DESCRIPTION_TEMPLATE: str = _(
    "You are a helpful and formal butler listening in to a chat server.\n"
    "Your name is {nick}.\n"
    "You will respond to the name {name}.\n"
    "You will respond using honorifics.\n"
    "Do not ask follow-up questions.\n",
)


def _csv(value: ConfigValue) -> list[str]:
    """Return a parsed list of comma-separated values from the given value.

    Parameters
    ----------
    value : ConfigValue
        A string containing comma-separated list of values or a list of already parsed values.

    Returns
    -------
    list[str]
        A list of all values in the comma-separated string.

    """
    if isinstance(value, dict):
        raise TypeError("Expected 'str | list[str]' but got 'dict'")

    return [v.strip() for v in value.split(",")] if isinstance(value, str) else value


class Manor:
    """A class for creating and managing staff members (aka Discord bots).

    Parameters
    ----------
    config_file : Path | str | None
        The path to the configuration file to use when setting up hypercorn for the API.

    """

    #: The features that staff members may be configured to use.
    #: If unconfigured, staff members will be able to use all available features.
    enabled_features = fields.ConfigField[list[str]](
        name="features",
        namespace=_ALFRED_NAMESPACE,
        env="ALFRED_ENABLED_FEATURES",
        parser=_csv,
        default=[],
    )

    #: The URL to use when connecting to the database.
    #: If unconfigured, the 'Manor' will create an ephemeral database.
    db_url = fields.ConfigField[str](
        namespace=_DB_NAMESPACE,
        env="DATABASE_URL",
        default=_IN_MEMORY_DB_URL,
    )

    #: The Discord token to use when deploying the ephemeral staff member.
    discord_token = fields.ConfigField[str](
        name="token",
        namespace=_DISCORD_NAMESPACE,
        env="DISCORD_TOKEN",
    )

    #: The list of server IDs to use for any commands that do not have any configured.
    guild_ids = fields.ConfigField[list[str]](
        namespace=_DISCORD_NAMESPACE,
        env="DISCORD_GUILD_IDS",
        parser=_csv,
        default=[],
    )

    #: The name to which the ephemeral staff member will respond.
    ephemeral_name = fields.ConfigField[str](
        name="name",
        namespace=_ALFRED_NAMESPACE,
        env="ALFRED_NAME",
        default=_DEFAULT_NAME,
    )

    #: The nickname to set for the ephemeral staff member.
    ephemeral_nick = fields.ConfigField[str | None](
        name="nick",
        namespace=_ALFRED_NAMESPACE,
        env="ALFRED_NICK",
        default=None,
    )

    #: The description to use for the ephemeral staff member.
    ephemeral_description = fields.ConfigField[str](
        name="description",
        namespace=_ALFRED_NAMESPACE,
        env="ALFRED_DESCRIPTION",
        default=_DEFAULT_DESCRIPTION_TEMPLATE,
    )

    def __init__(self, config_file: Path | str | None = None) -> None:
        self._config_file = config_file
        self._features: dict[str, FeatureRef] = feature.discover_features()
        self._ephemeral: bool = self.db_url == _IN_MEMORY_DB_URL
        self._start_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._api_task: asyncio.Task | None = None
        self._deployed_staff: dict[int, asyncio.Task] = {}
        self._deployed_staff_lock: asyncio.Lock = asyncio.Lock()

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

        self._start_event.set()
        feature_modules = (ref.imported_module_name for ref in self._features.values())

        await _log.ainfo("Initializing the database.")
        warnings.simplefilter("ignore", RuntimeWarning)

        try:
            await Tortoise.init(
                db_url=self.db_url,
                modules={
                    "models": ("alfred.db", "aerich.models", *feature_modules),
                },
            )
        finally:
            warnings.resetwarnings()

        try:
            await Tortoise.generate_schemas()

            async with transactions.in_transaction():
                features: list[db.Feature] = [
                    (await db.Feature.get_or_create(name=feature))[0] for feature in self._features
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

    async def deploy(self, staff_id: int) -> None:
        """Deploy the staff member with the given ID.

        This creates a new asynchronous task containing running a Discord bot running as the
        configured staff member.

        Parameters
        ----------
        staff_id : int
            The ID of the staff member to deploy.

        """
        staff: db.Staff = await db.Staff.get(id=staff_id)

        async def runner() -> None:
            bot_feature_names: tuple[str] = tuple(
                feature.name for feature in await staff.features if feature.name in self._features
            )
            bot_feature_classes: tuple[type[feature.Feature], ...] = tuple(
                self._features[feature].cls for feature in bot_feature_names
            )
            intents: Intents = feature.get_intents(*bot_feature_classes)
            guild_ids: list[str] | None = list(await staff.servers or self.guild_ids) or None

            try:
                bot = bot_.Bot(intents=intents)
                for cls in bot_feature_classes:
                    cls(
                        extra__manor=self,
                        extra__bot=bot,
                        extra__guild_ids=guild_ids,
                        extra__staff=staff,
                    )

                await _log.ainfo(f"Starting bot for staff: {staff.id}")
                await bot.start(staff.discord_token)
            finally:
                if not bot.is_closed():
                    await bot.close()

        async with self._deployed_staff_lock:
            await _log.ainfo(f"Deploying {staff!r}")
            self._deployed_staff[staff_id] = asyncio.create_task(runner())

    async def recall(self, staff_id: int) -> None:
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

    async def _populate_ephemeral_db(self, *features: db.Feature) -> None:
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

        staff = await db.Staff.create(
            discord_token=self.discord_token,
            name=self.ephemeral_name,
            nick=self.ephemeral_nick,
            description=self.ephemeral_description.format(
                name=self.ephemeral_name,
                nick=self.ephemeral_nick,
            ),
        )
        await staff.features.add(*features)

    async def _deploy_on_load(self) -> None:
        """Deploy all staff marked to be loaded on start-up."""
        await _log.ainfo("Deploying all staff set to deploy on load.")

        async with transactions.in_transaction():
            async for staff in db.Staff.all():
                if staff.load_on_start:
                    await self.deploy(staff.id)