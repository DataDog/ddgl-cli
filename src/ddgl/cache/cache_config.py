from __future__ import annotations

from enum import Enum
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Key types — one NamedTuple per namespace, naming every component.
# These are also used as plain tuples (NamedTuple is a tuple subtype), so
# they pass through to backends unchanged.
# ---------------------------------------------------------------------------


class ProjectKey(NamedTuple):
    git_root_path: str

    def __str__(self) -> str:
        return self.git_root_path


class TokenKey(NamedTuple):
    gitlab_url: str

    def __str__(self) -> str:
        return self.gitlab_url


class ApiResponseKey(NamedTuple):
    request_hash: str

    def __str__(self) -> str:
        return self.request_hash


class ObjectKey(NamedTuple):
    table_name: str
    project_id: str
    object_id: int

    def __str__(self) -> str:
        return f"{self.table_name}/{self.project_id}/{self.object_id}"


class LogKey(NamedTuple):
    job_id: str

    def __str__(self) -> str:
        return self.job_id


# ---------------------------------------------------------------------------
# Namespace config
# ---------------------------------------------------------------------------

# Type alias for "a NamedTuple class".  NamedTuple classes are tuple
# subclasses with a ``_fields`` attribute; there is no tighter built-in
# spelling, so we use ``type[tuple]`` here.
_KeyClass = type[tuple]


class _NSConfig(NamedTuple):
    filename: str
    backend: str
    key_class: _KeyClass  # must be one of the NamedTuples above


class CacheNS(Enum):
    """Maps a logical cache namespace to its storage configuration.

    The ``key_class`` field is the NamedTuple that defines the structure and
    arity of the key for that namespace.  Its ``_fields`` give both the
    expected length and a human-readable name for each component.
    """

    PROJECTS = _NSConfig("projects.json", "json", ProjectKey)
    TOKENS = _NSConfig("tokens.json", "json", TokenKey)
    API_RESPONSES = _NSConfig("api_responses.sqlite", "kv_sqlite", ApiResponseKey)
    OBJECTS = _NSConfig("objects.sqlite", "struct_sqlite", ObjectKey)
    LOGS = _NSConfig("logs", "text_files", LogKey)
