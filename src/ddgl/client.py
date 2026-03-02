from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ddgl.config import Config


class GitLabClient:
    """Async GitLab REST API client."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            headers={"PRIVATE-TOKEN": config.private_token},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GitLabClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _project_path(self, project_id: str | None = None) -> str:
        pid = project_id or self._config.project_id
        if pid is None:
            raise ValueError(
                "No project ID configured. Set GITLAB_PROJECT_ID or pass project_id."
            )
        return f"/projects/{quote(pid, safe='')}"

    async def _get(self, path: str, **params: Any) -> Any:
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_text(self, path: str) -> str:
        resp = await self._http.get(path)
        resp.raise_for_status()
        return resp.text

    # -- Pipelines --

    async def get_pipelines(
        self,
        ref: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """List pipelines, optionally filtered by git ref."""
        base = self._project_path(project_id)
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        return await self._get(f"{base}/pipelines", **params)

    async def get_pipeline(
        self,
        pipeline_id: int,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get details of a single pipeline."""
        base = self._project_path(project_id)
        return await self._get(f"{base}/pipelines/{pipeline_id}")

    async def get_jobs(
        self,
        pipeline_id: int,
        project_id: str | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List jobs for a pipeline."""
        base = self._project_path(project_id)
        return await self._get(
            f"{base}/pipelines/{pipeline_id}/jobs", per_page=per_page
        )

    async def get_job_log(
        self,
        job_id: int,
        project_id: str | None = None,
    ) -> str:
        """Get the raw log output of a job."""
        base = self._project_path(project_id)
        return await self._get_text(f"{base}/jobs/{job_id}/trace")
