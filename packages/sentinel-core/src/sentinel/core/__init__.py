"""Shared foundations for every Sentinel service.

Nothing in here knows about cameras, models or routes. It knows about
configuration, connections, the shapes rows come back in, and the two
queue mechanisms.
"""

from sentinel.core.config import Settings, settings
from sentinel.core.logging import configure_logging, get_logger

__all__ = ["Settings", "settings", "configure_logging", "get_logger"]
