#!/bin/bash

# Build script for datavia packages
# Builds packages in dependency order: core -> pipelines

set -e  # Exit on any error

echo "🔨 Building datavia packages..."

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info/
rm -rf packages/*/dist/ packages/*/build/ packages/*/*.egg-info/

# Build core datavia package first
echo "📦 Building core datavia package..."
python -m build

# Build pipeline packages
echo "📦 Building elevation package..."
cd packages/elevation
python -m build
cd ../..

echo "📦 Building soil package..."  
cd packages/soil
python -m build
cd ../..

echo "✅ All packages built successfully!"
echo ""
echo "📋 To install locally with pixi:"
echo "  # Install core only:"
echo "  pixi add --pypi \"datavia@file:///home/bergmi/tree-D_data-integration/datavia\" --editable"
echo ""  
echo "  # Install with elevation:"
echo "  pixi add --pypi \"datavia[elevation]@file:///home/bergmi/tree-D_data-integration/datavia\" --editable"
echo ""
echo "  # Install with soil:"
echo "  pixi add --pypi \"datavia[soil]@file:///home/bergmi/tree-D_data-integration/datavia\" --editable"
echo ""
echo "  # Install with all pipelines:"
echo "  pixi add --pypi \"datavia[pipelines]@file:///home/bergmi/tree-D_data-integration/datavia\" --editable"