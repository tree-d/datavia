#!/bin/bash
# Version management script for Datavia
# Usage: ./scripts/version.sh [major|minor|patch|dev]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYPROJECT_FILE="$PROJECT_ROOT/pyproject.toml"

# Function to get current version
get_current_version() {
    grep '^version = ' "$PYPROJECT_FILE" | sed 's/version = "\(.*\)"/\1/' | tr -d '"'
}

# Function to update version in pyproject.toml
update_version() {
    local new_version="$1"
    sed -i.bak "s/version = \".*\"/version = \"$new_version\"/g" "$PYPROJECT_FILE"
    rm -f "${PYPROJECT_FILE}.bak"
    echo "Updated version to: $new_version"
}

# Function to increment version
increment_version() {
    local version="$1"
    local type="$2"
    
    # Remove -dev suffix if present
    version="${version%-dev}"
    
    IFS='.' read -ra ADDR <<< "$version"
    major="${ADDR[0]}"
    minor="${ADDR[1]}"
    patch="${ADDR[2]}"
    
    case "$type" in
        "major")
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        "minor")
            minor=$((minor + 1))
            patch=0
            ;;
        "patch")
            patch=$((patch + 1))
            ;;
        *)
            echo "Invalid increment type: $type"
            exit 1
            ;;
    esac
    
    echo "$major.$minor.$patch"
}

# Main logic
if [ $# -eq 0 ]; then
    echo "Current version: $(get_current_version)"
    echo ""
    echo "Usage: $0 [major|minor|patch|dev|set <version>]"
    echo ""
    echo "Examples:"
    echo "  $0 patch        # 1.0.0 -> 1.0.1"
    echo "  $0 minor        # 1.0.0 -> 1.1.0"
    echo "  $0 major        # 1.0.0 -> 2.0.0"
    echo "  $0 dev          # 1.0.0 -> 1.0.0-dev"
    echo "  $0 set 1.2.3    # Set version to 1.2.3"
    exit 0
fi

current_version=$(get_current_version)
echo "Current version: $current_version"

case "$1" in
    "major"|"minor"|"patch")
        new_version=$(increment_version "$current_version" "$1")
        update_version "$new_version"
        ;;
    "dev")
        # Add -dev suffix
        base_version="${current_version%-dev}"
        new_version="$base_version-dev"
        update_version "$new_version"
        ;;
    "set")
        if [ -z "$2" ]; then
            echo "Error: Please provide a version number"
            echo "Usage: $0 set <version>"
            exit 1
        fi
        update_version "$2"
        ;;
    *)
        echo "Invalid command: $1"
        echo "Usage: $0 [major|minor|patch|dev|set <version>]"
        exit 1
        ;;
esac