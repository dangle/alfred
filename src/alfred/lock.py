"""Tools for working with working with asynchronous locks."""

import asyncio


class Locked[T]:
    """An async context manager that yields the object given to it.

    Parameters
    ----------
    obj : T
        The object to lock.

    """

    def __init__(self, obj: T) -> None:
        self._obj: T = obj
        self._lock: asyncio.Lock = asyncio.Lock()

    async def __aenter__(self) -> T:
        """Acquire the lock and return the locked object.

        Returns
        -------
        T
            The locked object.

        """
        await self._lock.acquire()
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        """Release the lock."""
        self._lock.release()

    def __repr__(self) -> str:
        """Return a Python representation of the 'Locked' object.

        Returns
        -------
        str
            A Python string representation of the 'Locked' object.

        """
        return f"Locked({self._obj!r})"
