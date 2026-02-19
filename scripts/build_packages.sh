#!/bin/bash

# Build script for datavia packages
# Builds packages in dependency order: core -> pipelines

set -euo pipefail  # Exit on any error and fail on unset variables

PYTHON_BIN=${PYTHON_BIN:-python3}

check_build_tool() {
	if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
		echo "❌ Python executable not found: $PYTHON_BIN"
		exit 1
	fi

	if ! "$PYTHON_BIN" -m build --version >/dev/null 2>&1; then
		echo "❌ Python build module not available. Install with: $PYTHON_BIN -m pip install build"
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
"$PYTHON_BIN" -m build

# Build pipeline packages
echo "📦 Building elevation package..."
cd packages/elevation
"$PYTHON_BIN" -m build
cd ../..

echo "📦 Building soil package..."  
cd packages/soil
"$PYTHON_BIN" -m build
cd ../..

echo "✅ All packages built successfully!"