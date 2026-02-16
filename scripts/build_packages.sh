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