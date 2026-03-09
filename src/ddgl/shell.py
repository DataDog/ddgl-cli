from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time

# Custom four-char level names
logging.addLevelName(logging.DEBUG, "DEBG")
logging.addLevelName(logging.INFO, "INFO")
logging.addLevelName(logging.WARNING, "WARN")
logging.addLevelName(logging.ERROR, "ERRO")
logging.addLevelName(logging.CRITICAL, "CRIT")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ddgl namespace."""
    return logging.getLogger(name)


def setup_logging(verbosity: int = 0) -> None:
    """Configure the ddgl logger hierarchy.

    Level resolution (first match wins):
        1. verbosity >= 2 -> DEBUG
        2. verbosity == 1 -> INFO
        3. DDGL_LOG_LEVEL env var
        4. Default: WARNING
    """
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        env_level = os.environ.get("DDGL_LOG_LEVEL", "").upper()
        env_val = getattr(logging, env_level, None) if env_level else None
        level = env_val if isinstance(env_val, int) else logging.WARNING

    ddgl_logger = logging.getLogger("ddgl")
    ddgl_logger.setLevel(level)

    if not ddgl_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        ddgl_logger.addHandler(handler)

    ddgl_logger.propagate = False


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
