#!/usr/bin/env python3
"""Comprehensive namespace package debugging test."""

import sys
import os
import importlib.util


def debug_installation():
    print("=== DEBUGGING NAMESPACE PACKAGE INSTALLATION ===")

    # 1. Check Python path
    print("\n1. Python Path:")
    for i, path in enumerate(sys.path):
        print(f"   {i}: {path}")

    # 2. Find all datavia installations
    print("\n2. Datavia Locations:")
    datavia_locations = []
    for path in sys.path:
        datavia_path = os.path.join(path, "datavia")
        if os.path.exists(datavia_path):
            datavia_locations.append(datavia_path)
            print(f"   📁 {datavia_path}")
            try:
                contents = os.listdir(datavia_path)
                print(f"      Contents: {sorted(contents)}")

                # Check __init__.py
                init_file = os.path.join(datavia_path, "__init__.py")
                if os.path.exists(init_file):
                    with open(init_file) as f:
                        content = f.read()
                        print(f"      __init__.py: {len(content)} chars")
                        if "extend_path" in content:
                            print("      ✅ Has extend_path")
                        else:
                            print("      ❌ No extend_path")
                        if content.strip():
                            print(f"      Content preview: {content[:100]}...")
                else:
                    print("      ❌ No __init__.py")
            except Exception as e:
                print(f"      ❌ Error reading: {e}")

    # 3. Test basic import
    print("\n3. Basic Import Test:")
    try:
        import datavia

        print("   ✅ datavia imported successfully")
        print(f"   📍 __file__: {getattr(datavia, '__file__', 'Not available')}")
        print(f"   📁 __path__: {getattr(datavia, '__path__', 'Not available')}")

        # Check if it's a namespace package
        if hasattr(datavia, "__path__") and len(datavia.__path__) > 1:
            print("   ✅ Multiple paths detected (namespace package working)")
        else:
            print("   ⚠️  Only single path (might not be namespace package)")

    except ImportError as e:
        print(f"   ❌ Failed to import datavia: {e}")
        return

    # 4. Test core modules
    print("\n4. Core Module Tests:")
    for module_name in ["core", "library"]:
        try:
            module = importlib.import_module(f"datavia.{module_name}")
            print(f"   ✅ datavia.{module_name} imported")
        except ImportError as e:
            print(f"   ❌ datavia.{module_name} failed: {e}")

    # 5. Test pipeline modules
    print("\n5. Pipeline Module Tests:")
    for pipeline in ["soil", "elevation"]:
        try:
            module = importlib.import_module(f"datavia.{pipeline}")
            print(f"   ✅ datavia.{pipeline} imported")

            # Try to find the pipeline file
            if hasattr(module, "__file__"):
                print(f"      📍 Location: {module.__file__}")
            elif hasattr(module, "__path__"):
                print(f"      📁 Package path: {module.__path__}")

        except ImportError as e:
            print(f"   ❌ datavia.{pipeline} failed: {e}")

            # Debug: Check if the directory exists in any datavia location
            for loc in datavia_locations:
                pipeline_path = os.path.join(loc, pipeline)
                if os.path.exists(pipeline_path):
                    print(f"      🔍 Found {pipeline} directory at: {pipeline_path}")
                    print(f"         Contents: {os.listdir(pipeline_path)}")

    # 6. Check installed packages
    print("\n6. Installed Package Check:")
    try:
        import pkg_resources

        for dist in pkg_resources.working_set:
            if "datavia" in dist.project_name.lower():
                print(f"   📦 {dist.project_name} {dist.version} at {dist.location}")
    except ImportError:
        print("   ⚠️  pkg_resources not available")


def test_manual_installation():
    """Test installing packages in a clean way"""
    print("\n\n=== MANUAL INSTALLATION TEST ===")

    # Check current working directory
    print(f"Current directory: {os.getcwd()}")

    # Check if dist files exist
    core_wheel = "dist/datavia-1.0.1.dev0-py3-none-any.whl"
    soil_wheel = "packages/soil/dist/datavia_soil-1.0.1.dev0-py3-none-any.whl"
    elevation_wheel = (
        "packages/elevation/dist/datavia_elevation-1.0.1.dev0-py3-none-any.whl"
    )

    print("\nChecking wheel files:")
    for wheel, name in [
        (core_wheel, "Core"),
        (soil_wheel, "Soil"),
        (elevation_wheel, "Elevation"),
    ]:
        if os.path.exists(wheel):
            print(f"   ✅ {name}: {wheel}")
        else:
            print(f"   ❌ {name}: {wheel} NOT FOUND")


if __name__ == "__main__":
    debug_installation()
    test_manual_installation()
