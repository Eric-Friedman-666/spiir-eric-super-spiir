#!/usr/bin/env python3
"""Archived robust tail-clipping helper.

This file preserves the former high-LLR tail outlier clipping implementation
for auditability only. It is intentionally not imported by the production
single-detector FAR assignment path. As of 2026-06-09, production tail fitting
uses all available tail points in the constrained FAR-LLR line fit.
"""

from __future__ import annotations

import math


def _median(values):
    values = sorted(values)
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return 0.5 * (values[midpoint - 1] + values[midpoint])


def _fit_line_through_fixed_point(points, x0, y0):
    if len(points) < 2:
        return None, None
    denom = sum((x - x0) * (x - x0) for x, _y in points)
    if denom <= 0.0:
        return None, None
    slope = sum((x - x0) * (y - y0) for x, y in points) / denom
    if not math.isfinite(slope):
        return None, None
    intercept = y0 - slope * x0
    return slope, intercept


def archived_clip_tail_fit_outliers(
        points,
        x0,
        y0,
        min_points,
        sigma=2.6,
        min_log10_residual=0.08,
        iterations=8):
    """Former robust residual clipping for high-LLR FAR tail fits."""

    clean = list(points)
    min_points = max(2, int(min_points))
    if len(clean) < min_points:
        return clean
    ratios = [
        (y - y0) / (x - x0)
        for x, y in clean
        if x > x0
        and math.isfinite((y - y0) / (x - x0))
        and (y - y0) / (x - x0) < 0.0
    ]
    if ratios:
        slope = _median(ratios)
    else:
        slope, _intercept = _fit_line_through_fixed_point(clean, x0, y0)
    if slope is None:
        return clean

    for _iteration in range(max(1, int(iterations))):
        residuals = [y - (y0 + slope * (x - x0)) for x, y in clean]
        center = _median(residuals)
        mad = _median(abs(value - center) for value in residuals)
        if mad is None:
            break
        cutoff = max(float(min_log10_residual),
                     float(sigma) * 1.4826 * float(mad))
        filtered = [
            point for point, residual in zip(clean, residuals)
            if abs(residual - center) <= cutoff
        ]
        if len(filtered) < min_points:
            break
        new_slope, _intercept = _fit_line_through_fixed_point(
            filtered, x0, y0)
        if new_slope is None:
            break
        if len(filtered) == len(clean) and abs(new_slope - slope) < 1.0e-8:
            clean = filtered
            break
        clean = filtered
        slope = new_slope
    return clean
