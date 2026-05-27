"""
URLDownloader - Clean implementation of Downloader interface for URL-based downloads.
Provides robust error handling, retry logic, and content-type validation.
"""

import logging
import os
import random
import tempfile
import time
from http.client import IncompleteRead
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ChunkedEncodingError
from tqdm import tqdm
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

from .interfaces import Downloader

logger = logging.getLogger(__name__)


class URLDownloader(Downloader):
    """Downloader for URL-based file downloads with robust error handling."""

    def __init__(self, url: str) -> None:
        """Initialize URLDownloader with URL and set up a robust HTTP session.

        Parameters
        ----------
        url : str
            URL to download data from.
        """
        self.url = url

        # Configuration from legacy proven approach
        self.max_retries = 5
        self.chunk_size = (
            65536  # 64 KB — aligns with typical TCP window sizes for streaming HTTP
        )

        # Retry strategy for both HTTP and connection errors
        self.retry_strategy = Retry(
            total=7,
            backoff_factor=5,  # Wait 5, 10, 20, ... seconds between retries
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )

        # Setup robust session
        self.adapter = HTTPAdapter(max_retries=self.retry_strategy)
        self.session = requests.Session()
        self.session.mount("https://", self.adapter)
        self.session.mount("http://", self.adapter)

    def download(self) -> str:
        """
        Download file from URL and return the file path.

        Returns:
            str: Path to downloaded file on success, "failed" on error
        """
        try:
            # Create temporary file for download
            temp_fd, temp_path = tempfile.mkstemp(suffix=".download")
            working_filename = temp_path

            try:
                os.close(temp_fd)  # Close file descriptor, keep path

                # Perform HEAD request for content validation
                content_type, content_length = self._get_content_info()
                if not self._validate_content_type(content_type):
                    logger.error(f"Invalid content type: {content_type}")
                    return "failed"

                # Download with robust error handling
                success = self._download_with_retries(working_filename, content_length)

                if success:
                    # Create final filename based on content type or use generic
                    final_path = self._get_final_filename(
                        working_filename, content_type
                    )
                    os.rename(working_filename, final_path)
                    logger.info(f"Download completed successfully: {final_path}")
                    return final_path
                else:
                    self._cleanup_file(working_filename)
                    return "failed"

            except Exception as e:
                logger.error(f"Download failed: {e}")
                self._cleanup_file(working_filename)
                return "failed"

        except Exception as e:
            logger.error(f"Failed to create temporary file: {e}")
            return "failed"

    def _get_content_info(self) -> tuple[str, str | None]:
        """Get content type and length from HEAD request."""
        try:
            head = self.session.head(self.url, timeout=30)
            content_type = head.headers.get("Content-Type", "")
            content_length = head.headers.get("Content-Length", "")

            logger.info(
                f"HEAD request successful. Content-Type: {content_type}, "
                "Content-Length: "
                f"{int(content_length) / (1024 * 1024) if content_length else 'n/a'} "
                "MB"
            )
            return content_type, content_length

        except Exception as e:
            logger.warning(f"HEAD request failed: {e}")
            return "", None

    def _validate_content_type(self, content_type: str) -> bool:
        """
        Validate content type. Override in subclasses for specific validation.
        Default implementation accepts common geospatial formats.
        """
        if not content_type:
            logger.warning("No content type provided, proceeding with download")
            return True

        valid_types = [
            "image/tiff",
            "image/geotiff",
            "application/octet-stream",
            "application/zip",
            "application/json",
            "text/plain",
        ]

        return any(vtype in content_type.lower() for vtype in valid_types)

    def _download_with_retries(
        self, working_filename: str, content_length: str | None
    ) -> bool:
        """Download file with robust retry logic.

        Parameters
        ----------
        working_filename : str
            Path to the temporary file being written.
        content_length : str | None
            Value of the HTTP ``Content-Length`` header, or ``None`` when the
            server did not provide it.

        Returns
        -------
        bool
            ``True`` when the download completed and passed size validation,
            ``False`` on permanent failure.
        """
        total_downloaded = 0
        headers: dict[str, str] = {}

        total_bytes = (
            int(content_length) if content_length and content_length.isdigit() else None
        )

        progress: tqdm | None = tqdm(
            total=total_bytes,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="Downloading",
            initial=0,
        )

        try:
            for attempt in range(self.max_retries):
                try:
                    # Resume download if partially completed
                    if total_downloaded > 0:
                        file_size = (
                            os.path.getsize(working_filename)
                            if os.path.exists(working_filename)
                            else 0
                        )
                        if file_size != total_downloaded:
                            # Sync the progress bar when the on-disk size differs
                            # (e.g. a partial truncation happened between retries).
                            if progress is not None:
                                progress.update(file_size - total_downloaded)
                            total_downloaded = file_size
                        headers["Range"] = f"bytes={total_downloaded}-"

                    with self.session.get(
                        self.url, headers=headers, stream=True, timeout=30
                    ) as r:
                        # Handle rate limiting
                        if r.status_code in [202, 429]:
                            retry_after = r.headers.get("Retry-After")
                            wait_time = (
                                int(retry_after)
                                if retry_after and retry_after.isdigit()
                                else 30
                            )
                            logger.warning(
                                "Server asked to wait (status %s). "
                                "Waiting %s seconds...",
                                r.status_code,
                                wait_time,
                            )
                            time.sleep(wait_time)
                            continue

                        r.raise_for_status()

                        # If the server honoured the Range header it returns 206;
                        # the response starts at the requested offset so we append.
                        # A 200 means the server ignored Range and sends from byte 0
                        # — discard any partial data and rewrite from scratch.
                        open_mode = "ab" if r.status_code == 206 else "wb"
                        if open_mode == "wb" and total_downloaded > 0:
                            total_downloaded = 0
                            if progress is not None:
                                progress.reset()

                        with open(working_filename, open_mode) as f:
                            for chunk in r.iter_content(chunk_size=self.chunk_size):
                                if not chunk:
                                    continue
                                if not self._write_chunk_with_retries(f, chunk):
                                    raise RuntimeError("Chunk write failed repeatedly.")
                                total_downloaded += len(chunk)
                                if progress is not None:
                                    progress.update(len(chunk))
                                logger.debug(
                                    "Downloaded: %.2f MB",
                                    total_downloaded / (1024 * 1024),
                                )

                    # Validate download completion
                    return self._validate_download(total_downloaded, content_length)

                except (
                    requests.ConnectionError,
                    requests.Timeout,
                    IncompleteRead,
                    ChunkedEncodingError,
                    ProtocolError,
                ) as e:
                    wait_time = min(30, 5 * (2 ** (attempt + 1)))
                    wait_time = wait_time * (
                        0.8 + 0.4 * random.random()  # nosec B311 - jitter, not crypto
                    )
                    logger.warning(
                        "Connection issue: %s\nRetrying in %.1f seconds...",
                        e,
                        wait_time,
                    )
                    time.sleep(wait_time)

                except Exception as e:
                    logger.error("Download failed: %s", e)
                    return False

            logger.error("Max retries reached without successful download.")
            return False
        finally:
            if progress is not None:
                progress.close()

    def _write_chunk_with_retries(self, file_handle: Any, chunk: bytes) -> bool:
        """Write chunk to file with retry logic."""
        for attempt in range(self.max_retries):
            try:
                file_handle.write(chunk)
                return True
            except Exception as e:
                logger.error(f"Chunk write failed (attempt {attempt + 1}): {e}")
                time.sleep(2**attempt)

        return False

    def _validate_download(
        self, total_downloaded: int, content_length: str | None
    ) -> bool:
        """Validate that download completed successfully."""
        logger.info(f"Download size: {round(total_downloaded / (1024 * 1024), 1)} MB")

        if content_length and content_length.isdigit():
            expected_size = int(content_length)
            if total_downloaded == expected_size:
                logger.info("Download completed successfully - size verified.")
                return True
            else:
                logger.warning(
                    "Size mismatch: downloaded %s, expected %s",
                    total_downloaded,
                    expected_size,
                )
                return False
        else:
            logger.warning("Content-Length not available - cannot verify size")
            return True  # Accept download without verification

    def _get_final_filename(self, temp_path: str, content_type: str) -> str:
        """Generate final filename based on content type."""
        base_name = f"downloaded_file_{int(time.time())}"

        # Determine extension from content type
        if "tiff" in content_type.lower():
            extension = ".tif"
        elif "zip" in content_type.lower():
            extension = ".zip"
        elif "json" in content_type.lower():
            extension = ".json"
        else:
            extension = ".dat"  # Generic data file

        final_dir = os.path.dirname(temp_path)
        return os.path.join(final_dir, base_name + extension)

    def _cleanup_file(self, filepath: str) -> None:
        """Clean up file if it exists."""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.debug(f"Cleaned up file: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to cleanup file {filepath}: {e}")


class TiffDownloader(URLDownloader):
    """Specialized downloader for TIFF files with strict content validation."""

    def _validate_content_type(self, content_type: str) -> bool:
        """Validate that content type is TIFF."""
        if not content_type:
            logger.warning("No content type - assuming TIFF")
            return True

        valid_tiff_types = ["image/tiff", "image/geotiff"]
        is_valid = any(ttype in content_type.lower() for ttype in valid_tiff_types)

        if not is_valid:
            logger.error(f"Expected TIFF content, got: {content_type}")

        return is_valid
