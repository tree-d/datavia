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
├── pyproject.toml.template    # Template for main package
├── scripts/
│   └── version.sh             # Version management
├── datavia/                   # Core package source
├── packages/                  # Extension packages
│   ├── elevation/
│   └── soil/
└── tests/                     # Test suite
```

## How It Works

1. **Local Development:** All packages are installed in editable mode
2. **Multi-package:** Core + extension packages work together