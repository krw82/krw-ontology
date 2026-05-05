"""Structured JSON logging setup."""

from __future__ import annotations

import json
import logging


class StructuredFormatter(logging.Formatter):
    """Format logs as structured JSON for pipeline analysis."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "stage": getattr(record, "stage", "unknown"),
            "message": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the pipeline logger."""
    logger = logging.getLogger("krw_ontology")
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger
