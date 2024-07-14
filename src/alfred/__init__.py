"""Contains global package constants.

Attributes
----------
__project_package__ : str
    The name of the root package of the project.
__version__ : str
    The dynamically calculated version of the project.

"""

import dunamai

__all__ = (
    "__project_package__",
    "__version__",
)

__project_package__: str = __name__

__version__: str = dunamai.get_version(
    f"{__project_package__}",
    third_choice=dunamai.Version.from_any_vcs,
).serialize()
