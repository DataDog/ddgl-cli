from __future__ import annotations


class ConfigError(Exception):
    """Raised when required configuration is missing."""


class ShellError(Exception):
    """A subprocess exited with a non-zero return code."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Command {cmd} failed (rc={returncode}): {stderr}"
        )


class PaginationLimitError(Exception):
    """Raised when pagination exceeds the maximum allowed pages."""

    def __init__(self, max_pages: int, total_pages: int | None) -> None:
        self.max_pages = max_pages
        self.total_pages = total_pages
        total = f"/{total_pages}" if total_pages else ""
        super().__init__(
            f"Pagination limit reached: fetched {max_pages}{total} pages"
        )


class GitLabAPIError(Exception):
    """Raised when a GitLab API request fails.

    Wraps httpx.HTTPStatusError with structured fields for error handling.
    """

    def __init__(
        self,
        status_code: int,
        method: str,
        path: str,
        message: str = "",
    ) -> None:
        self.status_code = status_code
        self.method = method
        self.path = path
        detail = f": {message}" if message else ""
        super().__init__(
            f"{method} {path} -> {status_code}{detail}"
        )

    @property
    def is_retryable(self) -> bool:
        """Whether this error is likely transient and worth retrying."""
        return self.status_code in {408, 429, 500, 502, 503, 504}
