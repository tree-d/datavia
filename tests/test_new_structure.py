#!/usr/bin/env python3
"""Test namespace package installation with new structure."""


def test_namespace_packages():
    print("🧪 Testing namespace package architecture...")

    try:
        import datavia  # noqa: PLC0415

        print(
            f"✅ datavia imported from: {datavia.__file__ if hasattr(datavia, '__file__') else 'namespace'}"
        )
        print(f"✅ datavia.__path__: {datavia.__path__}")

        # Test core modules
        try:
            import datavia.core  # noqa: PLC0415

            print("✅ datavia.core imported")
        except ImportError as e:
            print(f"❌ datavia.core failed: {e}")

        try:
            import datavia.library  # noqa: PLC0415

            print("✅ datavia.library imported")
        except ImportError as e:
            print(f"❌ datavia.library failed: {e}")

        # Test pipeline modules (should fail if not installed)
        try:
            import datavia.soil  # noqa: PLC0415

            print("✅ datavia.soil imported")
        except ImportError as e:
            print(f"❌ datavia.soil not available: {e}")

        try:
            import datavia.elevation  # noqa: PLC0415

            print("✅ datavia.elevation imported")
        except ImportError as e:
            print(f"❌ datavia.elevation not available: {e}")

    except ImportError as e:
        print(f"❌ Basic datavia import failed: {e}")


if __name__ == "__main__":
    test_namespace_packages()
