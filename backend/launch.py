"""Backend entry point used by start.ps1.

start.ps1 starts this script with Start-Process (own hidden console, no inherited handles), so
the server is not tied to the console of the shell that launched it. Logs go to files because
the hidden console is not visible. Always binds to 127.0.0.1 with a single worker.
"""

from __future__ import annotations

import argparse
import logging.config
import sys
from pathlib import Path

import uvicorn

BACKEND = Path(__file__).resolve().parent


def log_config(log_dir: Path) -> dict:
    handler = {"class": "logging.handlers.RotatingFileHandler", "maxBytes": 5_000_000, "backupCount": 3,
               "encoding": "utf-8", "formatter": "plain"}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {
            "server": handler | {"filename": str(log_dir / "server.log")},
            "access": handler | {"filename": str(log_dir / "access.log")},
        },
        "loggers": {
            "uvicorn": {"handlers": ["server"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["server"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            "app": {"handlers": ["server"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["server"], "level": "WARNING"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-dir", type=Path, required=True)
    args = parser.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    cfg = log_config(args.log_dir)
    logging.config.dictConfig(cfg)
    sys.path.insert(0, str(BACKEND))
    from app.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, workers=1, log_config=cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
