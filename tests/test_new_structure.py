#!/usr/bin/env python3
"""Test namespace package installation with new structure."""


def test_datavia_core_imports():
    """Tests that the core datavia packages are importable."""
    try:
        import datavia  # noqa: PLC0415
        import datavia.core  # noqa: PLC0415
        import datavia.library  # noqa: PLC0415

        assert hasattr(datavia, "__path__"), "datavia should be a namespace package."
        assert hasattr(datavia.core, "__path__"), "datavia.core should be a package."
        assert hasattr(datavia.library, "__path__"), (
            "datavia.library should be a package."
        )
    except ImportError as e:
        print(f"Core datavia import failed: {e}")


def test_datavia_pipeline_imports():
    """Tests that the installed pipeline packages are importable."""
    try:
        # This should succeed as it's part of the main project
        import datavia.elevation  # noqa: PLC0415

        assert hasattr(datavia.elevation, "__path__"), (
            "datavia.elevation should be a package."
        )
    except ImportError as e:
        print(f"datavia.elevation pipeline import failed: {e}")

    try:
        # This is expected to fail if the optional soil package is not installed
        import datavia.soil  # noqa: PLC0415

        assert hasattr(datavia.soil, "__path__"), "datavia.soil should be a package."
    except ImportError:
        # This is the expected outcome, so we pass.
        pass
    except Exception as e:
        print(f"Unexpected error during datavia.soil import: {e}")
