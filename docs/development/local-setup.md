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
   pixi add --pypi "datavia@file:///path/to/your/datavia" --editable
   
   # Or install with specific pipelines
   pixi add --pypi "datavia-elevation@file:///path/to/your/datavia/packages/elevation/pyproject.toml" --editable
   pixi add --pypi "datavia-soil@file:///path/to/your/datavia/packages/soil/pyproject.toml" --editable
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
# Please have a look at pixi.toml for more useful tasks
pixi run format          # Auto-format code
pixi run format-check    # Check formatting without changes
pixi run lint            # Run ruff linting
pixi run type-check      # Run mypy type checking
pixi run security-scan   # Run bandit security scan

# Testing tasks
pixi run test-unit       # Run unit tests
pixi run test-integration # Run integration tests  
pixi run test-all        # Run all tests with coverage

# Complete CI check
pixi run ci-check        # Run all checks like CI pipeline
pixi run ci-pr-check
```