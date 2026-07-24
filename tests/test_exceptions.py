# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

import pytest

from ddgl.exceptions import NoPipelineFoundError


class TestNoPipelineFoundError:
    def test_message(self) -> None:
        err = NoPipelineFoundError(ref="main", depth=10)
        assert "main" in str(err)
        assert "10" in str(err)

    def test_attributes(self) -> None:
        err = NoPipelineFoundError(ref="feature/foo", depth=5)
        assert err.ref == "feature/foo"
        assert err.depth == 5

    def test_is_exception(self) -> None:
        with pytest.raises(NoPipelineFoundError):
            raise NoPipelineFoundError("main", 1)
