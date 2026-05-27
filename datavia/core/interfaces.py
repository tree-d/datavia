"""Abstract base classes and protocol definitions for datavia pipelines."""

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
        """Initialize the composite downloader."""
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

    A ``Saver`` owns two concerns:

    1. **File writes** — copying a downloaded file to the data directory and
       inserting the corresponding metadata row(s) into the database.
    2. **File inventory** — listing the files it has written on disk, and
       removing stale database registrations.

    A ``Saver`` must **never** perform a database SELECT.  All read access to
    the database is the sole responsibility of the :class:`Getter`.
    Reconciliation between disk and database state is orchestrated by
    :class:`Pipeline`, which calls :meth:`list_managed_files` (Saver) and
    :meth:`get_registered_uris` (Getter) and coordinates the two results.
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
    def list_managed_files(self) -> list[str]:
        """Return absolute paths of all files this saver has written to disk.

        Scans the data directory for files that belong to this source
        (typically by matching a ``<source_name>_*`` prefix and the relevant
        file extensions).  This is a pure filesystem operation — it must
        **not** access the database.

        Returns
        -------
        list[str]
            Absolute paths to every file owned by this saver that currently
            exists on disk.  Returns an empty list when no files are found
            or the data directory does not exist yet.
        """
        raise NotImplementedError(
            "list_managed_files() must be implemented by subclasses."
        )

    @abstractmethod
    def delete_registration(self, uri: str) -> None:
        """Remove all database registrations for the given file URI.

        Deletes every database row whose ``uri`` column matches *uri* for
        this source.  This covers multi-row cases where a single file
        produces one row per variable (e.g. ERA5 multi-variable NetCDF).

        This method must **not** modify the filesystem — it only removes
        the database record(s).  The file itself is not touched.

        Parameters
        ----------
        uri : str
            Absolute path to the file whose registrations should be removed.
        """
        raise NotImplementedError(
            "delete_registration() must be implemented by subclasses."
        )

    @abstractmethod
    def save(
        self,
        data_path: str,
        reproject: bool = False,
        resolution_m: int | None = None,
        register_only: bool = False,
    ) -> bool:
        """Save data from the given path to storage.

        Copies the file to the data directory and registers its metadata in
        the database.  When *register_only* is ``True`` the copy step is
        skipped — the file is assumed to already be in the data directory
        (e.g. when re-registering an orphan file found by
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`).

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
        register_only : bool, optional
            When ``True`` skip the file-copy step and only register metadata
            in the database.  Use this when the file is already in the data
            directory, e.g. during orphan re-registration in sync.
            Defaults to ``False``.

        Returns
        -------
        bool
            ``True`` if the operation completed successfully.

        Raises
        ------
        NotImplementedError
            If subclass doesn't implement this method
        """
        raise NotImplementedError("Save method must be implemented by subclasses.")


class Getter(ABC):
    """Abstract base class for data getters.

    A ``Getter`` owns two concerns:

    1. **Database reads** — querying metadata tables to locate the files
       that cover a requested variable, time range, or spatial extent.
    2. **File-data extraction** — opening those files and returning
       interpolated values at caller-supplied coordinates.

    A ``Getter`` must **never** write to the database or the filesystem.
    All write operations are the sole responsibility of the :class:`Saver`.

    Two DB-read methods serve different callers:

    - :meth:`get_existing_layers` — returns logical layer identifiers
      (variable names, coverage IDs, etc.) used by pipeline update logic
      to decide what still needs to be downloaded.
    - :meth:`get_registered_uris` — returns the set of absolute file paths
      currently registered for this source, used by
      :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
      to reconcile disk state with database state.
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
        """Return logical layer identifiers registered in the database for this source.

        The identifier type depends on the pipeline:

        - TIFF pipelines return ``layer_name`` values
          (e.g. ``"elevation_dgm200"``).
        - Weather pipelines return variable names
          (e.g. ``"temperature_2m"``, ``"precipitation"``).

        This method is used by pipeline update logic to determine which data
        still needs to be downloaded.  It must **not** return file paths; use
        :meth:`get_registered_uris` when file paths are required (e.g. for
        disk↔DB reconciliation).

        Returns
        -------
        set[str]
            Logical layer identifiers present in the database.  Returns an
            empty set when no data has been stored yet.

        Raises
        ------
        NotImplementedError
            If subclass does not implement this method
        """
        raise NotImplementedError(
            "get_existing_layers() must be implemented by subclasses."
        )

    @abstractmethod
    def get_registered_uris(self) -> set[str]:
        """Return the set of file URIs currently registered for this source.

        Queries the database for all distinct ``uri`` values belonging to
        this source and returns them as absolute file paths.  This is used
        exclusively by
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
        to compare what the database knows about against what is actually on
        disk.

        Unlike :meth:`get_existing_layers`, this method returns file paths,
        not logical layer names or variable identifiers.

        Returns
        -------
        set[str]
            Absolute file paths registered for this source.  Returns an
            empty set when nothing has been stored yet.

        Raises
        ------
        NotImplementedError
            If subclass does not implement this method
        """
        raise NotImplementedError(
            "get_registered_uris() must be implemented by subclasses."
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
            Band number to extract (1-indexed). Defaults to 1, which selects
            the first band; suitable for single-band sources. For multi-band
            TIFFs use :meth:`get_band_mapping` to resolve property names to
            band indices.
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

    A ``Pipeline`` combines :class:`Downloader`, :class:`Saver`, and
    :class:`Getter` components to provide end-to-end data integration.

    **Component responsibilities:**

    +-------------+---------------------------------------+-------------+--------------+
    | Component   | Responsibility                        | DB reads    | DB writes    |
    +=============+=======================================+=============+==============+
    | ``Saver``   | File writes + file inventory          | never       | INSERT/DELETE|
    +-------------+---------------------------------------+-------------+--------------+
    | ``Getter``  | DB reads + file-data extraction       | SELECT only | never        |
    +-------------+---------------------------------------+-------------+--------------+
    | ``Pipeline``| Orchestration; disk↔DB reconciliation | via Getter  | via Saver    |
    +-------------+---------------------------------------+-------------+--------------+

    The ``Pipeline`` itself never queries or modifies the database directly.
    Disk↔DB reconciliation is performed by :meth:`sync_files_and_database`,
    which coordinates :meth:`Saver.list_managed_files`,
    :meth:`Getter.get_registered_uris`, :meth:`Saver.delete_registration`,
    and :meth:`Saver.save` with ``register_only=True``.

    Parameters
    ----------
    name : str
        Unique identifier for this pipeline.
    downloader : type[Downloader]
        Downloader class (not instance) to use.
    saver : type[Saver]
        Saver class (not instance) to use.
    getter : type[Getter]
        Getter class (not instance) to use.
    url : str, optional
        Data source URL.  If ``None``, must be provided during :meth:`__call__`.
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
        """Reconcile on-disk files with database records.

        Compares the set of files currently on disk
        (:meth:`Saver.list_managed_files`) against the set of file URIs
        currently registered in the database
        (:meth:`Getter.get_registered_uris`) and performs two-way cleanup:

        1. **Orphan DB rows** — rows whose URI points to a file that no
           longer exists on disk are removed via
           :meth:`Saver.delete_registration`.
        2. **Orphan disk files** — files on disk that are not registered
           in the database are re-registered via
           :meth:`Saver.save` with ``register_only=True``.

        Raises
        ------
        RuntimeError
            If the pipeline components could not be initialised.
        """
        if not self.saver or not self.getter:
            self()
        if self.saver is None or self.getter is None:
            raise RuntimeError("Pipeline components could not be initialized.")

        disk_files = set(self.saver.list_managed_files())
        db_uris = self.getter.get_registered_uris()

        orphan_db = db_uris - disk_files
        for uri in sorted(orphan_db):
            self.saver.delete_registration(uri)

        orphan_disk = disk_files - db_uris
        for path in sorted(orphan_disk):
            self.saver.save(path, register_only=True)

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
