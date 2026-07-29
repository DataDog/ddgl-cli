# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from ddgl.config.loader import (
    Config,
    get_config_file_path,
    load_config,
    load_config_file,
)

__all__ = ["Config", "load_config", "load_config_file", "get_config_file_path"]
