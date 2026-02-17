#!/usr/bin/env python3
"""
Test file to demonstrate namespace package import.
"""

import datavia.elevation
import datavia.soil


def test_namespace_imports():
    """Test that namespace packages can be imported and used."""
    # Test elevation
    try:
        # This should succeed as the elevation package is installed
        from datavia.elevation import ElevationPipeline

        instance = ElevationPipeline()
        assert isinstance(instance, datavia.elevation.ElevationPipeline), (
            "Should be an instance of ElevationPipeline"
        )
    except ImportError as e:
        assert False, f"Failed to import ElevationPipeline: {e}"

    # Test soil - this is expected to fail if not installed
    try:
        from datavia.soil import SoilPipeline

        # This part of the test will only run if `datavia-soil` is installed
        instance = SoilPipeline()
        assert isinstance(instance, datavia.soil.SoilPipeline), (
            "Should be an instance of SoilPipeline"
        )
    except ImportError:
        # This is an expected outcome if the soil package is not installed.
        # In a real test suite, you might use pytest.importorskip("datavia.soil").
        pass
    except Exception as e:
        assert False, f"An unexpected error occurred when importing SoilPipeline: {e}"
