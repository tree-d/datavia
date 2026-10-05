"""Scalar vs. array-like input normalization utilities."""

from typing import Any

import numpy as np


def is_scalar_like(value: Any) -> bool:
    """Return True if `value` should be treated as a single item rather
    than a sequence of items.

    Uses `np.ndim(value) == 0`, which correctly classifies Python/NumPy
    scalars, 0-d arrays, and strings as scalar, while lists, tuples,
    NumPy arrays, and pandas Series (anything exposing ndim >= 1) are
    treated as sequences.
    """
    return np.ndim(value) == 0
