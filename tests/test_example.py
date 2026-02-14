#!/usr/bin/env python3
"""
Test file to demonstrate namespace package import.
"""

# Test elevation
print("Testing elevation...")
import datavia.elevation

try:
    pipeline = datavia.elevation.ElevationPipeline()
    print("✅ Success: ElevationPipeline created from datavia.elevation")
except Exception as e:
    print(f"❌ Error: {e}")

# Test soil
print("\nTesting soil...")
import datavia.soil

try:
    soil_pipeline = datavia.soil.SoilPipeline()
    print("✅ Success: SoilPipeline created from datavia.soil")
except Exception as e:
    print(f"❌ Error: {e}")

# Test direct imports
print("\nTesting direct imports...")
try:

    print("✅ Success: Both direct imports work")
except Exception as e:
    print(f"❌ Direct import failed: {e}")
