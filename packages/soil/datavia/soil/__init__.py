"""Datavia soil namespace package.

Contains soil-related pipeline implementations.
"""

from .pipeline import SoilPipeline

__all__ = ["SoilPipeline"]
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
