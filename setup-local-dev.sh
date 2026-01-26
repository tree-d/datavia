#!/bin/bash

# Setup script for datavia local development
# This script generates pyproject.toml files from templates with correct absolute paths

set -e

# Get the absolute path of the datavia root directory
DATAVIA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔧 Setting up datavia for local development..."
echo "📁 Datavia root: $DATAVIA_ROOT"

# Generate main pyproject.toml from template
echo "📝 Generating main pyproject.toml..."
sed "s|{{DATAVIA_ROOT}}|$DATAVIA_ROOT|g" "$DATAVIA_ROOT/pyproject.toml.template" > "$DATAVIA_ROOT/pyproject.toml"

# Generate elevation package pyproject.toml
echo "📝 Generating elevation pyproject.toml..."
sed "s|{{DATAVIA_ROOT}}|$DATAVIA_ROOT|g" "$DATAVIA_ROOT/packages/elevation/pyproject.toml.template" > "$DATAVIA_ROOT/packages/elevation/pyproject.toml"

# Generate soil package pyproject.toml
echo "📝 Generating soil pyproject.toml..."
sed "s|{{DATAVIA_ROOT}}|$DATAVIA_ROOT|g" "$DATAVIA_ROOT/packages/soil/pyproject.toml.template" > "$DATAVIA_ROOT/packages/soil/pyproject.toml"

echo "✅ Setup complete!"
echo ""
echo "📋 Now you can install with:"
echo "  pixi add --pypi \"datavia@file://$DATAVIA_ROOT\" --editable"
echo "  pixi add --pypi \"datavia[elevation]@file://$DATAVIA_ROOT\" --editable"
echo "  pixi add --pypi \"datavia[soil]@file://$DATAVIA_ROOT\" --editable"