"""Helpers shared by the Mojo tests of the kriging kernels."""


def close(a: Float64, b: Float64, rel: Float64 = 1e-12) -> Bool:
    """Tests whether two numbers agree to a relative tolerance.

    Args:
        a: The first number.
        b: The second number.
        rel: Relative tolerance, measured against the larger magnitude.

    Returns:
        True if `|a - b| <= rel * max(|a|, |b|)` (plus a tiny absolute term, so two zeros agree).
    """
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-300
