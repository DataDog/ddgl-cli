# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import msgspec
import pytest

from ddgl.constants import DEFAULT_GITLAB_URL
from ddgl.model.config import ConfigFile


class TestConfigFile:
    def test_defaults(self) -> None:
        cfg = ConfigFile()
        assert cfg.gitlab_url == DEFAULT_GITLAB_URL
        assert cfg.token_command == ()
        assert cfg.token_file == ""
        assert cfg.github_fallback is False

    def test_from_toml(self) -> None:
        raw = (
            b'gitlab_url = "https://gitlab.example.com"\n'
            b'token_command = ["my-auth-tool", "print-token"]\n'
            b'token_file = "/run/secrets/gitlab-token"\n'
            b"github_fallback = true\n"
        )
        cfg = ConfigFile.from_toml(raw)
        assert cfg.gitlab_url == "https://gitlab.example.com"
        assert cfg.token_command == ("my-auth-tool", "print-token")
        assert cfg.token_file == "/run/secrets/gitlab-token"
        assert cfg.github_fallback is True

    def test_from_toml_empty(self) -> None:
        assert ConfigFile.from_toml(b"") == ConfigFile()

    def test_from_toml_unknown_field_raises(self) -> None:
        with pytest.raises(msgspec.DecodeError):
            ConfigFile.from_toml(b'bogus_key = "x"\n')

    def test_from_toml_wrong_type_raises(self) -> None:
        with pytest.raises(msgspec.DecodeError):
            ConfigFile.from_toml(b"gitlab_url = 1\n")

    def test_from_toml_malformed_raises(self) -> None:
        with pytest.raises(msgspec.DecodeError):
            ConfigFile.from_toml(b"this is not valid toml [[[")
