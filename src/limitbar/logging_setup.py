from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from limitbar.config import app_data_dir


class SecretFilter(logging.Filter):
    patterns = (
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+"),
        re.compile(r"(?i)(access[_-]?token[\"'=:\s]+)[A-Za-z0-9._~+\-/=]+"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self.patterns:
            replacement = r"\1[redacted]" if pattern.groups else "[redacted]"
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    handler = RotatingFileHandler(
        app_data_dir() / "limitbar.log",
        maxBytes=512_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.addFilter(SecretFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
