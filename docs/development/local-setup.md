# Local Development Setup for Datavia

This repository uses a multi-package structure with local file references that need to be configured for each development environment.

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/tree-d/datavia.git
   cd datavia
   ```

4. **Only if necessary: Modify Pixi.toml -- DO NOT PUSH THIS**
They are installed but without a real path, if you want to change this locally do this:
But be aware that you do not push these changes!

   ```bash
   # Install core only
   pixi add --pypi "datavia@file://$(pwd)" --editable
   
   # Or install with specific pipelines
   pixi add --pypi "datavia[elevation]@file://$(pwd)" --editable
   pixi add --pypi "datavia[soil]@file://$(pwd)" --editable
   ```

## Project Structure

```
datavia/
├── README.md                  # Main project README
├── pyproject.toml             # Core package configuration
├── scripts/
│   └── version.sh             # Version management
├── datavia/                   # Core package source
├── packages/                  # Extension packages
│   ├── elevation/
│   │   └── pyproject.toml
│   └── soil/
│       └── pyproject.toml
└── tests/                     # Test suite
```

## How It Works

1. **Local Development:** All packages are installed in editable mode
2. **Multi-package:** Core + extension packages work together

```bash
# Code quality tasks (use dev environment)
pixi run -e dev format          # Auto-format code
pixi run -e dev format-check    # Check formatting without changes
pixi run -e dev lint            # Run ruff linting
pixi run -e dev type-check      # Run mypy type checking
pixi run -e dev security-scan   # Run bandit security scan

# Testing tasks
pixi run -e dev test-unit       # Run unit tests
pixi run -e dev test-integration # Run integration tests  
pixi run -e dev test-all        # Run all tests with coverage

# Complete CI check
pixi run -e dev ci-check        # Run all checks like CI pipeline
```