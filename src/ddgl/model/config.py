# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import msgspec

from ddgl.constants import DEFAULT_GITLAB_URL


class ConfigFile(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Schema for the optional ddgl config file (TOML).

    Every field has a default, so a missing config file is equivalent to
    ``ConfigFile()`` — callers never need to handle ``None``.
    """

    gitlab_url: str = DEFAULT_GITLAB_URL
    token_command: tuple[str, ...] = ()
    token_file: str = ""
    github_fallback: bool = False

    @classmethod
    def from_toml(cls, raw: bytes) -> ConfigFile:
        return msgspec.toml.decode(raw, type=cls)
