"""Custom exception hierarchy for the pipeline."""


class KrwOntologyError(Exception):
    """Base exception for the pipeline."""


class PipelineStageError(KrwOntologyError):
    """A pipeline stage failed after all retries."""


class ExtractionError(KrwOntologyError):
    """AI extraction failed after all retries."""


class RateLimitError(ExtractionError):
    """AI provider rejected the request due to rate limiting or overload."""


class ValidationError(KrwOntologyError):
    """Validation found issues with extracted objects."""


class ConfigurationError(KrwOntologyError):
    """Invalid configuration."""
