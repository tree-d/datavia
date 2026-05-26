"""Unit tests for the SoilPipeline and SoilGetterTiff classes.

Covers:
- :class:`~datavia.soil.pipeline.SoilGetterTiff`:
    - ``get_stored_coverage_ids`` — prefix stripping and source isolation.
    - ``get_data_for_coverage`` — successful extraction and missing-layer error.
- :class:`~datavia.soil.pipeline.SoilPipeline`:
    - ``__init__`` — default and custom initialisation.
    - ``configure`` — in-place attribute updates.
    - ``__call__`` — downloader config dict assembly.
    - ``get_available_properties`` — with and without stored data.
    - ``get_data`` — uninitialized error, empty store, single match,
      multiple matches, unknown property warning, string coercion.
    - ``update_data`` — all-present short-circuit, incremental download,
      partial failure, unrecognised property warning.
"""

import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from datavia.soil.pipeline import SoilGetterTiff, SoilPipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline() -> SoilPipeline:
    """Return a SoilPipeline instance without initialising external components.

    Only ``__init__`` is executed. No database connections, no downloaders
    and no network calls are made. This fixture is intentionally lightweight
    so that parser and configuration tests remain fast and isolated.
    """
    return SoilPipeline()


@pytest.fixture()
def initialized_pipeline(pipeline: SoilPipeline) -> SoilPipeline:
    """Return a SoilPipeline with mock downloader, saver and getter.

    Attaches :class:`~unittest.mock.MagicMock` objects for each component
    so downstream tests can verify call patterns without external services.
    """
    pipeline.downloader = MagicMock()
    pipeline.saver = MagicMock()
    pipeline.getter = MagicMock()
    return pipeline


@pytest.fixture()
def getter() -> SoilGetterTiff:
    """Return a bare SoilGetterTiff without any database connection."""
    return SoilGetterTiff("soil")


# ---------------------------------------------------------------------------
# SoilGetterTiff.get_stored_coverage_ids
# ---------------------------------------------------------------------------


class TestSoilGetterTiffGetStoredCoverageIds:
    """Tests for the prefix-stripping logic in get_stored_coverage_ids."""

    def test_strips_source_prefix_correctly(self, getter: SoilGetterTiff) -> None:
        """Layer names with the correct prefix are returned without it."""
        getter.get_existing_layers = MagicMock(
            return_value={"soil_clay_0-5cm_mean", "soil_sand_5-15cm_mean"}
        )
        result = getter.get_stored_coverage_ids()
        assert result == {"clay_0-5cm_mean", "sand_5-15cm_mean"}

    def test_layers_from_other_sources_excluded(self, getter: SoilGetterTiff) -> None:
        """Layer names belonging to unrelated sources are filtered out."""
        getter.get_existing_layers = MagicMock(
            return_value={"soil_clay_0-5cm_mean", "elevation_dgm200"}
        )
        result = getter.get_stored_coverage_ids()
        assert "elevation_dgm200" not in result
        assert "clay_0-5cm_mean" in result

    def test_empty_database_returns_empty_set(self, getter: SoilGetterTiff) -> None:
        """No registered layers results in an empty set."""
        getter.get_existing_layers = MagicMock(return_value=set())
        assert getter.get_stored_coverage_ids() == set()

    def test_compound_property_names_preserved(self, getter: SoilGetterTiff) -> None:
        """Compound property names like 'field_capacity' survive prefix stripping."""
        getter.get_existing_layers = MagicMock(
            return_value={"soil_field_capacity_0-5cm_mean"}
        )
        result = getter.get_stored_coverage_ids()
        assert "field_capacity_0-5cm_mean" in result


# ---------------------------------------------------------------------------
# SoilGetterTiff.get_data_for_coverage
# ---------------------------------------------------------------------------


class TestSoilGetterTiffGetDataForCoverage:
    """Tests for per-coverage raster extraction routing."""

    def test_returns_array_for_stored_coverage(self, getter: SoilGetterTiff) -> None:
        """A stored coverage ID returns an interpolated NumPy array."""
        expected = np.array([10.0, 20.0])
        with (
            patch(
                "datavia.soil.pipeline.get_layer_by_name",
                return_value={"uri": "/data/soil_clay_0-5cm_mean.tif"},
            ),
            patch(
                "datavia.soil.pipeline.spatial_interpolate",
                return_value=expected,
            ) as mock_interp,
        ):
            coords = np.array([[10.0, 50.0], [11.0, 51.0]])
            result = getter.get_data_for_coverage("clay_0-5cm_mean", coords)
        np.testing.assert_array_equal(result, expected)
        mock_interp.assert_called_once()

    def test_raises_value_error_for_missing_coverage(
        self, getter: SoilGetterTiff
    ) -> None:
        """A coverage ID absent from the database raises a ValueError."""
        with (
            patch(
                "datavia.soil.pipeline.get_layer_by_name",
                return_value=None,
            ),
            pytest.raises(ValueError, match="not found in database"),
        ):
            getter.get_data_for_coverage(
                "clay_0-5cm_mean",
                np.array([[10.0, 50.0]]),
            )

    def test_raises_runtime_error_on_extraction_failure(
        self, getter: SoilGetterTiff
    ) -> None:
        """A raster extraction error is re-raised as a RuntimeError."""
        with (
            patch(
                "datavia.soil.pipeline.get_layer_by_name",
                return_value={"uri": "/data/soil_clay_0-5cm_mean.tif"},
            ),
            patch(
                "datavia.soil.pipeline.spatial_interpolate",
                side_effect=OSError("file not found"),
            ),
            pytest.raises(RuntimeError, match="Failed to extract"),
        ):
            getter.get_data_for_coverage(
                "clay_0-5cm_mean",
                np.array([[10.0, 50.0]]),
            )

    def test_layer_name_built_from_source_name_and_coverage(
        self, getter: SoilGetterTiff
    ) -> None:
        """The DB query uses '{source_name}_{coverage_id}' as the layer name."""
        mock_layer_lookup = MagicMock(return_value=None)
        with (
            patch("datavia.soil.pipeline.get_layer_by_name", mock_layer_lookup),
            pytest.raises(ValueError),
        ):
            getter.get_data_for_coverage(
                "clay_0-5cm_mean",
                np.array([[10.0, 50.0]]),
            )
        mock_layer_lookup.assert_called_once_with("soil_clay_0-5cm_mean", "soil")


# ---------------------------------------------------------------------------
# SoilPipeline.__init__
# ---------------------------------------------------------------------------


class TestSoilPipelineInit:
    """Tests for SoilPipeline default and custom initialisation."""

    def test_default_name(self, pipeline: SoilPipeline) -> None:
        """Default pipeline name is 'soil'."""
        assert pipeline.name == "soil"

    def test_default_properties_include_all_nine(self, pipeline: SoilPipeline) -> None:
        """Default property list covers five SoilGrids and four HiHydroSoil entries."""
        expected = {
            "clay",
            "sand",
            "silt",
            "ph",
            "carbon",
            "field_capacity",
            "wilting_point",
            "porosity",
            "hydraulic_conductivity",
        }
        assert set(pipeline.properties) == expected

    def test_default_depths(self, pipeline: SoilPipeline) -> None:
        """Default SoilGrids depth list is the two shallow layers."""
        assert pipeline.depths == ["0-5cm", "5-15cm"]

    def test_default_statistic(self, pipeline: SoilPipeline) -> None:
        """Default statistic is 'mean'."""
        assert pipeline.statistic == "mean"

    def test_components_none_before_call(self, pipeline: SoilPipeline) -> None:
        """Downloader, saver and getter are None before __call__ is executed."""
        assert pipeline.downloader is None
        assert pipeline.saver is None
        assert pipeline.getter is None

    def test_custom_name_stored(self) -> None:
        """A custom name is stored without modification."""
        custom = SoilPipeline(config={"source": "my_soil"})
        assert custom.name == "my_soil"

    def test_custom_properties_stored(self) -> None:
        """A custom property list is stored as provided."""
        custom = SoilPipeline(config={"source": "soil", "properties": ["clay", "ph"]})
        assert custom.properties == ["clay", "ph"]

    def test_custom_depths_stored(self) -> None:
        """A custom SoilGrids depth list is stored as provided."""
        custom = SoilPipeline(config={"source": "soil", "depths": ["0-5cm"]})
        assert custom.depths == ["0-5cm"]

    def test_custom_value_stored(self) -> None:
        """A custom statistic token is stored as provided."""
        custom = SoilPipeline(config={"source": "soil", "statistic": "Q0.05"})
        assert custom.statistic == "Q0.05"


# ---------------------------------------------------------------------------
# SoilPipeline.configure
# ---------------------------------------------------------------------------


class TestSoilPipelineConfigure:
    """Tests for in-place attribute updates via configure()."""

    def test_updates_properties(self, pipeline: SoilPipeline) -> None:
        """configure() replaces the properties list."""
        pipeline.configure(properties=["clay"])
        assert pipeline.properties == ["clay"]

    def test_updates_depths(self, pipeline: SoilPipeline) -> None:
        """configure() replaces the SoilGrids depth list."""
        pipeline.configure(depths=["15-30cm"])
        assert pipeline.depths == ["15-30cm"]

    def test_updates_statistic(self, pipeline: SoilPipeline) -> None:
        """configure() replaces the statistic token."""
        pipeline.configure(value="Q0.95")
        assert pipeline.statistic == "Q0.95"

    def test_none_arguments_leave_attributes_unchanged(
        self, pipeline: SoilPipeline
    ) -> None:
        """Passing None for any parameter leaves the current value unchanged."""
        original_props = pipeline.properties.copy()
        original_depths = pipeline.depths.copy()
        original_statistic = pipeline.statistic
        pipeline.configure()
        assert pipeline.properties == original_props
        assert pipeline.depths == original_depths
        assert pipeline.statistic == original_statistic

    def test_partial_update_leaves_other_attributes_unchanged(
        self, pipeline: SoilPipeline
    ) -> None:
        """Updating only properties leaves depths and statistic untouched."""
        original_depths = pipeline.depths.copy()
        pipeline.configure(properties=["sand"])
        assert pipeline.depths == original_depths


# ---------------------------------------------------------------------------
# SoilPipeline.get_available_properties
# ---------------------------------------------------------------------------


class TestSoilPipelineGetAvailableProperties:
    """Tests for get_available_properties with various database states."""

    def test_returns_configured_properties_when_no_getter(
        self, pipeline: SoilPipeline
    ) -> None:
        """Without an initialised getter, the configured property list is returned."""
        result = pipeline.get_available_properties()
        assert set(result) == set(pipeline.properties)

    def test_returns_configured_properties_when_database_empty(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """An empty database falls back to the configured property list."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = set()
        result = initialized_pipeline.get_available_properties()
        assert set(result) == set(initialized_pipeline.properties)

    def test_parses_properties_from_stored_coverage_ids(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """Stored coverage IDs are translated to canonical property names."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean",
            "ph_5-15cm_mean",
        }
        result = initialized_pipeline.get_available_properties()
        assert "clay" in result
        assert "ph" in result

    def test_api_aliases_translated_to_canonical_names(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """API names like 'soc' and 'phh2o' are translated to 'carbon' and 'ph'."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "soc_0-5cm_mean",
            "phh2o_5-15cm_mean",
        }
        result = initialized_pipeline.get_available_properties()
        assert "carbon" in result
        assert "ph" in result
        assert "soc" not in result
        assert "phh2o" not in result

    def test_no_duplicates_returned(self, initialized_pipeline: SoilPipeline) -> None:
        """Each canonical property name appears at most once."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean",
            "clay_5-15cm_mean",
        }
        result = initialized_pipeline.get_available_properties()
        assert result.count("clay") == 1


# ---------------------------------------------------------------------------
# SoilPipeline.get_data
# ---------------------------------------------------------------------------


class TestSoilPipelineGetData:
    """Tests for get_data under various storage and parameter conditions."""

    _COORDS = np.array([[10.0, 50.0], [11.0, 51.0]])

    def test_get_data_lazily_initialises_pipeline(self, pipeline: SoilPipeline) -> None:
        """Calling get_data on an uninitialised pipeline triggers lazy __call__.

        The pipeline's downloader, saver, and getter are None after bare
        construction.  On the first get_data call the pipeline initialises
        itself; a mock replaces __call__ so no external services are contacted.
        With no stored coverages the result is an empty dict.
        """
        getter_mock = MagicMock()
        getter_mock.get_stored_coverage_ids.return_value = set()

        def fake_call(*args, **kwargs):
            pipeline.downloader = MagicMock()
            pipeline.saver = MagicMock()
            pipeline.getter = getter_mock
            return pipeline

        with patch.object(SoilPipeline, "__call__", side_effect=fake_call):
            result = pipeline.get_data(self._COORDS)

        assert result == {}

    def test_returns_empty_dict_when_no_stored_ids(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """get_data returns {} when no coverages are stored in the database."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = set()
        result = initialized_pipeline.get_data(self._COORDS)
        assert result == {}

    def test_single_matching_coverage_returns_array(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """Exactly one matching coverage ID returns a plain NumPy array."""
        expected = np.array([15.0, 20.0])
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean"
        }
        initialized_pipeline.getter.get_data_for_coverage.return_value = expected
        result = initialized_pipeline.get_data(
            self._COORDS,
            properties=["clay"],
            depths=["0-5cm"],
            value="mean",
        )
        np.testing.assert_array_equal(result, expected)

    def test_multiple_matching_coverages_return_dict(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """Multiple matching coverage IDs return a dict keyed by coverage ID."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean",
            "clay_5-15cm_mean",
        }
        initialized_pipeline.getter.get_data_for_coverage.return_value = np.zeros(2)
        result = initialized_pipeline.get_data(
            self._COORDS,
            properties=["clay"],
            depths=["0-5cm", "5-15cm"],
            value="mean",
        )
        assert isinstance(result, dict)
        assert "clay_0-5cm_mean" in result
        assert "clay_5-15cm_mean" in result

    def test_unrecognised_properties_do_not_raise(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """Unknown property names produce a warning but do not raise an exception."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = set()
        # Should log a warning but return {} without exception.
        result = initialized_pipeline.get_data(
            self._COORDS, properties=["nonexistent_property"]
        )
        assert result == {}

    def test_string_properties_coerced_to_list(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """A plain string for properties is silently treated as a one-element list."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean"
        }
        initialized_pipeline.getter.get_data_for_coverage.return_value = np.zeros(2)
        # Should not raise a TypeError when properties is a bare string.
        result = initialized_pipeline.get_data(
            self._COORDS, properties="clay", depths="0-5cm", value="mean"
        )
        # Result is an array (single match) or dict — either is acceptable.
        assert result is not None

    def test_failed_extraction_fills_nan_for_multi_coverage(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """A single failed extraction in a multi-coverage request fills NaN,
        not raising an exception that would discard all other results."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_0-5cm_mean",
            "sand_0-5cm_mean",
        }

        def selective_error(coverage_id, coords, **kwargs):
            if coverage_id == "clay_0-5cm_mean":
                raise RuntimeError("extraction failed")
            return np.array([5.0, 6.0])

        initialized_pipeline.getter.get_data_for_coverage.side_effect = selective_error
        result = initialized_pipeline.get_data(
            self._COORDS,
            properties=["clay", "sand"],
            depths=["0-5cm"],
            value="mean",
        )
        assert isinstance(result, dict)
        np.testing.assert_array_equal(result["sand_0-5cm_mean"], [5.0, 6.0])
        assert np.all(np.isnan(result["clay_0-5cm_mean"]))

    def test_returns_empty_dict_when_no_matching_ids(
        self, initialized_pipeline: SoilPipeline
    ) -> None:
        """When stored IDs do not match the requested filters, {} is returned."""
        initialized_pipeline.getter.get_stored_coverage_ids.return_value = {
            "clay_5-15cm_mean"
        }
        result = initialized_pipeline.get_data(
            self._COORDS,
            properties=["clay"],
            depths=["0-5cm"],  # stored depth is 5-15cm, not 0-5cm
            value="mean",
        )
        assert result == {}


# ---------------------------------------------------------------------------
# SoilPipeline.update_data
# ---------------------------------------------------------------------------


class TestSoilPipelineUpdateData:
    """Tests for the incremental download and delta-computation logic."""

    def _setup_update(
        self,
        pipeline: SoilPipeline,
        needed_ids: list[str],
        stored_ids: set[str],
        download_results: list[tuple[str, str]] | None = None,
        save_returns: bool = True,
    ) -> tuple[SoilPipeline, str]:
        """Configure a fully mocked pipeline ready for update_data.

        Returns the pipeline and a temporary directory that acts as the
        data directory so real filesystem side-effects are contained.
        """
        mock_downloader = MagicMock()
        mock_downloader.get_coverage_ids.return_value = needed_ids
        if download_results is None:
            download_results = [
                (f"/work/dir/{cid}.tif", cid) for cid in (set(needed_ids) - stored_ids)
            ]
        mock_downloader.download_coverages.return_value = download_results

        mock_saver = MagicMock()
        mock_saver.sync_files_and_database.return_value = True
        mock_saver.save.return_value = save_returns

        mock_getter = MagicMock()
        mock_getter.get_stored_coverage_ids.return_value = stored_ids

        pipeline.downloader = mock_downloader
        pipeline.saver = mock_saver
        pipeline.getter = mock_getter

        return pipeline

    def test_returns_true_when_nothing_to_download(
        self, pipeline: SoilPipeline
    ) -> None:
        """update_data returns True immediately when all coverages are present."""
        needed = ["clay_0-5cm_mean", "sand_0-5cm_mean"]
        self._setup_update(pipeline, needed_ids=needed, stored_ids=set(needed))
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is True
        pipeline.downloader.download_coverages.assert_not_called()

    def test_downloads_only_missing_coverages(self, pipeline: SoilPipeline) -> None:
        """Only the delta (needed minus stored) is passed to download_coverages."""
        needed = ["clay_0-5cm_mean", "sand_0-5cm_mean"]
        stored = {"clay_0-5cm_mean"}
        self._setup_update(pipeline, needed_ids=needed, stored_ids=stored)
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            pipeline.update_data()
        call_ids = pipeline.downloader.download_coverages.call_args.args[0]
        assert call_ids == ["sand_0-5cm_mean"]

    def test_returns_true_on_full_success(self, pipeline: SoilPipeline) -> None:
        """update_data returns True when all downloaded coverages are saved."""
        needed = ["clay_0-5cm_mean"]
        self._setup_update(pipeline, needed_ids=needed, stored_ids=set())
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is True

    def test_returns_false_when_no_files_downloaded(
        self, pipeline: SoilPipeline
    ) -> None:
        """update_data returns False when download_coverages returns no files."""
        needed = ["clay_0-5cm_mean"]
        self._setup_update(
            pipeline, needed_ids=needed, stored_ids=set(), download_results=[]
        )
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is False

    def test_returns_false_when_saver_fails(self, pipeline: SoilPipeline) -> None:
        """update_data returns False when the saver reports a failure."""
        needed = ["clay_0-5cm_mean"]
        self._setup_update(
            pipeline, needed_ids=needed, stored_ids=set(), save_returns=False
        )
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is False

    def test_returns_false_on_partial_download_failure(
        self, pipeline: SoilPipeline
    ) -> None:
        """update_data returns False when download_coverages omits some requested IDs.

        download_coverages silently drops coverage IDs that failed to download.
        The pipeline must detect the gap and propagate False even though the
        returned files were all saved successfully.
        """
        needed = ["clay_0-5cm_mean", "sand_0-5cm_mean"]
        # Only clay was downloaded; sand silently dropped by the downloader.
        self._setup_update(
            pipeline,
            needed_ids=needed,
            stored_ids=set(),
            download_results=[("/work/dir/clay_0-5cm_mean.tif", "clay_0-5cm_mean")],
            save_returns=True,
        )
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is False

    def test_api_aliases_in_stored_ids_prevent_redundant_download(
        self, pipeline: SoilPipeline
    ) -> None:
        """Stored coverage IDs under API names (e.g. 'soc') are not re-downloaded
        when the pipeline requests equivalent canonical names (e.g. 'carbon')."""
        needed = ["carbon_0-5cm_mean"]
        # Stored under the SoilGrids API name 'soc' rather than the canonical 'carbon'.
        stored = {"soc_0-5cm_mean"}
        self._setup_update(pipeline, needed_ids=needed, stored_ids=stored)
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            result = pipeline.update_data()
        assert result is True
        pipeline.downloader.download_coverages.assert_not_called()

    def test_string_depths_coerced_without_error(self, pipeline: SoilPipeline) -> None:
        """Passing a plain string for depths does not raise a TypeError."""
        needed = ["clay_0-5cm_mean"]
        self._setup_update(pipeline, needed_ids=needed, stored_ids=set(needed))
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            # depths="0-5cm" is a string — should be silently coerced.
            result = pipeline.update_data(depths="0-5cm")
        assert result is True

    def test_sync_files_always_called_when_components_ready(
        self, pipeline: SoilPipeline
    ) -> None:
        """sync_files_and_database is called on every update_data invocation
        so that manually deleted files are detected before the delta is computed."""
        needed = ["clay_0-5cm_mean"]
        self._setup_update(pipeline, needed_ids=needed, stored_ids=set(needed))
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            pipeline.update_data()
        pipeline.saver.sync_files_and_database.assert_called_once()


class TestSoilPipelineUpdateDataReprojectParams:
    """Tests that reproject and resolution_m kwargs are forwarded to saver.save."""

    def _setup_pipeline_for_download(
        self,
        pipeline: SoilPipeline,
        coverage_id: str = "clay_0-5cm_mean",
    ) -> None:
        """Wire a pipeline so exactly one coverage is missing and will be saved."""
        mock_downloader = MagicMock()
        mock_downloader.get_coverage_ids.return_value = [coverage_id]
        mock_downloader.download_coverages.return_value = [
            (f"/work/{coverage_id}.tif", coverage_id)
        ]

        mock_saver = MagicMock()
        mock_saver.sync_files_and_database.return_value = True
        mock_saver.save.return_value = True

        mock_getter = MagicMock()
        mock_getter.get_stored_coverage_ids.return_value = set()

        pipeline.downloader = mock_downloader
        pipeline.saver = mock_saver
        pipeline.getter = mock_getter

    def test_reproject_true_forwarded_to_saver_save(
        self, pipeline: SoilPipeline
    ) -> None:
        """saver.save is called with reproject=True when the flag is set."""
        self._setup_pipeline_for_download(pipeline)
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            pipeline.update_data(reproject=True)

        pipeline.saver.save.assert_called_once()
        _, call_kwargs = pipeline.saver.save.call_args
        assert call_kwargs.get("reproject") is True

    def test_resolution_m_forwarded_to_saver_save(self, pipeline: SoilPipeline) -> None:
        """saver.save receives the resolution_m value passed to update_data."""
        self._setup_pipeline_for_download(pipeline)
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            pipeline.update_data(reproject=True, resolution_m=250)

        _, call_kwargs = pipeline.saver.save.call_args
        assert call_kwargs.get("resolution_m") == 250

    def test_default_call_passes_reproject_false(self, pipeline: SoilPipeline) -> None:
        """By default saver.save is called with reproject=False and resolution_m=None."""
        self._setup_pipeline_for_download(pipeline)
        with (
            patch("datavia.soil.pipeline.get_config") as mock_cfg,
            patch("datavia.soil.pipeline.tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_cfg.return_value.data_directory = tempfile.gettempdir()
            mock_tmpdir.return_value.__enter__ = lambda s: tempfile.gettempdir()
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            pipeline.update_data()

        _, call_kwargs = pipeline.saver.save.call_args
        assert call_kwargs.get("reproject") is False
        assert call_kwargs.get("resolution_m") is None
