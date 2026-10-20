"""Central logging configuration.

Writes to console and to a rotating log file.  Never logs secrets: callers
must pass already-sanitised values, and MQTT credentials are explicitly
masked in the MQTT modules.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import BACKEND_DIR, settings

_CONFIGURED = False

SECRET_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey",
               "authorization", "mqtt_password", "credential"}


def sanitize(value: str) -> str:
    """Mask anything that looks like a credential."""
    if not value:
        return ""
    return "***"


def mask_dict(data: dict) -> dict:
    """Return a copy of *data* with sensitive keys masked."""
    out = {}
    for k, v in data.items():
        if k.lower() in SECRET_KEYS:
            out[k] = sanitize(str(v)) if v else ""
        else:
            out[k] = v
    return out


def setup_logging() -> logging.Logger:
    """Configure root logging once and return the application logger."""
    global _CONFIGURED

    logger = logging.getLogger("p310")
    if _CONFIGURED:
        return logger

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)
    logger.addHandler(console)

    # File handler (best effort - never crash the app because of logging)
    try:
        log_path = Path(settings.log_file)
        if not log_path.is_absolute():
            log_path = BACKEND_DIR / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(level)
        logger.addHandler(fh)
    except OSError:  # pragma: no cover - defensive
        logger.warning("Could not open log file %s; file logging disabled.",
                       settings.log_file)

    _CONFIGURED = True
    logger.info("Logging initialised (level=%s)", settings.log_level)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the ``p310`` namespace."""
    base = logging.getLogger("p310")
    if not base.handlers:
        setup_logging()
    return base.getChild(name)
