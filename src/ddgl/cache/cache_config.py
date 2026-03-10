from enum import Enum
from typing import Protocol


class _KeyClass(Protocol):
    pass


class CacheNS(Enum):
    """Maps a logical cache namespace to its storage configuration."""
