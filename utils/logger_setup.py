"""utils/logger_setup.py — Rotating file + console logging."""

import logging
import logging.handlers
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/trading_bot.log",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    fmt     = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(ch)

    # Rotating file
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)
