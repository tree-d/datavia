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

        return {
            "valid": True,
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
            logger.warning(f"Unknown outlier method: {method}, using IQR")
            outliers = _detect_outliers_iqr(finite_data, threshold)

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
        return {"indices": np.array([], dtype=int), "statistics": {}}

    z_scores = np.abs((data - mean_val) / std_val)
    outlier_mask = z_scores > threshold
    outlier_indices = np.where(outlier_mask)[0]

    return {
        "indices": outlier_indices,
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
        return {"indices": np.array([], dtype=int), "statistics": {}}

    modified_z_scores = 0.6745 * (data - median_val) / mad
    outlier_mask = np.abs(modified_z_scores) > threshold
    outlier_indices = np.where(outlier_mask)[0]

    return {
        "indices": outlier_indices,
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
    """Comprehensive data quality assessment."""
    try:
        quality_report: dict[str, Any] = {
            "total_points": len(data),
            "valid_points": 0,
            "missing_points": 0,
            "outlier_points": 0,
            "quality_score": 0.0,
            "issues": [],
        }

        # Check for missing data
        finite_mask = np.isfinite(data)
        valid_count = np.sum(finite_mask)
        missing_count = len(data) - valid_count

        quality_report["valid_points"] = int(valid_count)
        quality_report["missing_points"] = int(missing_count)

        if missing_count > 0:
            quality_report["issues"].append(f"{missing_count} missing/invalid values")

        # Check data range if specified
        if expected_range and valid_count > 0:
            finite_data = data[finite_mask]
            min_val, max_val = expected_range
            range_violations = np.sum((finite_data < min_val) | (finite_data > max_val))
            if range_violations > 0:
                quality_report["issues"].append(
                    f"{range_violations} values outside expected range [{min_val}, {max_val}]"
                )

        # Detect outliers
        if valid_count > 0:
            outliers = detect_outliers(data[finite_mask])
            outlier_count = len(outliers["outlier_indices"])
            quality_report["outlier_points"] = outlier_count

            if outlier_count > 0:
                quality_report["issues"].append(
                    f"{outlier_count} potential outliers detected"
                )

        # Calculate quality score
        if len(data) > 0:
            base_score = valid_count / len(data)  # Proportion of valid data
            outlier_penalty = (
                int(quality_report["outlier_points"]) / len(data)
            ) * 0.1  # Small penalty for outliers
            quality_report["quality_score"] = max(0.0, base_score - outlier_penalty)

        # Add coordinate quality if provided
        if coords is not None:
            coord_quality = validate_coordinate_bounds(coords, (-180, -90, 180, 90))
            if not coord_quality["valid"]:
                quality_report["issues"].append("Invalid coordinates detected")

        return quality_report

    except Exception as e:
        logger.error(f"Data quality check failed: {e}")
        return {
            "total_points": len(data) if hasattr(data, "__len__") else 0,
            "quality_score": 0.0,
            "error": str(e),
            "issues": ["Quality check failed"],
        }


def apply_quality_filters(
    data: np.ndarray,
    coords: np.ndarray | None = None,
    remove_outliers: bool = True,
    outlier_method: str = "iqr",
    expected_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Apply quality filters and return cleaned data."""
    try:
        original_length = len(data)

        # Start with all data
        valid_mask = np.ones(len(data), dtype=bool)

        # Remove NaN/inf values
        finite_mask = np.isfinite(data)
        valid_mask &= finite_mask

        # Apply range filter if specified
        if expected_range:
            min_val, max_val = expected_range
            range_mask = (data >= min_val) & (data <= max_val)
            valid_mask &= range_mask

        # Remove outliers if requested
        outlier_indices = []
        if remove_outliers and np.sum(valid_mask) > 0:
            valid_data = data[valid_mask]
            outliers = detect_outliers(valid_data, method=outlier_method)

            if outliers["outlier_indices"]:
                # Map outlier indices back to original array
                valid_indices = np.where(valid_mask)[0]
                outlier_original_indices = valid_indices[outliers["outlier_indices"]]
                valid_mask[outlier_original_indices] = False
                outlier_indices = outliers["outlier_indices"]

        # Extract filtered data
        filtered_data = data[valid_mask]
        filtered_coords = coords[valid_mask] if coords is not None else None

        return {
            "data": filtered_data,
            "coordinates": filtered_coords,
            "valid_mask": valid_mask,
            "valid_indices": np.where(valid_mask)[0].tolist(),
            "removed_count": original_length - len(filtered_data),
            "quality_score": (
                len(filtered_data) / original_length if original_length > 0 else 0.0
            ),
            "filter_stats": {
                "original_count": original_length,
                "finite_removed": np.sum(~finite_mask),
                "outliers_removed": len(outlier_indices),
                "final_count": len(filtered_data),
            },
        }

    except Exception as e:
        logger.error(f"Quality filtering failed: {e}")
        return {
            "data": data,
            "coordinates": coords,
            "valid_mask": np.ones(len(data), dtype=bool),
            "error": str(e),
        }
