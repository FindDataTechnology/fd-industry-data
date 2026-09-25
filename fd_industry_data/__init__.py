"""fd-industry-data: Industry data sources crawler collection.

Keep the package import light: the admission gate and dispatch library
import fd_industry_data.sites without scrapling/playwright installed.
Heavy members load lazily via module __getattr__ (PEP 562).
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["get_browser_session"]


def __getattr__(name: str):
    if name == "get_browser_session":
        from fd_industry_data.browser import get_browser_session

        return get_browser_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
