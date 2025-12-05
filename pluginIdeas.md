# Datavia Plugin Architecture Implementation Plan

## Overview
Based on our conversation analysis, we're implementing a **full plugin architecture** for datavia pipelines. This aligns perfectly with the project timeline: GitHub cleanup → CI/CD → PyPI → finalize soil pipeline.

## Current State
- Core `datavia` package exists with basic structure
- `datavia-pipelines` package exists separately 
- Registry functionality exists within the `Datavia` class
- Need to separate into plugin ecosystem

## Recommended Architecture

### Core Package Structure
```
datavia/                    (Main package - core only)
├── core/
│   ├── interfaces.py      (Pipeline interface + PluginRegistry)
│   └── datavia.py         (Main Datavia class with registry)
├── cli.py                 (Plugin discovery & execution commands)
└── __init__.py            (Auto-plugin discovery)

datavia-soil/              (Separate plugin package)
datavia-elevation/         (Separate plugin package)
datavia-climate/           (Future plugin package)
```

## Key Changes Required

### 1. Plugin Interface (datavia/core/interfaces.py)
```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class Pipeline(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique pipeline identifier"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """Pipeline version"""
        pass
    
    @abstractmethod
    def execute(self, config: Dict[str, Any]) -> Any:
        """Execute pipeline with configuration"""
        pass
    
    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate configuration before execution"""
        pass
```

### 2. Registry Integration in Datavia Class
The registry functionality remains **inside the Datavia class** as requested:

```python
# datavia/core/datavia.py
class Datavia:
    def __init__(self):
        self._pipeline_registry = {}
        self._discover_plugins()
    
    def register_pipeline(self, pipeline: Pipeline) -> None:
        """Register a pipeline plugin"""
        self._pipeline_registry[pipeline.name] = pipeline
    
    def _discover_plugins(self) -> None:
        """Auto-discover installed plugins via entry points"""
        import pkg_resources
        for entry_point in pkg_resources.iter_entry_points('datavia.pipelines'):
            try:
                pipeline_class = entry_point.load()
                pipeline = pipeline_class()
                self.register_pipeline(pipeline)
            except Exception as e:
                print(f"Failed to load plugin {entry_point.name}: {e}")
    
    def get_pipeline(self, name: str) -> Optional[Pipeline]:
        return self._pipeline_registry.get(name)
    
    def list_pipelines(self) -> Dict[str, Pipeline]:
        return self._pipeline_registry.copy()
```

### 3. Plugin Package Structure (Example: datavia-soil)
```
datavia-soil/
├── pyproject.toml         (with entry points)
├── datavia_soil/
│   ├── __init__.py
│   └── pipeline.py        (SoilPipeline class)
```

### 4. Easy Plugin Creation Template
For users to create their own plugins easily:

```python
# my-custom-pipeline/my_pipeline.py
from datavia.core.interfaces import Pipeline
from typing import Dict, Any

class MyCustomPipeline(Pipeline):
    @property
    def name(self) -> str:
        return "my-custom"
    
    @property  
    def description(self) -> str:
        return "My custom data processing pipeline"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    def validate_config(self, config: Dict[str, Any]) -> bool:
        # Check required fields
        return "input_path" in config and "output_path" in config
    
    def execute(self, config: Dict[str, Any]) -> Any:
        # User's custom logic here
        input_path = config["input_path"] 
        output_path = config["output_path"]
        
        # Process data...
        
        return {"status": "success", "output": output_path}

# pyproject.toml entry point:
# [project.entry-points."datavia.pipelines"]
# my-custom = "my_pipeline:MyCustomPipeline"
```

## Benefits of This Approach

### For Users:
- **Simple installation**: `pip install datavia datavia-soil`
- **Easy plugin creation**: Just inherit from `Pipeline` class
- **Auto-discovery**: Plugins register automatically when installed
- **Clean CLI**: `datavia list-plugins`, `datavia run-pipeline soil --config config.json`

### For Development:
- **Focused repos**: Each plugin has its own repository
- **Independent CI/CD**: Each package has separate build pipeline
- **Modular releases**: Core and plugins can release independently
- **Community friendly**: Easy for others to contribute new plugins

## Implementation Timeline

1. **GitHub Cleanup Phase**:
   - Split `datavia-pipelines` content into separate plugin repos
   - Update core `datavia` package structure
   - Remove pipeline-specific dependencies from core

2. **CI/CD Phase**:
   - Setup GitHub Actions for each repository
   - Automated testing and building
   - PyPI upload automation

3. **PyPI Upload Phase**:
   - Publish `datavia` core package
   - Publish `datavia-soil` plugin package  
   - Publish `datavia-elevation` plugin package

4. **Finalize Soil Pipeline**:
   - Complete soil pipeline functionality as independent plugin
   - Documentation and examples

## Technical Notes

- **Runtime Impact**: Minimal - plugin discovery happens at startup only
- **Plugin Registry**: Integrated into main `Datavia` class as requested
- **Entry Points**: Used for auto-discovery (`datavia.pipelines` group)
- **Dependencies**: Each plugin manages its own dependencies
- **Backward Compatibility**: Can be maintained during transition

## Migration Strategy

1. Keep current structure working during transition
2. Move pipeline files to new plugin packages
3. Update imports and dependencies
4. Test plugin discovery and execution
5. Remove old pipeline code from core package

This architecture perfectly supports the goal of making datavia easily extensible while maintaining a clean, focused core package.