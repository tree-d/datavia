#!/bin/bash
# Build script for creating all datavia packages

set -e

echo "Building Datavia packages..."

# Build core package
echo "Building datavia (core)..."
python -m build --wheel

# Build elevation package
echo "Building datavia-elevation..."
python -m build --wheel --config-setting="--config-file=pyproject_elevation.toml"

# Build soil package  
echo "Building datavia-soil..."
python -m build --wheel --config-setting="--config-file=pyproject_soil.toml"

echo "All packages built successfully!"
echo "Install with:"
echo "  pip install dist/datavia-*.whl                    # Core only"
echo "  pip install dist/datavia-*-*.whl dist/datavia_elevation-*.whl  # Core + elevation"
echo "  pip install dist/datavia-*-*.whl dist/datavia_soil-*.whl       # Core + soil"