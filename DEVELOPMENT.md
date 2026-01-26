# Local Development Setup for Datavia

This repository uses a multi-package structure with local file references that need to be configured for each development environment.

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/tree-d/datavia.git
   cd datavia
   ```

2. **Run the setup script:**
   ```bash
   ./setup-local-dev.sh
   ```
   This generates the correct `pyproject.toml` files with absolute paths for your system.

3. **Install the package:**
   ```bash
   # Install core only
   pixi add --pypi "datavia@file://$(pwd)" --editable
   
   # Or install with specific pipelines
   pixi add --pypi "datavia[elevation]@file://$(pwd)" --editable
   pixi add --pypi "datavia[soil]@file://$(pwd)" --editable
   ```

## How It Works

### Template System
- **Templates:** `*.toml.template` files contain placeholders like `{{DATAVIA_ROOT}}`
- **Generated:** `pyproject.toml` files are generated with absolute paths
- **Git:** Only templates are tracked, generated files are ignored

### Package Structure
```
datavia/
├── pyproject.toml.template     # Main package template
├── pyproject.toml             # Generated (git-ignored)
├── packages/
│   ├── elevation/
│   │   ├── pyproject.toml.template
│   │   └── pyproject.toml     # Generated (git-ignored)
│   └── soil/
│       ├── pyproject.toml.template
│       └── pyproject.toml     # Generated (git-ignored)
└── setup-local-dev.sh         # Setup script
```

### Why This Approach?

1. **Portable:** Works on any system after running `setup-local-dev.sh`
2. **Git-friendly:** No hardcoded paths in version control
3. **Modular:** True separate packages that can be installed independently
4. **Local-first:** Optimized for local development workflow

## Development Workflow

```bash
# 1. Initial setup (once per clone)
./setup-local-dev.sh

# 2. Install what you need
pixi add --pypi "datavia[elevation]@file://$(pwd)" --editable

# 3. Make changes to code...

# 4. If you modify templates, re-run setup
./setup-local-dev.sh
```

## CI/CD Considerations

For CI/CD pipelines, you can:
1. Run `./setup-local-dev.sh` as part of the build process
2. Or use a different strategy (like Option 1: single package) for releases