"""
CompositeDownloader - Routes coverage ID downloads to the appropriate backend.

Owns one SoilGridsDownloader and one HiHydroSoilDownloader and presents a
single Downloader-compatible interface to SoilPipeline. Coverage IDs are
routed to the correct backend by matching the property token against the
HiHydroSoil property catalogue; everything else is forwarded to SoilGrids.

This design means SoilPipeline.update_data() requires no changes: it still
calls get_coverage_ids() and download_coverages() on a single downloader
object, unaware that two different remote sources are involved.
"""

import logging
from typing import Any

from datavia.core.interfaces import CompositeDownloader as CompositeDownloaderABC
from datavia.core.interfaces import Downloader

from .hihydrosoil_downloader import HiHydroSoilDownloader
from .soilgrids_downloader import SoilGridsDownloader

logger = logging.getLogger(__name__)


class CompositeDownloader(CompositeDownloaderABC):
    """Downloader that aggregates SoilGrids and HiHydroSoil into one interface.

    Presents the same two-method contract as every other downloader
    (``get_coverage_ids`` / ``download_coverages``) while internally
    delegating to a :class:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader`
    or :class:`~datavia.soil.hihydrosoil_downloader.HiHydroSoilDownloader`
    based on the property token embedded in each coverage ID.

    Routing rule: coverage IDs whose property token is present in
    :attr:`~datavia.soil.hihydrosoil_downloader.HiHydroSoilDownloader._CANONICAL_TO_PREFIX`
    are routed to HiHydroSoil; all others are routed to SoilGrids.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise both backend downloaders from a shared configuration dict.

        Parameters
        ----------
        config : dict[str, Any]
            Configuration dictionary forwarded to both backends. Keys used:

            - ``properties`` (list[str]): All properties (SoilGrids and/or
              HiHydroSoil canonical names). Each backend filters its own.
            - ``depths`` (list[str]): Depth layers passed to all backends.
            - ``statistic`` (str): Statistic token forwarded to both backends.
        """
        # Build per-backend configs, splitting the shared property list so
        # each backends only receives components it recognises.
        super().__init__()
        hihydro_known = set(HiHydroSoilDownloader._CANONICAL_TO_PREFIX)
        all_properties: list[str] = config.get("properties", [])

        soilgrids_props = [p for p in all_properties if p not in hihydro_known]
        hihydro_props = [p for p in all_properties if p in hihydro_known]

        # Both backends use the same depth list: SoilGrids and HiHydroSoil
        # share identical depth layers for all common properties.
        self.soilgrids = SoilGridsDownloader({**config, "properties": soilgrids_props})
        self.hihydrosoil = HiHydroSoilDownloader(
            {**config, "properties": hihydro_props}
        )

        logger.info(
            "CompositeDownloader initialised — SoilGrids properties: %s; "
            "HiHydroSoil properties: %s",
            soilgrids_props,
            hihydro_props,
        )

    # ------------------------------------------------------------------
    # CompositeDownloader interface
    # ------------------------------------------------------------------

    @property
    def downloaders(self) -> list[Downloader]:
        """Return the two backend downloaders managed by this composite.

        Returns
        -------
        list[Downloader]
            Both backend downloaders in declaration order: SoilGrids first,
            then HiHydroSoil.
        """
        return [self.soilgrids, self.hihydrosoil]

    def get_coverage_ids(
        self,
        properties: list[str] | None = None,
        depths: list[str] | None = None,
        statistic: str | None = None,
    ) -> list[str]:
        """Return the union of coverage IDs from both backends.

        Each backend filters the ``properties`` list to its own known
        properties, so passing the full combined list is safe.

        Parameters
        ----------
        properties : list[str], optional
            Canonical property names from any source. Defaults to the
            combined list configured on each backend.
        depths : list[str], optional
            Depth layers forwarded to both backends. Defaults to each
            backend's own default depth list.
        statistic : str, optional
            Statistical summary identifier forwarded to both backends.
            Defaults to each backend's configured statistic.

        Returns
        -------
        list[str]
            Sorted, deduplicated list of all coverage identifiers across
            both backends, e.g.
            ``["clay_0-5cm_mean", "field_capacity_0-5cm_mean", ...]``.
        """
        sg_ids = self.soilgrids.get_coverage_ids(
            properties=properties, depths=depths, statistic=statistic
        )
        hh_ids = self.hihydrosoil.get_coverage_ids(
            properties=properties, depths=depths, statistic=statistic
        )
        all_ids = sorted(set(sg_ids) | set(hh_ids))
        logger.info(
            "CompositeDownloader: %d SoilGrids + %d HiHydroSoil"
            " = %d total coverage IDs",
            len(sg_ids),
            len(hh_ids),
            len(all_ids),
        )
        return all_ids

    def download_coverages(
        self,
        coverage_ids: list[str],
        output_dir: str,
    ) -> list[tuple[str, str]]:
        """Route each coverage ID to the appropriate backend and download it.

        Coverage IDs whose property token matches a known HiHydroSoil
        canonical property are routed to :attr:`hihydrosoil`; all others are
        routed to :attr:`soilgrids`. Both batches are dispatched and their
        results merged.

        Parameters
        ----------
        coverage_ids : list[str]
            Canonical coverage identifiers to download. May contain IDs from
            both sources mixed together.
        output_dir : str
            Directory in which backends write files. Must already exist.

        Returns
        -------
        list[tuple[str, str]]
            Combined list of ``(absolute_path, coverage_id)`` pairs from
            both backends for every successfully downloaded coverage.
        """
        hihydro_known = set(HiHydroSoilDownloader._CANONICAL_TO_PREFIX)

        sg_ids: list[str] = []
        hh_ids: list[str] = []
        for cid in coverage_ids:
            if self._property_token(cid) in hihydro_known:
                hh_ids.append(cid)
            else:
                sg_ids.append(cid)

        logger.info(
            "CompositeDownloader routing: %d → SoilGrids, %d → HiHydroSoil",
            len(sg_ids),
            len(hh_ids),
        )

        results: list[tuple[str, str]] = []
        if sg_ids:
            results.extend(self.soilgrids.download_coverages(sg_ids, output_dir))
        if hh_ids:
            results.extend(self.hihydrosoil.download_coverages(hh_ids, output_dir))
        return results

    def download(self) -> str:
        """Satisfy the :class:`~datavia.core.interfaces.Downloader` abstract interface.

        Not invoked in normal pipeline use; ``download_coverages`` is
        called directly by :meth:`~datavia.soil.pipeline.SoilPipeline.update_data`.

        Returns
        -------
        str
            Path to the first successfully downloaded file across either
            backend, or ``"failed"`` if nothing could be downloaded.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            results = self.download_coverages(self.get_coverage_ids(), temp_dir)
        return results[0][0] if results else "failed"

    def get_remote_available_properties(self) -> dict[str, list[str]]:
        """Discover available properties/depths across both remote backends.

        Delegates to
        :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader.get_remote_available_properties`
        and
        :meth:`~datavia.soil.hihydrosoil_downloader.HiHydroSoilDownloader.get_remote_available_properties`
        and merges their results. Properties present in both backends have
        their depth lists unioned and re-sorted.

        Returns
        -------
        dict[str, list[str]]
            Mapping of canonical property name → sorted list of available
            depths across both sources, e.g.
            ``{"clay": ["0-5cm", "5-15cm"], "field_capacity": ["0-5cm", ...]}``.
        """
        sg_catalogue = self.soilgrids.get_remote_available_properties()
        hh_catalogue = self.hihydrosoil.get_remote_available_properties()

        merged: dict[str, set[str]] = {}
        for prop, depths in sg_catalogue.items():
            merged.setdefault(prop, set()).update(depths)
        for prop, depths in hh_catalogue.items():
            merged.setdefault(prop, set()).update(depths)

        result = {prop: sorted(depths) for prop, depths in sorted(merged.items())}
        logger.info(
            "CompositeDownloader remote catalogue: "
            "%d SoilGrids + %d HiHydroSoil properties, "
            "%d total unique properties",
            len(sg_catalogue),
            len(hh_catalogue),
            len(result),
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _property_token(coverage_id: str) -> str:
        """Extract the property token from a coverage ID using longest-prefix matching.

        Tries each known HiHydroSoil canonical property name as a prefix
        (longest first). Falls back to the first underscore-delimited token
        for single-word SoilGrids property names.

        Parameters
        ----------
        coverage_id : str
            Coverage identifier, e.g. ``"field_capacity_0-5cm_mean"`` or
            ``"clay_0-5cm_mean"``.

        Returns
        -------
        str
            Property token such as ``"field_capacity"`` or ``"clay"``.
        """
        for prop in sorted(
            HiHydroSoilDownloader._CANONICAL_TO_PREFIX, key=len, reverse=True
        ):
            if coverage_id.startswith(prop + "_"):
                return prop
        # Single-word SoilGrids property — safe to use first token.
        return coverage_id.partition("_")[0]
