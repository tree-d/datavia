from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class Downloader(ABC):
    """Abstract base class for data downloaders.

    Downloaders are responsible for fetching data from external sources
    (URLs, APIs) and saving it locally.
    """

    @abstractmethod
    def __init__(self, url: str) -> None:
        """Initialize downloader with data source URL.

        Parameters
        ----------
        url : str
            URL or endpoint to download data from
        """
        self.url = url

    @abstractmethod
    def download(self) -> str:
        """Download data from the configured URL.

        Returns
        -------
        str
            Path to the downloaded file

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        ConnectionError
            If download fails
        """
        raise NotImplementedError("Download method must be implemented by subclasses.")


class Saver(ABC):
    """Abstract base class for data savers.

    Savers handle storing downloaded data to local files and
    registering metadata in the PostGIS database.
    """

    @abstractmethod
    def __init__(self, source_name: str):
        """Initialize saver with data source identifier.

        Parameters
        ----------
        source_name : str
            Unique identifier for this data source
        """
        self.source_name = source_name

    @abstractmethod
    def check_data_exists(self) -> tuple[set, set, set]:
        """Check if data exists in storage and database.

        Returns
        -------
        tuple[set, set, set]
            Three sets: (existing_files, missing_files, extra_files)
            - existing_files: Files present in both filesystem and database
            - missing_files: Files in database but not on filesystem
            - extra_files: Files on filesystem but not in database

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        """
        raise NotImplementedError("Check method must be implemented by subclasses.")

    @abstractmethod
    def sync_files_and_database(self) -> bool:
        """Synchronize filesystem files with database records.

        Ensures database metadata matches actual files on disk.

        Returns
        -------
        bool
            True if synchronization was performed successfully,
            False if there was nothing to sync

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        """
        raise NotImplementedError("Sync method must be implemented by subclasses.")

    @abstractmethod
    def save(self, data_path: str) -> bool:
        """Save data from the given path to storage.

        Parameters
        ----------
        data_path : str
            Path to the data file to save

        Returns
        -------
        bool
            True if save was successful

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        """
        raise NotImplementedError("Save method must be implemented by subclasses.")


class Getter(ABC):
    """Abstract base class for data getters.

    Getters provide coordinate-based data access with spatial
    interpolation capabilities.
    """

    @abstractmethod
    def __init__(self, source_name: str) -> None:
        """Initialize getter with data source identifier.

        Parameters
        ----------
        source_name : str
            Unique identifier for this data source
        """
        self.source_name = source_name

    @abstractmethod
    def get_data(self, coords: np.ndarray, **kwargs: Any) -> np.ndarray:
        """Get data values at specified coordinates.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates, shape (n, 2) as [[lon, lat], ...]
        **kwargs : Any
            Additional parameters like crs_coords, interpolation method

        Returns
        -------
        np.ndarray
            Data values at the specified coordinates

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        """
        raise NotImplementedError("Get method must be implemented by subclasses.")


class Pipeline:
    """Abstract base class for data integration pipelines.

    A Pipeline combines Downloader, Saver, and Getter components
    to provide end-to-end data integration functionality.

    Parameters
    ----------
    name : str
        Unique identifier for this pipeline
    downloader : type[Downloader]
        Downloader class (not instance) to use
    saver : type[Saver]
        Saver class (not instance) to use
    getter : type[Getter]
        Getter class (not instance) to use
    url : str, optional
        Data source URL. If None, must be provided during __call__
    """

    def __init__(
        self,
        name: str,
        downloader: type[Downloader],
        saver: type[Saver],
        getter: type[Getter],
        url: str | None = None,
    ) -> None:
        """Initialize the pipeline with component classes.

        Parameters
        ----------
        name : str
            Pipeline identifier
        downloader : type[Downloader]
            Downloader class to instantiate
        saver : type[Saver]
            Saver class to instantiate
        getter : type[Getter]
            Getter class to instantiate
        url : str, optional
            Data source URL
        """
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
        """Initialize pipeline components - lazy instantiation.

        Creates instances of downloader, saver, and getter components.

        Returns
        -------
        Pipeline
            Self for method chaining

        Raises
        ------
        ValueError
            If URL was not provided during __init__ or __call__
        """
        if self.url is None:
            raise ValueError("URL is required for pipeline initialization")
        self.downloader = self.downloader_class(self.url)
        self.saver = self.saver_class(self.name)
        self.getter = self.getter_class(self.name)
        return self

    def update_data(self) -> bool:
        """Update data by downloading and saving new data.

        Returns
        -------
        bool
            True if update was successful

        Raises
        ------
        RuntimeError
            If pipeline not initialized (call pipeline() first)
        """
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
