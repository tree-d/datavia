# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "Datavia"
copyright = "2025, Tree-D Research Group"
author = "Michael Berg"
release = "1.0.0-dev"
version = "1.0.0-dev"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx.ext.doctest",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "en"

# -- Autodoc configuration --------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "private-members": False,
    "special-members": "__init__",
    "inherited-members": True,
    "show-inheritance": True,
}

# -- Doctest configuration --------------------------------------------------
doctest_global_setup = """
import os
import sys
import numpy as np
from pathlib import Path

# Set up test environment
os.environ['DATAVIA_TEST_MODE'] = '1'
test_data_dir = Path('../tests/data')
test_data_dir.mkdir(exist_ok=True)
"""

doctest_test_doctest_blocks = "default"

# -- Napoleon settings -------------------------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
html_static_path = ["_static"]

# Theme options for alabaster theme
html_theme_options = {
    "description": "Datavia - Data integration framework",
    "github_user": "tree-d",
    "github_repo": "datavia",
    "show_powered_by": False,
}

# -- Intersphinx mapping ----------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "rasterio": ("https://rasterio.readthedocs.io/en/latest/", None),
}
