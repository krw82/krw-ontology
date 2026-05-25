"""Tests for structured logger setup."""

from __future__ import annotations

import logging

from krw_ontology.utils.logging import setup_logging


def test_setup_logging_is_idempotent():
    logger = logging.getLogger("krw_ontology")
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    logger.handlers = []
    try:
        setup_logging()
        setup_logging()

        structured_handlers = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_krw_structured_handler", False)
        ]
        assert len(structured_handlers) == 1
        assert logger.propagate is False
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate
