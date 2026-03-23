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

from datavia.core.interfaces import Downloader

from .hihydrosoil_downloader import HiHydroSoilDownloader
from .soilgrids_downloader import SoilGridsDownloader

logger = logging.getLogger(__name__)


class CompositeDownloader(Downloader):
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
            - ``depths`` (list[str]): Depth layers passed to both backends.
            - ``statistic`` (str): Statistic token forwarded to both backends.
            - ``hihydrosoil_depths`` (list[str], optional): Override depth
              list specifically for HiHydroSoil. Defaults to ``depths`` when
              absent. Useful because HiHydroSoil supports depths that
              SoilGrids does not (e.g. ``"15-30cm"``).
        """
        self.config = config

        # Build per-backend configs, splitting the shared property list so
        # each backends only receives components it recognises.
        hihydro_known = set(HiHydroSoilDownloader._CANONICAL_TO_PREFIX)
        all_properties: list[str] = config.get("properties", [])

        soilgrids_props = [p for p in all_properties if p not in hihydro_known]
        hihydro_props = [p for p in all_properties if p in hihydro_known]

        # HiHydroSoil may have its own depth list (all 6 depths by default).
        hihydro_depths = config.get(
            "hihydrosoil_depths",
            config.get(
                "depths",
                HiHydroSoilDownloader({"properties": [], "depths": []}).priority_depths,
            ),
        )

        self.soilgrids = SoilGridsDownloader({**config, "properties": soilgrids_props})
        self.hihydrosoil = HiHydroSoilDownloader(
            {**config, "properties": hihydro_props, "depths": hihydro_depths}
        )

        logger.info(
            "CompositeDownloader initialised — SoilGrids properties: %s; "
            "HiHydroSoil properties: %s",
            soilgrids_props,
            hihydro_props,
        )

    # ------------------------------------------------------------------
    # Downloader interface
    # ------------------------------------------------------------------

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
            "CompositeDownloader: %d SoilGrids + %d HiHydroSoil = %d total coverage IDs",
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
        import tempfile  # noqa: PLC0415 — deferred: this method is an interface-only edge-case

        with tempfile.TemporaryDirectory() as temp_dir:
            results = self.download_coverages(self.get_coverage_ids(), temp_dir)
        return results[0][0] if results else "failed"

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
        return coverage_id.split("_")[0]
