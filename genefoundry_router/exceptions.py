"""Error hierarchy for the GeneFoundry router."""

from __future__ import annotations


class RouterError(Exception):
    """Base class for all router errors."""


class ConfigurationError(RouterError):
    """Raised when settings or environment are invalid."""


class RegistryError(RouterError):
    """Raised when servers.yaml is malformed or a backend definition is invalid."""


class StartupError(RouterError):
    """Raised when the server fails to assemble or start."""
