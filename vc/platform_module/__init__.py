"""Platform-related modules."""

from vc.platform_module.shutdown_handlers import install_graceful_shutdown
from vc.platform_module.window_focus import get_foreground_window_title

__all__ = ["install_graceful_shutdown", "get_foreground_window_title"]
