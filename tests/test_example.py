#!/usr/bin/env python3
"""
Test file to demonstrate namespace package import.
"""

import datavia.elevation
import datavia.soil


def test_namespace_imports():
    """Test that namespace packages can be imported and used."""
    # Test elevation
    print("Testing elevation...")

    try:
        datavia.elevation.ElevationPipeline()
        print("✅ Success: ElevationPipeline created from datavia.elevation")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test soil
    print("\nTesting soil...")

    try:
        datavia.soil.SoilPipeline()
        print("✅ Success: SoilPipeline created from datavia.soil")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test direct imports
    print("\nTesting direct imports...")
    try:
        print("✅ Success: Both direct imports work")
    except Exception as e:
        print(f"❌ Direct import failed: {e}")


if __name__ == "__main__":
    test_namespace_imports()
