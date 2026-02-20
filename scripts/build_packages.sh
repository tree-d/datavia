#!/bin/bash

# Build script for datavia packages
# Builds packages in dependency order: core -> pipelines
# Uses pixi for environment management

set -euo pipefail  # Exit on any error and fail on unset variables

check_build_tool() {
	if ! command -v pixi >/dev/null 2>&1; then
		echo "❌ pixi not found. Install from https://pixi.sh"
		exit 1
	fi

	if ! pixi run --environment dev python -m build --version >/dev/null 2>&1; then
		echo "❌ Python build module not available. Ensure 'dev' environment is installed with: pixi install -e dev"
		exit 1
	fi
}

echo "🔨 Building datavia packages..."
check_build_tool

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info/
rm -rf packages/*/dist/ packages/*/build/ packages/*/*.egg-info/

# Build core datavia package first
echo "📦 Building core datavia package..."
pixi run --environment dev python -m build

# Build pipeline packages
echo "📦 Building elevation package..."
cd packages/elevation
pixi run --environment dev python -m build
cd ../..

echo "📦 Building soil package..."  
cd packages/soil
pixi run --environment dev python -m build
cd ../../..

echo "✅ All packages built successfully!"