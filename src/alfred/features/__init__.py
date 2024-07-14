"""Contains objects and methods for finding and loading new bot extension modules.

Attributes
----------
features : Features
    An instance of an object that can discover new features and return a list of all known features.

"""

import importlib
import sys
import typing

import structlog

from alfred.config import config

if typing.TYPE_CHECKING:
    from types import ModuleType

__all__ = ("features",)

# The group name that all new features must belong to in order to be discovered
_ENTRY_POINT_GROUP: str = __name__


class Features:
    """Finds bot extension modules and maps feature names to modules."""

    def __init__(self) -> None:
        self._lookup_by_features: dict[str, str] = {}
        self._lookup_by_modules: dict[str, str] = {}

    def discover_features(self) -> None:
        """Find and load new bot extension modules."""
        log: structlog.stdlib.BoundLogger = structlog.get_logger()

        log.info("Looking for new features.")

        self._lookup_by_features = {}
        self._lookup_by_modules = {}

        for ep in importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP):
            try:
                module: ModuleType
                if ep.module in sys.modules:
                    module = importlib.reload(sys.modules[ep.module])
                else:
                    module = ep.load()
            except Exception as e:
                if config.is_loaded:
                    log.error(
                        "An exception occurred while loading a feature module.",
                        exc_info=e,
                    )
                continue

            feature = module.__feature__ if hasattr(module, "__feature__") else ep.name
            self._lookup_by_features[feature] = ep.module
            self._lookup_by_modules[ep.module] = feature
            log.info(f"Found feature: {feature}")

        log.info("Done looking for new features.")

    @property
    def all_features(self) -> set[str]:
        """Return the set of feature names.

        Returns
        -------
        set[str]
            A set containing all unique feature names that have been found.

        """
        return set(self._lookup_by_features.keys())

    def get_feature_by_module_name(self, module_name: str) -> str:
        """Return the feature name associated with the given `module_name`.

        Parameters
        ----------
        module_name : str
            The name of the module associated with the feature to retrieve.

        Returns
        -------
        str
            The name of the feature associated with the given `module_name`.

        Raises
        ------
        KeyError
            Raised if `module_name` is not known.

        """
        return self._lookup_by_modules[module_name]

    def get_module_name_by_feature(self, feature: str) -> str:
        """Return the module name associated with the given `feature`.

        Parameters
        ----------
        feature : str
            The name of the feature associated with the module to retrieve.

        Returns
        -------
        str
            The name of the module associated with the given `feature`.

        Raises
        ------
        KeyError
            Raised if `feature` is not known.

        """
        return self._lookup_by_features[feature]


# The global object to be used for accessing the list of available features.
features = Features()
