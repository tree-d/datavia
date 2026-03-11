"""
MultibandSaver - alignment-aware multi-band TIFF management for the soil package.

Extends TiffSaver to support incremental coverage downloads:

- Groups individual single-band GeoTIFFs by spatial grid signature
  (CRS + affine transform + raster dimensions).
- Aligned coverages are stacked into one multi-band GeoTIFF per alignment group;
  misaligned coverages are stored as individual single-band files.
- When new coverages share the pixel grid of an already-stored file, that file is
  extended with additional bands rather than replaced.
- Files are written to the data directory configured by datavia and registered in
  PostGIS via the inherited TiffSaver machinery.

Band descriptions stored in the GeoTIFF and in the database are the raw coverage IDs
(e.g. ``"clay_0-5cm_mean"``), which makes delta computation trivial: the pipeline
simply compares needed IDs against stored IDs without any vocabulary translation.
"""

import hashlib
import logging
import os
import shutil

import numpy as np
import rasterio

from datavia.core.saver_tiff import TiffSaver
from datavia.library.database.query import get_band_metadata

logger = logging.getLogger(__name__)

# Maximum absolute difference between two affine transform coefficients that
# still counts as the same pixel grid. Degrees for EPSG:4326 sources.
_TRANSFORM_TOLERANCE: float = 1e-9


def _grid_signature(src: rasterio.DatasetReader) -> tuple:
    """Compute a hashable grid signature for an open raster dataset.

    Parameters
    ----------
    src : rasterio.DatasetReader
        Open rasterio dataset whose grid signature is to be computed.

    Returns
    -------
    tuple
        ``(crs_wkt, rounded_transform_tuple, width, height)`` where each
        affine coefficient is rounded to 9 decimal places to guard against
        floating-point noise introduced by the WCS server.
    """
    crs_wkt = src.crs.to_wkt() if src.crs else ""
    t = src.transform
    rounded = tuple(round(v, 9) for v in (t.a, t.b, t.c, t.d, t.e, t.f))
    return (crs_wkt, rounded, src.width, src.height)


def _are_grids_aligned(sig_a: tuple, sig_b: tuple) -> bool:
    """Return ``True`` when two grid signatures represent the same pixel grid.

    CRS and raster dimensions must be identical. Affine transform coefficients
    are compared with a tolerance of :data:`_TRANSFORM_TOLERANCE` to handle
    floating-point noise returned by different WCS service endpoints.

    Parameters
    ----------
    sig_a, sig_b : tuple
        Grid signatures as returned by :func:`_grid_signature`.

    Returns
    -------
    bool
        ``True`` if the two grids are pixel-for-pixel aligned.
    """
    crs_a, transform_a, w_a, h_a = sig_a
    crs_b, transform_b, w_b, h_b = sig_b
    if crs_a != crs_b or w_a != w_b or h_a != h_b:
        return False
    return all(
        abs(a - b) <= _TRANSFORM_TOLERANCE
        for a, b in zip(transform_a, transform_b, strict=True)
    )


def _signature_hash(sig: tuple) -> str:
    """Return an 8-character hex digest of a grid signature.

    Used to derive a stable, short filename component that uniquely
    identifies a pixel grid without encoding the full transform in the name.

    Parameters
    ----------
    sig : tuple
        Grid signature as returned by :func:`_grid_signature`.

    Returns
    -------
    str
        Eight lower-case hexadecimal characters.
    """
    raw = repr(sig).encode()
    return hashlib.md5(raw).hexdigest()[:8]


class MultibandSaver(TiffSaver):
    """Saver for incrementally-downloaded multi-coverage soil GeoTIFFs.

    Extends :class:`~datavia.core.saver_tiff.TiffSaver` with two soil-specific
    concerns:

    1. **Alignment grouping** — downloaded single-band files are grouped by
       their pixel grid (CRS + affine transform + dimensions). Only
       pixel-for-pixel aligned coverages are stacked into one multi-band file.
    2. **Incremental extension** — when new coverages share the grid of an
       already-stored file in the data directory, that file is extended with
       additional bands rather than replaced from scratch.

    Band descriptions stored in both the GeoTIFF and in ``raster_band_metadata``
    are the raw coverage IDs (e.g. ``"clay_0-5cm_mean"``). This makes the
    incremental delta computation unambiguous.
    """

    def get_stored_coverage_ids(self) -> set[str]:
        """Return the set of coverage IDs already registered in the database.

        Reads ``raster_band_metadata`` for this source. Band descriptions stored
        by :meth:`_save_group` are the raw coverage IDs, so no vocabulary
        translation is required.

        Returns
        -------
        set[str]
            Coverage IDs present in the database for this source, e.g.
            ``{"clay_0-5cm_mean", "sand_0-5cm_mean"}``. Returns an empty set
            when no data has been saved yet.
        """
        all_meta = get_band_metadata(self.source_name)
        stored: set[str] = set()
        for bands in all_meta.values():
            for band_info in bands:
                desc = band_info.get("description", "")
                if desc:
                    stored.add(desc)
        logger.debug(
            "Stored coverage IDs for source '%s': %s", self.source_name, stored
        )
        return stored

    def save_coverages(self, new_files: list[tuple[str, str]]) -> bool:
        """Align, group, and store a list of single-band coverage GeoTIFFs.

        Each entry in *new_files* is a ``(file_path, coverage_id)`` pair as
        returned by
        :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader.download_coverages`.

        The method performs the following steps:

        1. Opens every file and computes its pixel-grid signature.
        2. Groups files sharing the same signature into alignment groups.
        3. For each group, writes or extends a multi-band GeoTIFF in the data
           directory and updates PostGIS metadata.

        Parameters
        ----------
        new_files : list[tuple[str, str]]
            Pairs of ``(absolute_path, coverage_id)`` for single-band temp
            files to be processed.

        Returns
        -------
        bool
            ``True`` if at least one alignment group was stored successfully.
        """
        if not new_files:
            logger.warning("save_coverages called with empty file list")
            return False

        # Build signature → [(path, coverage_id)] groups
        groups: dict[tuple, list[tuple[str, str]]] = {}
        for path, coverage_id in new_files:
            try:
                with rasterio.open(path) as src:
                    sig = _grid_signature(src)
            except Exception as exc:
                logger.warning(
                    "Cannot read grid signature for '%s' (%s): skipping", path, exc
                )
                continue
            groups.setdefault(sig, []).append((path, coverage_id))

        if not groups:
            logger.error("No readable files in new_files list")
            return False

        success_count = 0
        for sig, group in groups.items():
            if self._save_group(sig, group):
                success_count += 1

        logger.info(
            "save_coverages: %d/%d alignment groups saved successfully",
            success_count,
            len(groups),
        )
        return success_count > 0

    def _save_group(self, sig: tuple, group: list[tuple[str, str]]) -> bool:
        """Write one alignment group to the data directory.

        Finds any existing multi-band file with the same grid signature in the
        data directory, merges its bands with the incoming ones, and writes the
        combined result atomically. Calls
        :meth:`~datavia.core.saver_tiff.TiffSaver._import_raster_metadata_with_bands`
        so that both ``raster_layers`` and ``raster_band_metadata`` are kept
        up to date.

        Parameters
        ----------
        sig : tuple
            Grid signature as returned by :func:`_grid_signature`.
        group : list[tuple[str, str]]
            ``(path, coverage_id)`` pairs for single-band files in this group.

        Returns
        -------
        bool
            ``True`` if the group was written and registered successfully.

        Raises
        ------
        Exception
            Re-raised after logging if an unexpected error occurs during file
            writing or metadata registration.
        """
        try:
            grid_hash = _signature_hash(sig)
            layer_name = f"{self.source_name}_{grid_hash}"
            dest_path = os.path.join(self.data_dir, f"{layer_name}.tif")

            # Load existing bands from a previously stored file (if any)
            existing_bands: list[tuple[np.ndarray, str]] = []
            if os.path.exists(dest_path):
                existing_bands = self._read_all_bands(dest_path)
                logger.debug(
                    "Found existing file '%s' with %d band(s)",
                    dest_path,
                    len(existing_bands),
                )

            # Collect incoming bands, skipping coverage IDs already present
            existing_ids = {cov_id for _, cov_id in existing_bands}
            new_bands: list[tuple[np.ndarray, str]] = []
            for path, coverage_id in group:
                if coverage_id in existing_ids:
                    logger.debug(
                        "Coverage '%s' already in '%s'; skipping",
                        coverage_id,
                        layer_name,
                    )
                    continue
                try:
                    with rasterio.open(path) as src:
                        new_bands.append((src.read(1), coverage_id))
                except Exception as exc:
                    logger.warning("Failed to read '%s': %s", path, exc)

            if not new_bands:
                logger.info("No new bands to add for layer '%s'", layer_name)
                return True  # Nothing to do is not a failure

            all_bands = existing_bands + new_bands

            # Use the first new file as the spatial profile template
            with rasterio.open(group[0][0]) as template:
                profile = template.profile.copy()
            profile.update(count=len(all_bands), compress="deflate")

            # Write atomically: temp file → rename to avoid partial writes
            tmp_path = dest_path + ".tmp"
            try:
                with rasterio.open(tmp_path, "w", **profile) as dst:
                    for band_idx, (data, coverage_id) in enumerate(all_bands, 1):
                        dst.write(data, band_idx)
                        # Store coverage_id as the band description so that
                        # delta computation and get_coverage_map() work without
                        # vocabulary translation.
                        dst.set_band_description(band_idx, coverage_id)
                        logger.debug(
                            "Wrote band %d: %s → %s", band_idx, coverage_id, layer_name
                        )
                shutil.move(tmp_path, dest_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise

            # Register in PostGIS — _import_raster_metadata deletes the old
            # entry first, so this safely handles both new and updated files.
            self._import_raster_metadata_with_bands(dest_path, layer_name)
            logger.info(
                "Saved layer '%s' with %d band(s) (%d new)",
                layer_name,
                len(all_bands),
                len(new_bands),
            )
            return True

        except Exception as exc:
            logger.error("Failed to save alignment group: %s", exc)
            return False

    def _read_all_bands(self, filepath: str) -> list[tuple[np.ndarray, str]]:
        """Read all bands and their descriptions from a GeoTIFF.

        Parameters
        ----------
        filepath : str
            Absolute path to the GeoTIFF to read.

        Returns
        -------
        list[tuple[np.ndarray, str]]
            Pairs of ``(band_data, coverage_id)`` for each band, where
            ``coverage_id`` is the band description stored by :meth:`_save_group`.
            Falls back to ``"band_N"`` for bands without a description.
        """
        result: list[tuple[np.ndarray, str]] = []
        with rasterio.open(filepath) as src:
            descs = src.descriptions or []
            for i in range(1, src.count + 1):
                data = src.read(i)
                desc = (
                    descs[i - 1] if i - 1 < len(descs) and descs[i - 1] else f"band_{i}"
                )
                result.append((data, desc))
        return result
