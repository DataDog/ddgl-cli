from __future__ import annotations

import asyncio
import logging
import subprocess
import time


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ddgl namespace."""
    return logging.getLogger(name)


class ShellError(Exception):
    """A subprocess exited with a non-zero return code."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Command {cmd} failed (rc={returncode}): {stderr}"
        )


# Module-level logger (can't use forward ref trick, just call get_logger)
logger = get_logger("ddgl.shell")


def run(
    *cmd: str,
    check: bool = True,
    timeout: float | None = 10.0,
) -> tuple[str, str]:
    """Run a command synchronously. Returns (stdout, stderr).

    Logs the command and result at DEBUG. Raises ShellError on non-zero
    exit when check=True.
    """
    cmd_str = " ".join(cmd)
    logger.debug("$ %s", cmd_str)

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("$ %s -> timed out after %.0fs", cmd_str, timeout)
        raise
    except FileNotFoundError:
        logger.debug("$ %s -> command not found", cmd_str)
        raise

    elapsed_ms = (time.monotonic() - t0) * 1000
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        logger.debug(
            '$ %s -> rc=%d "%s" (%.0fms)',
            cmd_str, result.returncode, stderr, elapsed_ms,
        )
        if check:
            raise ShellError(list(cmd), result.returncode, stderr)
    else:
        logger.debug('$ %s -> "%s" (%.0fms)', cmd_str, stdout, elapsed_ms)

    return stdout, stderr


async def run_async(
    *cmd: str,
    check: bool = True,
    timeout: float | None = 30.0,
) -> tuple[str, str]:
    """Run a command asynchronously. Returns (stdout, stderr).

    Logs the command and result at DEBUG. Raises ShellError on non-zero
    exit when check=True.
    """
    cmd_str = " ".join(cmd)
    logger.debug("$ %s", cmd_str)

    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("$ %s -> timed out after %.0fs", cmd_str, timeout)
        raise

    elapsed_ms = (time.monotonic() - t0) * 1000
    stdout = stdout_b.decode().strip()
    stderr = stderr_b.decode().strip()

    rc = proc.returncode or 0
    if rc != 0:
        logger.debug(
            '$ %s -> rc=%d "%s" (%.0fms)',
            cmd_str, rc, stderr, elapsed_ms,
        )
        if check:
            raise ShellError(list(cmd), rc, stderr)
    else:
        logger.debug('$ %s -> "%s" (%.0fms)', cmd_str, stdout, elapsed_ms)

    return stdout, stderr
