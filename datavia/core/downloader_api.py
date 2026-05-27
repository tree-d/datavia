"""API-based downloader placeholder for future data sources.

This module is intentionally minimal until a concrete API-backed
Downloader is implemented. It provides a documented stub so API docs
and imports do not fail.
"""

from __future__ import annotations

from typing import Any

from .interfaces import Downloader


class APIDownloader(Downloader):
    """Placeholder downloader for API-based data sources."""

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Initialize API downloader.

        Parameters
        ----------
        url : str
            Base URL for the API endpoint.
        **kwargs : Any
            Additional configuration for future API implementations.
        """
        self.url = url
        self.options = kwargs

    def download(self) -> str:
        """Download data from API.

        Raises
        ------
        NotImplementedError
                Always raised until implemented.
        """
        raise NotImplementedError("APIDownloader is not implemented yet.")


__all__ = ["APIDownloader"]
