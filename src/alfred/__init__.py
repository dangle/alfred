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
