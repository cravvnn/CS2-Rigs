"""A small log file to attach to bug reports. No Blender imports here.

The home folder is written as ~ so a shared log does not carry the Windows user name.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path

from .cache import default_cache_root

LOG_NAME = "cs2-rigs.log"
_MAX_BYTES = 1_000_000
_BACKUPS = 2

logger = logging.getLogger("cs2_rigs")
logger.setLevel(logging.INFO)
logger.propagate = False


def default_log_dir() -> Path:
    return default_cache_root() / "logs"


def _home() -> str:
    return os.environ.get("USERPROFILE") or str(Path.home())


def private(text: str, home: str | None = None) -> str:
    """`text` with the home folder written as ~, whichever way its slashes and case go."""
    home = (home if home is not None else _home()).rstrip("\\/")
    if len(home) <= 3:  # a bare drive or root says nothing about the user
        return text
    parts = re.split(r"[\\/]+", home)
    pattern = r"[\\/]+".join(re.escape(part) for part in parts)
    return re.sub(pattern, "~", text, flags=re.IGNORECASE)


class _PrivateFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        self._home = _home()

    def format(self, record: logging.LogRecord) -> str:
        return private(super().format(record), self._home)


def configure(log_dir: Path | None = None) -> Path | None:
    """Write the log to `log_dir` (the default folder when None). Returns the log file, or None if it cannot be written."""
    close()
    folder = Path(log_dir) if log_dir else default_log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            folder / LOG_NAME, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(_PrivateFormatter())
    logger.addHandler(handler)
    return folder / LOG_NAME


def close() -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def current_file() -> Path | None:
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename)
    return None
