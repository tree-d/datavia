"""Unit tests for datavia.library.type_utils.

Functions under test:
- is_scalar_like(value)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from datavia.library.type_utils import is_scalar_like


@pytest.mark.parametrize(
    "value",
    [
        1,
        1.5,
        "2024-06-15T12:00:00",
        None,
        pd.Timestamp("2024-06-15"),
        np.float64(1.0),
        np.array(1.0),
    ],
)
def test_scalar_like_values_are_scalar(value):
    assert is_scalar_like(value) is True


@pytest.mark.parametrize(
    "value",
    [
        [1, 2, 3],
        (1, 2, 3),
        [1],
        (1,),
        np.array([1, 2, 3]),
        np.array([1]),
        pd.Series([1, 2, 3]),
    ],
)
def test_sequence_like_values_are_not_scalar(value):
    assert is_scalar_like(value) is False
