"""The restricted tool interface.

Every operation the agent can perform on the data lives in this module and is
registered in TOOLS. There is no `eval`, no `exec`, no query string handed to
pandas, and no way for the model to reach anything that is not listed here.
Adding a capability means adding a function here on purpose.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

import pandas as pd

from .schema import Filter

AGGREGATIONS: Dict[str, Callable[[pd.Series], Any]] = {
    "sum": lambda s: s.sum(),
    "mean": lambda s: s.mean(),
    "median": lambda s: s.median(),
    "min": lambda s: s.min(),
    "max": lambda s: s.max(),
}


def apply_filters(frame: pd.DataFrame, filters: List[Filter]) -> pd.DataFrame:
    """Select rows. Filters are already validated against the real schema."""
    result = frame
    for f in filters:
        column = result[f.column]
        if f.column == "date":
            column = pd.to_datetime(column, errors="coerce")
            value = (
                [pd.to_datetime(v) for v in f.value]
                if isinstance(f.value, list)
                else pd.to_datetime(f.value)
            )
        else:
            value = f.value

        if f.op == "eq":
            mask = column == value
        elif f.op == "neq":
            mask = column != value
        elif f.op == "in":
            mask = column.isin(value if isinstance(value, list) else [value])
        elif f.op == "gt":
            mask = column > value
        elif f.op == "gte":
            mask = column >= value
        elif f.op == "lt":
            mask = column < value
        elif f.op == "lte":
            mask = column <= value
        elif f.op == "between":
            low, high = value
            mask = (column >= low) & (column <= high)
        else:  # unreachable: schema.FilterOp is a closed set
            raise ValueError(f"Unsupported operator: {f.op}")

        result = result[mask.fillna(False)]
    return result


def count_rows(frame: pd.DataFrame) -> int:
    """How many rows survived the filters."""
    return int(len(frame))


def aggregate(frame: pd.DataFrame, aggregation: str, metric: str) -> Any:
    """One number from one column. NaN values are skipped by pandas."""
    if aggregation == "count":
        return count_rows(frame)
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation}")
    if frame.empty:
        return None
    return AGGREGATIONS[aggregation](frame[metric])


def group_aggregate(
    frame: pd.DataFrame, group_by: str, aggregation: str, metric: str | None
) -> pd.Series:
    """One number per group."""
    if frame.empty:
        return pd.Series(dtype="float64")
    grouped = frame.groupby(group_by, dropna=False)
    if aggregation == "count":
        return grouped.size()
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation}")
    return grouped[metric].agg(aggregation)


def missing_count(frame: pd.DataFrame, metric: str | None) -> int:
    """Rows that will be skipped because the metric is missing."""
    if not metric or metric not in frame.columns:
        return 0
    return int(frame[metric].isna().sum())


TOOLS: Dict[str, Callable[..., Any]] = {
    "apply_filters": apply_filters,
    "count_rows": count_rows,
    "aggregate": aggregate,
    "group_aggregate": group_aggregate,
}
