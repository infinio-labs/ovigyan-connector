"""Ovigyan edge connector runtime."""
from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("ovigyan-connector")
except _metadata.PackageNotFoundError:  # running from a source checkout that is not installed
    __version__ = "0.0.0"
