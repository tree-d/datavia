#!/usr/bin/env python3
"""
Documentation Testing Script with I/O Support

This script removes +SKIP directives from doctest blocks and runs the
documentation tests with DATAVIA_DOCTEST_IO=1 to test I/O-dependent examples.

Usage:
    python scripts/test_docs_with_io.py [--verbose]

    # Run with verbose output
    python scripts/test_docs_with_io.py --verbose

Security note:
    The project ``.env`` file (which may contain database passwords or API keys)
    is **not** copied into the temporary docs directory by default.  Set
    ``DATAVIA_DOCTEST_COPY_ENV=1`` to opt in when live credentials are required
    for doctest runs.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from datavia.cli_utils import start_datavia_environment, stop_datavia_environment


def remove_skip_directives(content):
    """Remove all +SKIP directives from doctest code.

    This allows I/O-dependent tests to run when DATAVIA_DOCTEST_IO=1.

    Parameters
    ----------
    content : str
        RST file content

    Returns
    -------
    str
        Content with +SKIP directives removed
    """
    # Pattern to match +SKIP directives (with or without other directives)
    # Examples:
    # >>> some_code() # doctest: +SKIP
    # >>> some_code() # doctest: +SKIP +ELLIPSIS
    # >>> some_code() # doctest: +ELLIPSIS +SKIP

    # Replace " # doctest: +SKIP" with empty string if it's the only directive
    content = re.sub(r" # doctest: \+SKIP(?!\s*\+)", "", content)

    # Replace " +SKIP " with space if there are other directives
    content = re.sub(r" \+SKIP\s+", " ", content)

    # Replace "+SKIP " at end with empty if there are other directives
    content = re.sub(r"\+SKIP\s*$", "", content, flags=re.MULTILINE)

    return content


def process_rst_files(docs_dir, project_root):
    """Process RST files to remove +SKIP directives.

    Creates temporary copies of RST files with +SKIP directives removed,
    and also copies the tests directory for doctest to access.

    Parameters
    ----------
    docs_dir : Path
        Documentation directory
    project_root : Path
        Project root directory

    Returns
    -------
    Path
        Temporary directory with processed files
    """
    temp_dir = tempfile.mkdtemp(prefix="datavia_docs_")
    temp_root = Path(temp_dir)

    # Copy docs directory
    temp_docs_dir = temp_root / "docs"
    shutil.copytree(docs_dir, temp_docs_dir)

    # Copy tests directory (needed for doctest setup)
    tests_dir = project_root / "tests"
    temp_tests_dir = temp_root / "tests"
    if tests_dir.exists():
        shutil.copytree(tests_dir, temp_tests_dir)

    # Copy project-root config files so Sphinx doctests find the same
    # configuration as the live environment.  The .env file is only copied
    # when DATAVIA_DOCTEST_COPY_ENV=1 is set explicitly, to avoid propagating
    # secrets (passwords, API keys) into doctest runs and CI artefacts.
    # Sphinx changes cwd to temp_docs_dir before running tests, and
    # config.py resolves both datavia.conf and .env relative to cwd.
    copy_env = os.getenv("DATAVIA_DOCTEST_COPY_ENV", "0") == "1"

    for config_file in ["datavia.conf"]:
        src = project_root / config_file
        if src.exists():
            shutil.copy2(src, temp_docs_dir / config_file)

    if copy_env:
        env_src = project_root / ".env"
        if env_src.exists():
            shutil.copy2(env_src, temp_docs_dir / ".env")

    # Process all RST files
    for rst_file in temp_docs_dir.rglob("*.rst"):
        content = rst_file.read_text()
        processed = remove_skip_directives(content)
        rst_file.write_text(processed)

    return temp_docs_dir


def run_sphinx_doctest(docs_dir, verbose=False):
    """Run Sphinx doctest on documentation.

    Parameters
    ----------
    docs_dir : Path
        Documentation directory to test
    verbose : bool, default=False
        Enable verbose output

    Returns
    -------
    bool
        True if tests passed, False otherwise
    """
    os.chdir(docs_dir)

    cmd = ["python", "-m", "sphinx", "-b", "doctest", ".", "_build/doctest"]
    if verbose:
        cmd.append("-v")

    result = subprocess.run(cmd, capture_output=not verbose, text=True, check=False)

    return result.returncode == 0


def start_datavia():
    """Start datavia environment.

    Returns
    -------
    bool
        True if successfully started, False otherwise
    """
    print("[INFO] Starting datavia...")
    return start_datavia_environment()


def stop_datavia():
    """Stop datavia environment."""
    print("[INFO] Stopping datavia...")
    stop_datavia_environment()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test documentation with I/O support (removes +SKIP directives)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    # Get paths
    project_root = Path(__file__).parent.parent
    docs_dir = project_root / "docs"

    print("[INFO] Processing documentation files...")
    print("[INFO] Creating temporary copies with +SKIP directives removed...")

    # Create temporary copies with +SKIP removed (also copies tests dir)
    temp_docs_dir = process_rst_files(docs_dir, project_root)
    original_cwd = os.getcwd()

    # Start datavia before testing
    if not start_datavia():
        print("[ERROR] Failed to start datavia services")
        return 1

    try:
        # Set environment to enable I/O tests
        env = os.environ.copy()
        env["DATAVIA_DOCTEST_IO"] = "1"
        os.environ["DATAVIA_DOCTEST_IO"] = "1"

        print("[INFO] Running Sphinx doctest with DATAVIA_DOCTEST_IO=1...")
        print("[INFO] I/O-dependent doctests will now be executed")
        print()

        success = run_sphinx_doctest(temp_docs_dir, verbose=args.verbose)

        if success:
            print()
            print("[SUCCESS] ✓ All documentation tests with I/O passed!")
            return 0
        else:
            print()
            print("[FAIL] ✗ Some documentation tests with I/O failed!")
            return 1

    finally:
        os.chdir(original_cwd)
        # Stop datavia
        stop_datavia()
        # Clean up temporary directory

        shutil.rmtree(Path(temp_docs_dir).parent)


if __name__ == "__main__":
    sys.exit(main())
