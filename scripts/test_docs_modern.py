#!/usr/bin/env python3
"""
Modern Documentation Testing Script

This script uses proper documentation testing tools:
- sphinx.ext.doctest for RST files (built into Sphinx)
- xdoctest for comprehensive testing of all documentation formats

Usage:
    python scripts/test_docs_modern.py [--verbose] [--format sphinx|xdoctest|all]

    # Test using Sphinx doctest (RST files)
    python scripts/test_docs_modern.py --format sphinx

    # Test using xdoctest (all formats)
    python scripts/test_docs_modern.py --format xdoctest

    # Test using both (default)
    python scripts/test_docs_modern.py --format all
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


class ModernDocTester:
    """Modern documentation testing using Sphinx doctest and xdoctest.

    This class provides methods to test documentation across multiple
    formats (RST, Python docstrings) using industry-standard tools.

    Parameters
    ----------
    verbose : bool, default=False
        Enable verbose output during testing

    Attributes
    ----------
    project_root : Path
        Root directory of the project
    docs_dir : Path
        Documentation directory path
    """

    def __init__(self, verbose: bool = False):
        """Initialize the documentation tester.

        Parameters
        ----------
        verbose : bool, default=False
            If True, print detailed output during testing
        """
        self.verbose = verbose
        self.project_root = Path(__file__).parent.parent
        self.docs_dir = self.project_root / "docs"

    def log(self, message: str, level: str = "INFO"):
        """Log messages with severity level.

        Parameters
        ----------
        message : str
            Message to log
        level : str, default="INFO"
            Log level: INFO, ERROR, FAIL, SUCCESS
        """
        if self.verbose or level in ["ERROR", "FAIL"]:
            print(f"[{level}] {message}")

    def test_sphinx_doctest(self) -> bool:
        """Test documentation using Sphinx's built-in doctest"""
        self.log("Running Sphinx doctest...")

        os.chdir(self.docs_dir)

        try:
            # Run sphinx doctest
            cmd = ["python", "-m", "sphinx", "-b", "doctest", ".", "_build/doctest"]
            if self.verbose:
                cmd.append("-v")

            result = subprocess.run(
                cmd, capture_output=not self.verbose, text=True, check=False
            )

            if result.returncode == 0:
                self.log("✓ Sphinx doctest passed", "SUCCESS")
                return True
            else:
                self.log(
                    f"✗ Sphinx doctest failed with return code {result.returncode}",
                    "FAIL",
                )
                if not self.verbose and result.stdout:
                    print("STDOUT:")
                    print(result.stdout)
                if not self.verbose and result.stderr:
                    print("STDERR:")
                    print(result.stderr)
                return False

        except Exception as e:
            self.log(f"Error running Sphinx doctest: {e}", "ERROR")
            return False
        finally:
            os.chdir(self.project_root)

    def test_xdoctest(self) -> bool:
        """Test documentation using xdoctest"""
        self.log("Running xdoctest...")

        os.chdir(self.project_root)

        try:
            # Test RST files in docs
            cmd = [
                "python",
                "-m",
                "xdoctest",
                str(self.docs_dir),
                "--style=google",
                "--options=+NORMALIZE_WHITESPACE",
            ]

            if self.verbose:
                cmd.append("--verbose=3")
            else:
                cmd.append("--quiet")

            result = subprocess.run(
                cmd, capture_output=not self.verbose, text=True, check=False
            )

            if result.returncode == 0:
                self.log("✓ xdoctest passed", "SUCCESS")
                return True
            else:
                self.log(
                    f"✗ xdoctest failed with return code {result.returncode}", "FAIL"
                )
                if not self.verbose and result.stdout:
                    print("STDOUT:")
                    print(result.stdout)
                if not self.verbose and result.stderr:
                    print("STDERR:")
                    print(result.stderr)
                return False

        except Exception as e:
            self.log(f"Error running xdoctest: {e}", "ERROR")
            return False

    def test_package_doctests(self) -> bool:
        """Test doctests in the main package"""
        self.log("Running package doctests...")

        os.chdir(self.project_root)

        try:
            # Test main package with xdoctest
            cmd = [
                "python",
                "-m",
                "xdoctest",
                "datavia",
                "--style=google",
                "--options=+NORMALIZE_WHITESPACE",
            ]

            if self.verbose:
                cmd.append("--verbose=3")
            else:
                cmd.append("--quiet")

            result = subprocess.run(
                cmd, capture_output=not self.verbose, text=True, check=False
            )

            if result.returncode == 0:
                self.log("✓ Package doctests passed", "SUCCESS")
                return True
            else:
                self.log(
                    f"✗ Package doctests failed with return code {result.returncode}",
                    "FAIL",
                )
                if not self.verbose and result.stdout:
                    print("STDOUT:")
                    print(result.stdout)
                if not self.verbose and result.stderr:
                    print("STDERR:")
                    print(result.stderr)
                return False

        except Exception as e:
            self.log(f"Error running package doctests: {e}", "ERROR")
            return False

    def test_all(self, test_format: str = "all") -> bool:
        """Run all documentation tests"""
        self.log("Starting documentation tests...")

        results = []

        if test_format in ["sphinx", "all"]:
            results.append(self.test_sphinx_doctest())

        if test_format in ["xdoctest", "all"]:
            results.append(self.test_xdoctest())
            results.append(self.test_package_doctests())

        all_passed = all(results)

        if all_passed:
            self.log("✓ All documentation tests passed!", "SUCCESS")
        else:
            self.log("✗ Some documentation tests failed!", "FAIL")

        return all_passed


def main():
    parser = argparse.ArgumentParser(description="Test documentation code examples")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--format",
        choices=["sphinx", "xdoctest", "all"],
        default="all",
        help="Which testing format to use (default: all)",
    )

    args = parser.parse_args()

    tester = ModernDocTester(verbose=args.verbose)
    success = tester.test_all(test_format=args.format)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
