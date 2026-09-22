"""Executing a validated plan.

The explanation is assembled from the plan itself, not written by the LLM,
so it always describes what actually ran.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .dataset import Dataset
from .guards import ValidatedPlan
from .schema import Filter
from .tools import aggregate, apply_filters, group_aggregate, missing_count

OP_WORDS = {
    "eq": "=",
    "neq": "!=",
    "in": "in",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "between": "between",
}


@dataclass
class Answer:
    """The full result of one question, including how it was reached."""

    question: str
    action: str
    value: Any = None
    explanation: str = ""
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rows_used: Optional[int] = None
    plan: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if hasattr(value, "item"):
        try:
            return _round(value.item())
        except (ValueError, AttributeError):
            return value
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value


def _sentence(text: str) -> str:
    """Capitalise the first letter only, preserving values like 'UK'."""
    return (text[:1].upper() + text[1:] + ".") if text else ""


def _describe_filter(f: Filter) -> str:
    if f.op == "between":
        low, high = f.value
        return f"{f.column} between {low} and {high}"
    if f.op == "in":
        return f"{f.column} in [{', '.join(map(str, f.value))}]"
    return f"{f.column} {OP_WORDS[f.op]} {f.value}"


def execute(validated: ValidatedPlan, data: Dataset, question: str) -> Answer:
    """Run a validated plan against the dataset."""
    plan = validated.plan
    warnings = list(validated.warnings)

    frame = data.transactions
    total_rows = len(frame)
    steps: List[str] = []

    # 1. Filter.
    if validated.filters:
        frame = apply_filters(frame, validated.filters)
        steps.append(
            "filtered "
            + " and ".join(_describe_filter(f) for f in validated.filters)
            + f" ({len(frame)} of {total_rows} rows)"
        )
    else:
        steps.append(f"used all {total_rows} rows")

    # An empty result is reported as such, never as zero.
    if frame.empty:
        return Answer(
            question=question,
            action="empty",
            value=None,
            explanation="No rows match those conditions, so there is nothing to "
            f"calculate. Steps: {'; '.join(steps)}.",
            assumptions=validated.assumptions,
            warnings=warnings,
            rows_used=0,
            plan=plan.model_dump(exclude_none=True),
        )

    # 2. Note rows the aggregation will skip for missing values.
    skipped = missing_count(frame, validated.metric)
    if skipped:
        warnings.append(
            f"{skipped} row(s) have no '{validated.metric}' value and were "
            "skipped by the calculation."
        )

    # 3. Aggregate.
    if plan.group_by:
        series = group_aggregate(
            frame, plan.group_by, plan.aggregation, validated.metric
        )
        if plan.sort:
            series = series.sort_values(ascending=(plan.sort == "asc"))
        if plan.limit:
            series = series.head(plan.limit)
        value: Any = {str(k): _round(v) for k, v in series.items()}

        target = validated.metric or "rows"
        steps.append(f"grouped by {plan.group_by}")
        steps.append(f"took the {plan.aggregation} of {target} per group")
        if plan.sort:
            steps.append(
                f"sorted {'ascending' if plan.sort == 'asc' else 'descending'}"
            )
        if plan.limit:
            steps.append(f"kept the top {plan.limit}")
    else:
        value = _round(aggregate(frame, plan.aggregation, validated.metric))
        target = validated.metric or "rows"
        steps.append(f"took the {plan.aggregation} of {target}")

    return Answer(
        question=question,
        action="compute",
        value=value,
        # Not str.capitalize(): it would lowercase "UK" and "Beta".
        explanation=_sentence("; ".join(steps)),
        assumptions=validated.assumptions,
        warnings=warnings,
        rows_used=int(len(frame)),
        plan=plan.model_dump(exclude_none=True),
    )
