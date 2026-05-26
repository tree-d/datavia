"""Abstract base classes and protocol definitions for the datavia pipeline components."""

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class Downloader(ABC):
    """Abstract base class for data downloaders.

    Downloaders are responsible for fetching data from external sources
    and writing the result to a local file.
    """

    @abstractmethod
    def download(self) -> str:
        """Fetch data and return the path to the downloaded file.

        Returns
        -------
        str
            Absolute path to the downloaded file, or ``"failed"`` on error.

        Raises
        ------
        ConnectionError
            If the download cannot be completed.
        """
        raise NotImplementedError("download() must be implemented by subclasses.")


class CompositeDownloader(Downloader, ABC):
    """Abstract base class for downloaders that aggregate multiple sub-downloaders.

    A ``CompositeDownloader`` presents the same :class:`Downloader` interface
    to the rest of the pipeline while internally delegating to two or more
    specialised sub-downloaders.  This lets a :class:`Pipeline` remain
    unaware of the underlying split.

    Subclasses *must* implement :attr:`downloaders` (returning the list of
    component :class:`Downloader` instances) and :meth:`download` (which
    orchestrates and delegates to those components).
    """

    def __init__(self) -> None:
        """Initialise the composite downloader."""
        pass

    @property
    @abstractmethod
    def downloaders(self) -> list[Downloader]:
        """Return the list of component sub-downloaders.

        Returns
        -------
        list[Downloader]
            All sub-downloaders managed by this composite.
        """
        raise NotImplementedError(
            "downloaders property must be implemented by subclasses."
        )


class Saver(ABC):
    """Abstract base class for data savers.

    Savers handle storing downloaded data to local TIFF files and registering
    raster metadata in the SQLite metadata database.
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
    def save(
        self,
        data_path: str,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Save data from the given path to storage.

        Parameters
        ----------
        data_path : str
            Path to the data file to save.
        reproject : bool, optional
            When ``True`` the saved file is reprojected in-place to the
            application-wide default CRS (``get_config().default_crs``).
            Defaults to ``False``.
        resolution_m : int, optional
            Target pixel resolution in metres applied during reprojection.
            Only meaningful for projected (metric) CRSs; ignored for
            geographic CRSs. When ``None`` rasterio derives the resolution
            automatically. Defaults to ``None``.

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
    def get_existing_layers(self) -> set[str]:
        """Return layer names already registered in the database for this source.

        This is the authoritative way to query what data is currently stored.
        Only the Getter may read from the database; callers should use this
        method instead of asking the Saver.

        Returns
        -------
        set[str]
            Layer names present in the database, e.g.
            ``{"elevation_dgm200"}``. Returns an empty set when no data
            has been stored yet.

        Raises
        ------
        NotImplementedError
            If subclass does not implement this method
        """
        raise NotImplementedError(
            "get_existing_layers method must be implemented by subclasses."
        )

    @abstractmethod
    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
    ) -> "np.ndarray | dict[str, np.ndarray]":
        """Get data values at specified coordinates.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates, shape (n, 2) as [[lon, lat], ...]
        crs_coords : str
            CRS of the input coordinates. Defaults to "EPSG:4326".
        interpolation_order : int
            Interpolation order for raster sampling. Defaults to 3 (cubic).
        band : int
            Band number to extract (1-indexed). Defaults to 1 for backward
            compatibility with single-band sources. For multi-band TIFFs use
            :meth:`get_band_mapping` to resolve property names to band indices.
        **kwargs : Any
            Additional parameters passed to the underlying sampling function.

        Returns
        -------
        np.ndarray
            For single-property pipelines (e.g. elevation): shape ``(N,)``.
        dict[str, np.ndarray]
            For multi-property pipelines (e.g. soil): keys are coverage IDs
            such as ``"clay_0-5cm_mean"``, values are arrays of shape ``(N,)``.
            A single-property request on a multi-property pipeline also returns
            a plain ``np.ndarray``.

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

    # -----------------------------------------------------------------------
    # Config validation utility
    # -----------------------------------------------------------------------

    @staticmethod
    def validate_pipeline_config(
        config: dict,
        required_keys: set[str],
        known_keys: set[str],
        pipeline_name: str = "Pipeline",
    ) -> None:
        """Validate a pipeline configuration dict against required and known keys.

        Checks for missing required keys and unrecognised keys in a single
        pass, reporting *all* violations in one ``ValueError`` instead of
        stopping at the first problem.  Call this at ``__init__`` time before
        any other logic so users see the full list of mistakes at once.

        Parameters
        ----------
        config : dict
            The configuration dict provided by the caller.
        required_keys : set[str]
            Keys that must be present in *config*.
        known_keys : set[str]
            All keys that are valid for this pipeline (must include all
            required keys).  Keys absent from this set are treated as typos
            and raise an error.
        pipeline_name : str, optional
            Human-readable name of the calling pipeline, used in error
            messages.  Defaults to ``"Pipeline"``.

        Raises
        ------
        ValueError
            If one or more required keys are absent from *config*.
        ValueError
            If one or more keys in *config* are not in *known_keys*.
        """
        provided_keys = set(config.keys())

        missing = required_keys - provided_keys
        unknown = provided_keys - known_keys

        if missing:
            raise ValueError(
                f"{pipeline_name} config missing required key(s): {missing}"
            )
        if unknown:
            raise ValueError(
                f"{pipeline_name} config contains unknown key(s): {unknown}"
            )

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
        """Instantiate pipeline components.

        When ``self.url`` is set the downloader is created with just the URL
        (elevation case).  Otherwise ``*args`` and ``**kwds`` are forwarded
        directly to the downloader constructor (soil, weather pipelines that
        override this method supply their own config).

        Returns
        -------
        Pipeline
            Self for method chaining.

        Raises
        ------
        ValueError
            If neither a URL nor any arguments are provided.
        """
        if self.url:
            self.downloader = self.downloader_class(self.url)  # type: ignore[call-arg]
        elif args or kwds:
            self.downloader = self.downloader_class(*args, **kwds)
        else:
            raise ValueError(
                "No URL and no arguments provided for downloader initialization."
            )
        self.saver = self.saver_class(self.name)
        self.getter = self.getter_class(self.name)
        return self

    def update_data(
        self,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Update data by downloading and saving new data.

        Parameters
        ----------
        reproject : bool, optional
            When ``True`` each saved file is reprojected in-place to the
            application-wide default CRS (``get_config().default_crs``).
            Defaults to ``False``.
        resolution_m : int, optional
            Target pixel resolution in metres used during reprojection.
            Only meaningful for projected (metric) CRSs. When ``None``
            rasterio derives the resolution automatically.
            Defaults to ``None``.

        Returns
        -------
        bool
            True if update was successful
        """
        if not self.downloader or not self.saver:
            self()
        if self.downloader is None or self.saver is None:
            raise RuntimeError("Pipeline components could not be initialized.")
        data_path = self.downloader.download()
        if data_path == "failed":
            return False
        success = self.saver.save(
            data_path, reproject=reproject, resolution_m=resolution_m
        )
        return success

    def sync_files_and_database(self) -> None:
        """Sync files with database records."""
        if not self.saver:
            self()
        if self.saver is None:
            raise RuntimeError("Pipeline saver could not be initialized.")
        self.saver.sync_files_and_database()

    def get_data(
        self,
        coords: np.ndarray,
        crs_coords: str = "EPSG:4326",
        interpolation_order: int = 3,
        band: int = 1,
        **kwargs: Any,
    ) -> "np.ndarray | dict[str, np.ndarray]":
        """Get data values at specified coordinates.

        Parameters
        ----------
        coords : np.ndarray
            Array of coordinates in the specified CRS, shape ``(N, 2)``.
            For EPSG:4326: ``[longitude, latitude]`` pairs.
            For EPSG:25832: ``[easting, northing]`` pairs.
        crs_coords : str
            CRS of the input coordinates. Defaults to ``"EPSG:4326"``.
        interpolation_order : int
            Interpolation order for raster sampling. Defaults to 3 (cubic).
        band : int
            Band number to extract (1-indexed). Defaults to 1.

        Returns
        -------
        np.ndarray
            For single-property pipelines (e.g. elevation): shape ``(N,)``.
        dict[str, np.ndarray]
            For multi-property pipelines (e.g. soil): coverage-ID keys, ``(N,)`` values.

        """
        if not self.getter:
            self()
        if self.getter is None:
            raise RuntimeError("Pipeline getter could not be initialized.")
        return self.getter.get_data(
            coords=coords,
            crs_coords=crs_coords,
            interpolation_order=interpolation_order,
            band=band,
            **kwargs,
        )
