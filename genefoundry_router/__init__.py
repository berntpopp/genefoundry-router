"""GeneFoundry Router — a FastMCP aggregator for the GeneFoundry -link fleet."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("genefoundry-router")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
