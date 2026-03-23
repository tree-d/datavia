"""Tests for the soil pipeline HiHydroSoil integration.

Covers:
- SoilPipeline._split_coverage_id  (robust compound-name parser)
- SoilPipeline._parse_* helper methods
- SoilPipeline._normalize_coverage_id with new API aliases
- HiHydroSoilDownloader._build_url URL construction
- HiHydroSoilDownloader.get_coverage_ids filtering
- CompositeDownloader._property_token routing
- CompositeDownloader.get_coverage_ids union
"""

import pytest
from datavia.soil.composite_downloader import CompositeDownloader
from datavia.soil.hihydrosoil_downloader import HiHydroSoilDownloader
from datavia.soil.pipeline import SoilPipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline() -> SoilPipeline:
    """Return a SoilPipeline instance without initialising any external resources.

    Only ``__init__`` is called; no DB connection, no downloaders are created.
    This is sufficient for testing all parser / normalisation methods.
    """
    return SoilPipeline()


@pytest.fixture()
def hihydro_downloader() -> HiHydroSoilDownloader:
    """Return a HiHydroSoilDownloader with default configuration."""
    return HiHydroSoilDownloader({})


@pytest.fixture()
def composite_downloader() -> CompositeDownloader:
    """Return a CompositeDownloader configured with all supported properties."""
    return CompositeDownloader(
        {
            "properties": [
                "clay",
                "sand",
                "ph",
                "carbon",
                "field_capacity",
                "wilting_point",
                "porosity",
                "hydraulic_conductivity",
            ],
            "depths": ["0-5cm", "5-15cm"],
            "statistic": "mean",
        }
    )


# ---------------------------------------------------------------------------
# SoilPipeline._split_coverage_id
# ---------------------------------------------------------------------------


class TestSplitCoverageId:
    """Tests for the prefix-based coverage ID splitter."""

    def test_single_word_property(self, pipeline: SoilPipeline) -> None:
        """Single-word SoilGrids properties parse correctly."""
        result = pipeline._split_coverage_id("clay_0-5cm_mean")
        assert result == ("clay", "0-5cm", "mean")

    def test_single_word_api_alias(self, pipeline: SoilPipeline) -> None:
        """SoilGrids API names (phh2o, soc) parse correctly."""
        assert pipeline._split_coverage_id("phh2o_5-15cm_mean") == (
            "phh2o",
            "5-15cm",
            "mean",
        )
        assert pipeline._split_coverage_id("soc_0-5cm_Q0.05") == (
            "soc",
            "0-5cm",
            "Q0.05",
        )

    def test_compound_property_field_capacity(self, pipeline: SoilPipeline) -> None:
        """field_capacity (two underscores in property) splits correctly."""
        result = pipeline._split_coverage_id("field_capacity_0-5cm_mean")
        assert result == ("field_capacity", "0-5cm", "mean")

    def test_compound_property_wilting_point(self, pipeline: SoilPipeline) -> None:
        """wilting_point splits without confusing depth/statistic tokens."""
        result = pipeline._split_coverage_id("wilting_point_15-30cm_mean")
        assert result == ("wilting_point", "15-30cm", "mean")

    def test_compound_property_hydraulic_conductivity(
        self, pipeline: SoilPipeline
    ) -> None:
        """hydraulic_conductivity (longest compound name) splits correctly."""
        result = pipeline._split_coverage_id("hydraulic_conductivity_60-100cm_mean")
        assert result == ("hydraulic_conductivity", "60-100cm", "mean")

    def test_porosity_single_word(self, pipeline: SoilPipeline) -> None:
        """porosity is a single-word HiHydroSoil property."""
        result = pipeline._split_coverage_id("porosity_100-200cm_mean")
        assert result == ("porosity", "100-200cm", "mean")

    def test_unknown_property_returns_none(self, pipeline: SoilPipeline) -> None:
        """Coverage IDs with unrecognised tokens return None."""
        assert pipeline._split_coverage_id("unknown_0-5cm_mean") is None

    def test_malformed_id_too_short_returns_none(self, pipeline: SoilPipeline) -> None:
        """IDs without a statistic token return None."""
        assert pipeline._split_coverage_id("clay_0-5cm") is None

    def test_empty_string_returns_none(self, pipeline: SoilPipeline) -> None:
        """Empty string returns None without raising."""
        assert pipeline._split_coverage_id("") is None


# ---------------------------------------------------------------------------
# SoilPipeline parser helpers
# ---------------------------------------------------------------------------


class TestParseDepthFromCoverageId:
    """Tests for _parse_depth_from_coverage_id."""

    def test_simple_depth(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_depth_from_coverage_id("clay_0-5cm_mean") == "0-5cm"

    def test_compound_property_depth(self, pipeline: SoilPipeline) -> None:
        assert (
            pipeline._parse_depth_from_coverage_id("field_capacity_30-60cm_mean")
            == "30-60cm"
        )

    def test_unknown_property_returns_none(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_depth_from_coverage_id("mystery_0-5cm_mean") is None


class TestParseStatisticFromCoverageId:
    """Tests for _parse_statistic_from_coverage_id."""

    def test_mean_statistic(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_statistic_from_coverage_id("sand_5-15cm_mean") == "mean"

    def test_quantile_statistic(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_statistic_from_coverage_id("clay_0-5cm_Q0.05") == "Q0.05"

    def test_compound_property_statistic(self, pipeline: SoilPipeline) -> None:
        assert (
            pipeline._parse_statistic_from_coverage_id(
                "hydraulic_conductivity_5-15cm_mean"
            )
            == "mean"
        )

    def test_unknown_property_returns_none(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_statistic_from_coverage_id("xyz_0-5cm_mean") is None


class TestParsePropertyFromDescription:
    """Tests for _parse_property_from_description."""

    def test_canonical_soilgrids(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_property_from_description("clay_0-5cm_mean") == "clay"
        assert (
            pipeline._parse_property_from_description("carbon_0-5cm_mean") == "carbon"
        )

    def test_api_alias_soilgrids(self, pipeline: SoilPipeline) -> None:
        """API names are translated to canonical names."""
        assert pipeline._parse_property_from_description("soc_0-5cm_mean") == "carbon"
        assert pipeline._parse_property_from_description("phh2o_5-15cm_mean") == "ph"

    def test_hihydrosoil_canonical(self, pipeline: SoilPipeline) -> None:
        assert (
            pipeline._parse_property_from_description("field_capacity_0-5cm_mean")
            == "field_capacity"
        )
        assert (
            pipeline._parse_property_from_description("wilting_point_5-15cm_mean")
            == "wilting_point"
        )
        assert (
            pipeline._parse_property_from_description("porosity_0-5cm_mean")
            == "porosity"
        )
        assert (
            pipeline._parse_property_from_description(
                "hydraulic_conductivity_0-5cm_mean"
            )
            == "hydraulic_conductivity"
        )

    def test_hihydrosoil_api_alias(self, pipeline: SoilPipeline) -> None:
        """HiHydroSoil API names are translated to canonical names."""
        assert (
            pipeline._parse_property_from_description("WCpF2_0-5cm_mean")
            == "field_capacity"
        )
        assert (
            pipeline._parse_property_from_description("Ksat_0-5cm_mean")
            == "hydraulic_conductivity"
        )

    def test_unknown_returns_none(self, pipeline: SoilPipeline) -> None:
        assert pipeline._parse_property_from_description("mystery_0-5cm_mean") is None


class TestNormalizeCoverageId:
    """Tests for _normalize_coverage_id."""

    def test_already_canonical_soilgrids(self, pipeline: SoilPipeline) -> None:
        assert pipeline._normalize_coverage_id("clay_0-5cm_mean") == "clay_0-5cm_mean"
        assert (
            pipeline._normalize_coverage_id("carbon_0-5cm_mean") == "carbon_0-5cm_mean"
        )

    def test_soilgrids_api_alias_normalised(self, pipeline: SoilPipeline) -> None:
        assert pipeline._normalize_coverage_id("soc_0-5cm_mean") == "carbon_0-5cm_mean"
        assert pipeline._normalize_coverage_id("phh2o_5-15cm_mean") == "ph_5-15cm_mean"

    def test_hihydrosoil_api_alias_normalised(self, pipeline: SoilPipeline) -> None:
        assert (
            pipeline._normalize_coverage_id("WCpF2_0-5cm_mean")
            == "field_capacity_0-5cm_mean"
        )
        assert (
            pipeline._normalize_coverage_id("Ksat_5-15cm_mean")
            == "hydraulic_conductivity_5-15cm_mean"
        )

    def test_compound_canonical_unchanged(self, pipeline: SoilPipeline) -> None:
        cid = "field_capacity_30-60cm_mean"
        assert pipeline._normalize_coverage_id(cid) == cid

    def test_unknown_id_returned_unchanged(self, pipeline: SoilPipeline) -> None:
        assert (
            pipeline._normalize_coverage_id("mystery_0-5cm_mean")
            == "mystery_0-5cm_mean"
        )


# ---------------------------------------------------------------------------
# HiHydroSoilDownloader
# ---------------------------------------------------------------------------


class TestHiHydroSoilDownloaderBuildUrl:
    """Tests for HiHydroSoilDownloader._build_url."""

    def test_field_capacity_url(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        url = hihydro_downloader._build_url("field_capacity_0-5cm_mean")
        assert url is not None
        assert url.endswith("WCpF2_0-5cm_M_250m.tif")
        assert url.startswith("http://opendap.biodt.eu")

    def test_wilting_point_url(self, hihydro_downloader: HiHydroSoilDownloader) -> None:
        url = hihydro_downloader._build_url("wilting_point_5-15cm_mean")
        assert url is not None
        assert "WCpF4.2_5-15cm_M_250m.tif" in url

    def test_hydraulic_conductivity_url(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        url = hihydro_downloader._build_url("hydraulic_conductivity_100-200cm_mean")
        assert url is not None
        assert "Ksat_100-200cm_M_250m.tif" in url

    def test_porosity_url(self, hihydro_downloader: HiHydroSoilDownloader) -> None:
        url = hihydro_downloader._build_url("porosity_30-60cm_mean")
        assert url is not None
        assert "WCsat_30-60cm_M_250m.tif" in url

    def test_unknown_property_returns_none(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        assert hihydro_downloader._build_url("clay_0-5cm_mean") is None

    def test_unknown_statistic_returns_none(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        assert hihydro_downloader._build_url("field_capacity_0-5cm_Q0.05") is None


class TestHiHydroSoilDownloaderGetCoverageIds:
    """Tests for HiHydroSoilDownloader.get_coverage_ids."""

    def test_default_returns_all_properties_and_depths(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        ids = hihydro_downloader.get_coverage_ids()
        # 4 properties * 6 depths = 24
        assert len(ids) == 24

    def test_filters_unknown_properties(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        ids = hihydro_downloader.get_coverage_ids(
            properties=["field_capacity", "clay"],  # clay is not a HiHydroSoil prop
            depths=["0-5cm"],
            statistic="mean",
        )
        assert len(ids) == 1
        assert ids[0] == "field_capacity_0-5cm_mean"

    def test_explicit_properties_and_depths(
        self, hihydro_downloader: HiHydroSoilDownloader
    ) -> None:
        ids = hihydro_downloader.get_coverage_ids(
            properties=["porosity", "hydraulic_conductivity"],
            depths=["0-5cm", "5-15cm"],
            statistic="mean",
        )
        assert len(ids) == 4
        assert "porosity_0-5cm_mean" in ids
        assert "hydraulic_conductivity_5-15cm_mean" in ids


# ---------------------------------------------------------------------------
# CompositeDownloader
# ---------------------------------------------------------------------------


class TestCompositeDownloaderPropertyToken:
    """Tests for CompositeDownloader._property_token routing helper."""

    def test_soilgrids_single_word(self) -> None:
        assert CompositeDownloader._property_token("clay_0-5cm_mean") == "clay"
        assert CompositeDownloader._property_token("carbon_5-15cm_mean") == "carbon"

    def test_hihydrosoil_compound(self) -> None:
        assert (
            CompositeDownloader._property_token("field_capacity_0-5cm_mean")
            == "field_capacity"
        )
        assert (
            CompositeDownloader._property_token("hydraulic_conductivity_0-5cm_mean")
            == "hydraulic_conductivity"
        )

    def test_hihydrosoil_single_word(self) -> None:
        assert CompositeDownloader._property_token("porosity_0-5cm_mean") == "porosity"


class TestCompositeDownloaderGetCoverageIds:
    """Tests for CompositeDownloader.get_coverage_ids union behaviour."""

    def test_contains_both_source_ids(
        self, composite_downloader: CompositeDownloader
    ) -> None:
        ids = composite_downloader.get_coverage_ids()
        assert "clay_0-5cm_mean" in ids
        assert "field_capacity_0-5cm_mean" in ids

    def test_no_duplicates(self, composite_downloader: CompositeDownloader) -> None:
        ids = composite_downloader.get_coverage_ids()
        assert len(ids) == len(set(ids))

    def test_soilgrids_ids_not_in_hihydrosoil_and_vice_versa(
        self, composite_downloader: CompositeDownloader
    ) -> None:
        """SoilGrids and HiHydroSoil IDs must be disjoint (no property overlap)."""
        sg_ids = set(composite_downloader.soilgrids.get_coverage_ids())
        hh_ids = set(composite_downloader.hihydrosoil.get_coverage_ids())
        assert sg_ids.isdisjoint(hh_ids)

    def test_property_split_is_correct(
        self, composite_downloader: CompositeDownloader
    ) -> None:
        """SoilGrids backend should not contain HiHydroSoil properties."""
        hihydro_props = set(HiHydroSoilDownloader._CANONICAL_TO_PREFIX)
        for cid in composite_downloader.soilgrids.get_coverage_ids():
            prop = CompositeDownloader._property_token(cid)
            assert prop not in hihydro_props, (
                f"SoilGrids downloader received HiHydroSoil property '{prop}'"
            )
