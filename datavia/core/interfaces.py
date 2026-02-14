from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class Downloader(ABC):
    """Abstract base class for data downloaders."""

    @abstractmethod
    def __init__(self, url: str) -> None:
        """Initialize downloader with URL."""
        self.url = url

    @abstractmethod
    def download(self) -> str:
        """Download data and return the file path."""
        raise NotImplementedError("Download method must be implemented by subclasses.")


class Saver(ABC):
    """Abstract base class for data savers."""

    @abstractmethod
    def __init__(self, source_name: str):
        """Initialize saver with source name."""
        self.source_name = source_name

    @abstractmethod
    def check_data_exists(self) -> tuple[set, set, set]:
        """Check if data exists in storage and database.
        Returns:
            Tuple[set, set, set]: Sets of (existing_files, missing_files, extra_files)
        """
        raise NotImplementedError("Check method must be implemented by subclasses.")

    @abstractmethod
    def sync_files_and_database(self) -> bool:
        """Sync files with database records.
        Returns:
            bool: True if files and database are successfully synced, False if there is nothing to sync.
        """
        raise NotImplementedError("Sync method must be implemented by subclasses.")

    @abstractmethod
    def save(self, data_path: str) -> bool:
        """Save data from the given path."""
        raise NotImplementedError("Save method must be implemented by subclasses.")


class Getter(ABC):
    """Abstract base class for data getters."""

    @abstractmethod
    def __init__(self, source_name: str) -> None:
        """Initialize getter with source name."""
        self.source_name = source_name

    @abstractmethod
    def get_data(self, coords: np.ndarray, **kwargs: Any) -> np.ndarray:
        """Get data values at specified coordinates."""
        raise NotImplementedError("Get method must be implemented by subclasses.")


class Pipeline:
    """Abstract base class for data integration pipelines."""

    def __init__(
        self,
        name: str,
        downloader: type[Downloader],
        saver: type[Saver],
        getter: type[Getter],
        url: str | None = None,
    ) -> None:
        """Initialize the pipeline."""
        self.name = name
        self.downloader_class = downloader
        self.saver_class = saver
        self.getter_class = getter
        self.url = url

        # Instances created on demand
        self.downloader: Downloader | None = None
        self.saver: Saver | None = None
        self.getter: Getter | None = None

    def __call__(self, *args: Any, **kwds: Any) -> "Pipeline":
        """Initialize pipeline components - lazy instantiation."""
        if self.url is None:
            raise ValueError("URL is required for pipeline initialization")
        self.downloader = self.downloader_class(self.url)
        self.saver = self.saver_class(self.name)
        self.getter = self.getter_class(self.name)
        return self

    def update_data(self) -> bool:
        """Update data by downloading and saving new data."""
        if not self.downloader or not self.saver:
            raise RuntimeError("Pipeline not initialized. Call pipeline() first.")
        data_path = self.downloader.download()
        if data_path == "failed":
            return False
        success = self.saver.save(data_path)
        return success

    def find_files(self) -> set[Any]:
        """Find existing files in storage and database."""
        if not self.saver:
            raise RuntimeError("Pipeline not initialized. Call pipeline() first.")
        existing_files, _, _ = self.saver.check_data_exists()
        return existing_files

    def sync_files_and_database(self) -> None:
        """Sync files with database records."""
        if not self.saver:
            raise RuntimeError("Pipeline not initialized. Call pipeline() first.")
        self.saver.sync_files_and_database()

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        **kwargs: Any,
    ) -> np.ndarray:
        """Get data values at specified coordinates.

        Args:
            coords (np.ndarray): Array of coordinates in specified CRS.
                            - EPSG:4326: (lon, lat) pairs
                            - EPSG:25832: (x, y) pairs
                            - Shape: (n_points, 2)
            crs_coords (str): CRS of input coordinates. Defaults to "EPSG:4326".
            interpolation_order (int): Interpolation order for raster sampling.
        """
        if not self.getter:
            raise RuntimeError("Pipeline not initialized. Call pipeline() first.")
        return self.getter.get_data(
            coords=coords,
            crs_coords=crs_coords,
            interpolation_order=interpolation_order,
            **kwargs,
        )
