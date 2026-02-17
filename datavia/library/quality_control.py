"""
Quality control and validation functions migrated from processor.

Data quality functions for all pipelines:
- Coordinate validation and bounds checking
- Outlier detection and filtering
- Data quality assessment
- Quality flags and metadata
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def validate_coordinate_bounds(
    coords: np.ndarray,
    bounds: tuple[float, float, float, float],
    crs: str = "EPSG:4326",
) -> dict[str, Any]:
    """Comprehensive coordinate validation with bounds checking."""
    try:
        left, bottom, right, top = bounds

        # Check coordinate format
        if coords.ndim != 2 or coords.shape[1] != 2:
            return {
                "valid": False,
                "error": "Coordinates must be 2D array with shape (N, 2)",
                "valid_indices": [],
            }

        # Check for NaN/inf values
        finite_mask = np.isfinite(coords).all(axis=1)

        # Check bounds
        if crs == "EPSG:4326":
            # Special validation for WGS84
            lon_valid = (coords[:, 0] >= -180) & (coords[:, 0] <= 180)
            lat_valid = (coords[:, 1] >= -90) & (coords[:, 1] <= 90)
            geographic_valid = lon_valid & lat_valid
        else:
            geographic_valid = np.ones(len(coords), dtype=bool)

        # Check custom bounds
        bounds_valid = (
            (coords[:, 0] >= left)
            & (coords[:, 0] <= right)
            & (coords[:, 1] >= bottom)
            & (coords[:, 1] <= top)
        )

        # Combined validation
        valid_mask = finite_mask & geographic_valid & bounds_valid
        valid_indices = np.where(valid_mask)[0].tolist()

        # Check if all coordinates are valid
        all_valid = np.all(valid_mask)

        if all_valid:
            return {
                "valid": True,
                "error": None,
                "total_points": len(coords),
                "valid_points": np.sum(valid_mask),
                "valid_indices": valid_indices,
                "validation_details": {
                    "finite_check": np.sum(finite_mask),
                    "geographic_check": np.sum(geographic_valid),
                    "bounds_check": np.sum(bounds_valid),
                },
            }
        else:
            # Some coordinates are invalid
            error_parts = []
            if not np.all(finite_mask):
                error_parts.append(
                    f"NaN/inf values at indices {np.where(~finite_mask)[0].tolist()}"
                )
            if not np.all(geographic_valid):
                error_parts.append(
                    f"geographic bounds violations at indices {np.where(~geographic_valid)[0].tolist()}"
                )
            if not np.all(bounds_valid):
                error_parts.append(
                    f"custom bounds violations at indices {np.where(~bounds_valid)[0].tolist()}"
                )

            return {
                "valid": False,
                "error": "; ".join(error_parts),
                "total_points": len(coords),
                "valid_points": np.sum(valid_mask),
                "valid_indices": valid_indices,
                "validation_details": {
                    "finite_check": np.sum(finite_mask),
                    "geographic_check": np.sum(geographic_valid),
                    "bounds_check": np.sum(bounds_valid),
                },
            }

    except Exception as e:
        logger.error(f"Coordinate validation failed: {e}")
        return {"valid": False, "error": str(e), "valid_indices": []}


def detect_outliers(
    data: np.ndarray, method: str = "iqr", threshold: float = 1.5
) -> dict[str, Any]:
    """Detect outliers in data using various methods."""
    try:
        finite_mask = np.isfinite(data)
        finite_data = data[finite_mask]

        if len(finite_data) == 0:
            return {
                "outlier_indices": [],
                "outlier_values": [],
                "method": method,
                "threshold": threshold,
            }

        if method == "iqr":
            outliers = _detect_outliers_iqr(finite_data, threshold)
        elif method == "zscore":
            outliers = _detect_outliers_zscore(finite_data, threshold)
        elif method == "modified_zscore":
            outliers = _detect_outliers_modified_zscore(finite_data, threshold)
        else:
            raise ValueError(
                f"Unknown outlier detection method: {method}. Use 'iqr', 'zscore', or 'modified_zscore'."
            )

        # Map back to original indices
        finite_indices = np.where(finite_mask)[0]
        outlier_indices = finite_indices[outliers["indices"]].tolist()

        return {
            "outlier_indices": outlier_indices,
            "outlier_values": data[outlier_indices].tolist(),
            "method": method,
            "threshold": threshold,
            "statistics": outliers.get("statistics", {}),
        }

    except ValueError:
        # Re-raise ValueError for invalid method
        raise
    except Exception as e:
        logger.error(f"Outlier detection failed: {e}")
        return {"outlier_indices": [], "outlier_values": [], "error": str(e)}


def _detect_outliers_iqr(data: np.ndarray, threshold: float = 1.5) -> dict[str, Any]:
    """IQR-based outlier detection."""
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    iqr = q3 - q1

    lower_bound = q1 - threshold * iqr
    upper_bound = q3 + threshold * iqr

    outlier_mask = (data < lower_bound) | (data > upper_bound)
    outlier_indices = np.where(outlier_mask)[0]

    return {
        "indices": outlier_indices,
        "outlier_indices": outlier_indices.tolist(),
        "outlier_values": data[outlier_indices].tolist(),
        "statistics": {
            "Q1": q1,
            "Q3": q3,
            "IQR": iqr,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
        },
    }


def _detect_outliers_zscore(data: np.ndarray, threshold: float = 3.0) -> dict[str, Any]:
    """Z-score based outlier detection."""
    mean_val = np.mean(data)
    std_val = np.std(data)

    if std_val == 0:
        return {
            "indices": np.array([], dtype=int),
            "outlier_indices": [],
            "outlier_values": [],
            "statistics": {},
        }

    z_scores = np.abs((data - mean_val) / std_val)
    outlier_mask = z_scores > threshold
    outlier_indices = np.where(outlier_mask)[0]

    return {
        "indices": outlier_indices,
        "outlier_indices": outlier_indices.tolist(),
        "outlier_values": data[outlier_indices].tolist(),
        "statistics": {
            "mean": mean_val,
            "std": std_val,
            "max_zscore": np.max(z_scores),
        },
    }


def _detect_outliers_modified_zscore(
    data: np.ndarray, threshold: float = 3.5
) -> dict[str, Any]:
    """Modified Z-score using median absolute deviation."""
    median_val = np.median(data)
    mad = np.median(np.abs(data - median_val))

    if mad == 0:
        return {
            "indices": np.array([], dtype=int),
            "outlier_indices": [],
            "outlier_values": [],
            "statistics": {},
        }

    modified_z_scores = 0.6745 * (data - median_val) / mad
    outlier_mask = np.abs(modified_z_scores) > threshold
    outlier_indices = np.where(outlier_mask)[0]

    return {
        "indices": outlier_indices,
        "outlier_indices": outlier_indices.tolist(),
        "outlier_values": data[outlier_indices].tolist(),
        "statistics": {
            "median": median_val,
            "mad": mad,
            "max_modified_zscore": np.max(np.abs(modified_z_scores)),
        },
    }


def check_data_quality(
    data: np.ndarray,
    coords: np.ndarray | None = None,
    expected_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Comprehensive data quality assessment.

    Parameters
    ----------
    data : np.ndarray
        Array of data values to assess.
    coords : np.ndarray, optional
        Array of coordinates with shape (N, 2) for coordinate validation.
    expected_range : tuple, optional
        Expected (min, max) range for data values.

    Returns
    -------
    dict[str, Any]
        Quality assessment report with overall_quality, data_coverage, outlier_percentage,
        coordinate_validity, and other metrics.

    Raises
    ------
    ValueError
        If data and coordinates have mismatched lengths.
    """
    try:
        # Validate coordinate/data length matching
        if coords is not None and len(data) != len(coords):
            raise ValueError("Data and coordinates must have same length")

        # Check for missing data
        finite_mask = np.isfinite(data)
        valid_count = np.sum(finite_mask)
        missing_count = len(data) - valid_count
        data_coverage = valid_count / len(data) if len(data) > 0 else 0.0
        missing_data_percentage = (
            (missing_count / len(data) * 100) if len(data) > 0 else 0.0
        )

        # Detect outliers
        outlier_percentage = 0.0
        outliers_detected = False
        outlier_count = 0

        if valid_count > 0:
            try:
                outliers = detect_outliers(data[finite_mask])
                outlier_count = len(outliers["outlier_indices"])
                outlier_percentage = (
                    (outlier_count / len(data) * 100) if len(data) > 0 else 0.0
                )
                outliers_detected = outlier_count > 0
            except ValueError:
                # Use default IQR method if detection fails
                outliers_detected = False

        # Check coordinate validity if provided
        coordinate_validity = 1.0
        if coords is not None:
            coord_quality = validate_coordinate_bounds(coords, (-180, -90, 180, 90))
            valid_coords = len(coord_quality["valid_indices"])
            coordinate_validity = valid_coords / len(coords) if len(coords) > 0 else 0.0

        # Calculate overall quality score
        base_score = data_coverage  # Proportion of valid data
        outlier_penalty = (
            outlier_percentage / 100.0
        ) * 0.1  # Small penalty for outliers
        overall_quality = max(0.0, base_score - outlier_penalty)

        # Check data range if specified
        range_violations = 0
        if expected_range and valid_count > 0:
            finite_data = data[finite_mask]
            min_val, max_val = expected_range
            range_violations = np.sum((finite_data < min_val) | (finite_data > max_val))

        return {
            "total_points": len(data),
            "valid_points": int(valid_count),
            "missing_points": int(missing_count),
            "data_coverage": float(data_coverage),
            "missing_data_percentage": float(missing_data_percentage),
            "outlier_points": outlier_count,
            "outlier_percentage": float(outlier_percentage),
            "outliers_detected": bool(outliers_detected),
            "coordinate_validity": float(coordinate_validity),
            "overall_quality": float(overall_quality),
            "quality_score": float(overall_quality),
            "range_violations": int(range_violations),
        }

    except ValueError:
        # Re-raise ValueError for validation errors
        raise
    except Exception as e:
        logger.error(f"Data quality check failed: {e}")
        return {
            "total_points": len(data) if hasattr(data, "__len__") else 0,
            "overall_quality": 0.0,
            "quality_score": 0.0,
            "error": str(e),
        }


def apply_quality_filters(
    data: np.ndarray,
    coords: np.ndarray | None = None,
    remove_outliers: bool = True,
    outlier_method: str = "iqr",
    expected_range: tuple[float, float] | None = None,
    coordinate_bounds: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Apply quality filters and return cleaned data.

    Parameters
    ----------
    data : np.ndarray
        Array of data values to filter.
    coords : np.ndarray, optional
        Array of coordinates with shape (N, 2).
    remove_outliers : bool, default=True
        Whether to remove detected outliers.
    outlier_method : str, default="iqr"
        Method for outlier detection: "iqr", "zscore", or "modified_zscore".
    expected_range : tuple, optional
        Expected (min, max) range for data values.
    coordinate_bounds : tuple, optional
        Custom coordinate bounds (left, bottom, right, top).

    Returns
    -------
    dict[str, Any]
        Dictionary with filtered_data, coordinates, valid_mask, valid_indices,
        removed_count, quality_score, and filter_stats.
    """
    try:
        original_length = len(data)
        if original_length == 0:
            return {
                "filtered_data": np.array([]),
                "coordinates": coords if coords is not None else None,
                "filtered_coords": np.array([]).reshape(0, 2)
                if coords is not None
                else None,
                "valid_mask": np.array([], dtype=bool),
                "valid_indices": [],
                "removed_count": 0,
                "quality_score": 0.0,
                "filter_stats": {
                    "original_count": 0,
                    "finite_removed": 0,
                    "outliers_removed": 0,
                    "final_count": 0,
                },
                "filter_summary": "Empty input",
            }

        # Start with all data
        valid_mask = np.ones(original_length, dtype=bool)

        # Remove NaN/inf values
        finite_mask = np.isfinite(data)
        valid_mask &= finite_mask
        finite_removed = np.sum(~finite_mask)

        # Apply range filter if specified
        outliers_removed = 0
        if expected_range:
            min_val, max_val = expected_range
            range_mask = (data >= min_val) & (data <= max_val)
            valid_mask &= range_mask

        # Apply coordinate filter if bounds specified
        if coords is not None and coordinate_bounds is not None:
            try:
                coord_quality = validate_coordinate_bounds(coords, coordinate_bounds)
                valid_coord_indices = set(coord_quality["valid_indices"])
                for i in range(len(coords)):
                    if i not in valid_coord_indices:
                        valid_mask[i] = False
            except Exception as e:
                logger.warning(f"Coordinate filtering failed: {e}")

        # Remove outliers if requested
        if remove_outliers and np.sum(valid_mask) > 0:
            valid_data = data[valid_mask]
            try:
                outliers = detect_outliers(valid_data, method=outlier_method)
                if outliers["outlier_indices"]:
                    # Map outlier indices back to original array
                    valid_indices = np.where(valid_mask)[0]
                    outlier_original_indices = valid_indices[
                        outliers["outlier_indices"]
                    ]
                    valid_mask[outlier_original_indices] = False
                    outliers_removed = len(outliers["outlier_indices"])
            except ValueError:
                # If invalid method, just skip outlier removal
                logger.warning(
                    f"Outlier detection with method '{outlier_method}' failed, skipping"
                )

        # Extract filtered data
        filtered_data = data[valid_mask]
        filtered_coords = coords[valid_mask] if coords is not None else None

        return {
            "filtered_data": filtered_data,
            "data": filtered_data,  # Alias for compatibility
            "coordinates": filtered_coords,
            "filtered_coords": filtered_coords,
            "valid_mask": valid_mask,
            "valid_indices": np.where(valid_mask)[0].tolist(),
            "removed_count": original_length - len(filtered_data),
            "quality_score": (
                len(filtered_data) / original_length if original_length > 0 else 0.0
            ),
            "filter_stats": {
                "original_count": original_length,
                "finite_removed": int(finite_removed),
                "outliers_removed": int(outliers_removed),
                "final_count": len(filtered_data),
            },
            "filter_summary": {
                "total_removed": original_length - len(filtered_data),
                "removed_all_data": len(filtered_data) == 0,
                "final_count": len(filtered_data),
            },
        }

    except Exception as e:
        logger.error(f"Quality filtering failed: {e}")
        return {
            "filtered_data": data,
            "data": data,
            "coordinates": coords,
            "valid_mask": np.ones(len(data), dtype=bool),
            "error": str(e),
        }
