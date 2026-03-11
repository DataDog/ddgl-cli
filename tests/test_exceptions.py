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
