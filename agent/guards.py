"""Guardrails.

Two layers sit between the question and pandas:

1. `prescreen` runs before the LLM is called at all, so a request to execute
   code or read credentials never reaches the model. Safety does not depend
   on the model choosing to behave.
2. `validate_plan` runs after the LLM answers. Every column, operation and
   filter value in the plan is checked against the real dataset, so a
   fabricated column or an impossible aggregation is rejected rather than
   executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pandas as pd

from .dataset import (
    CATEGORICAL_COLUMNS,
    COLUMN_TYPES,
    DATE_COLUMNS,
    NUMERIC_COLUMNS,
    Dataset,
    resolve_metric,
)
from .schema import AnalysisPlan, Filter

# Requests that are out of scope for a data-analysis agent no matter who asks.
OUT_OF_SCOPE_PATTERNS = [
    (r"\b(run|execute|eval|exec|interpret)\b[^.?]{0,40}\b(code|python|script|shell|command)\b",
     "running arbitrary code"),
    (r"(os\.|subprocess|__import__|\beval\s*\(|\bexec\s*\()", "running arbitrary code"),
    # Not anchored with \b on every alternative: an underscore is a word
    # character, so "anthropic_api_key" has no boundary before "api".
    (r"(\bsecrets?\b|\bcredentials?\b|\bpasswords?\b|api[ _-]?keys?"
     r"|\btokens?\b|\.env\b|\benvironment variables?\b)",
     "access to credentials"),
    (r"\b(read|open|list|inspect|access|delete|overwrite)\b[^.?]{0,40}\b(file|files|directory|folder|filesystem|disk|path)\b",
     "access to the filesystem"),
    (r"\bignore\b[^.?]{0,30}\b(previous|prior|earlier|above)\b[^.?]{0,20}\binstructions?\b",
     "overriding its instructions"),
    (r"\b(system prompt|your instructions)\b", "disclosing its own configuration"),
]

REFUSAL_TEMPLATE = (
    "This agent only answers questions about the transaction dataset. "
    "It cannot help with {reason}."
)


class PlanRejected(Exception):
    """Raised when a plan cannot be safely or meaningfully executed."""

    def __init__(self, message: str, kind: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        # kind: "invalid" (malformed), "unsupported" (legal but impossible),
        # "unknown_value" (filter value absent from the data)
        self.kind = kind


@dataclass
class ValidatedPlan:
    """A plan that has been checked against the dataset and is safe to run."""

    plan: AnalysisPlan
    metric: Optional[str] = None
    filters: List[Filter] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def prescreen(question: str) -> Optional[str]:
    """Return a refusal message if the question is out of scope, else None."""
    lowered = (question or "").lower()
    for pattern, reason in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, lowered):
            return REFUSAL_TEMPLATE.format(reason=reason)
    return None


def _words(text: str) -> set:
    """The distinct words in a piece of text, ignoring single characters."""
    return {w for w in re.findall(r"[a-z_0-9]+", text.lower()) if len(w) > 1}


def merge_assumptions(canonical: List[str], model: List[str]) -> List[str]:
    """Our assumptions, then any of the model's that add something new.

    A model assumption whose words all appear in ours is a paraphrase of
    something we already say, so it is dropped rather than printed twice.
    """
    known = set().union(*(_words(a) for a in canonical))
    return list(canonical) + [a for a in model if not _words(a) <= known]


def _match_category(value: Any, domain: List[str]) -> Optional[str]:
    """Case-insensitive lookup of a categorical value in its real domain."""
    text = str(value).strip().lower()
    for known in domain:
        if str(known).strip().lower() == text:
            return known
    return None


def _validate_filter(f: Filter, data: Dataset) -> Filter:
    column = f.column.strip().lower()
    if column not in COLUMN_TYPES or column not in data.transactions.columns:
        raise PlanRejected(
            f"There is no column called '{f.column}'. Available columns: "
            + ", ".join(sorted(data.transactions.columns)),
            kind="invalid",
        )

    kind = COLUMN_TYPES[column]

    if kind == "categorical":
        if f.op not in {"eq", "neq", "in"}:
            raise PlanRejected(
                f"'{f.op}' cannot be applied to the text column '{column}'.",
                kind="unsupported",
            )
        domain = data.domain(column)
        raw_values = f.value if isinstance(f.value, list) else [f.value]
        matched = []
        for value in raw_values:
            hit = _match_category(value, domain)
            if hit is None:
                raise PlanRejected(
                    f"'{value}' is not a value of '{column}'. "
                    f"Known values: {', '.join(map(str, domain))}.",
                    kind="unknown_value",
                )
            matched.append(hit)
        value: Any = matched if f.op == "in" else matched[0]
        return Filter(column=column, op=f.op, value=value)

    if kind == "numeric":
        values = f.value if isinstance(f.value, list) else [f.value]
        try:
            numbers = [float(v) for v in values]
        except (TypeError, ValueError):
            raise PlanRejected(
                f"'{f.value}' is not a number, so it cannot filter '{column}'.",
                kind="invalid",
            )
        if f.op == "between" and len(numbers) != 2:
            raise PlanRejected(
                "A 'between' filter needs exactly two values.", kind="invalid"
            )
        value = numbers if f.op in {"in", "between"} else numbers[0]
        return Filter(column=column, op=f.op, value=value)

    if kind == "date":
        values = f.value if isinstance(f.value, list) else [f.value]
        parsed = []
        for value in values:
            stamp = pd.to_datetime(value, errors="coerce")
            if pd.isna(stamp):
                raise PlanRejected(
                    f"'{value}' is not a readable date.", kind="invalid"
                )
            parsed.append(stamp.isoformat())
        if f.op == "between" and len(parsed) != 2:
            raise PlanRejected(
                "A 'between' date filter needs a start and an end.", kind="invalid"
            )
        value = parsed if f.op in {"in", "between"} else parsed[0]
        return Filter(column=column, op=f.op, value=value)

    # identifier
    if f.op not in {"eq", "neq", "in"}:
        raise PlanRejected(
            f"'{f.op}' cannot be applied to '{column}'.", kind="unsupported"
        )
    return Filter(column=column, op=f.op, value=f.value)


def validate_plan(plan: AnalysisPlan, data: Dataset) -> ValidatedPlan:
    """Check a plan against the real dataset. Raises PlanRejected on failure."""
    if plan.action != "compute":
        return ValidatedPlan(plan=plan, assumptions=list(plan.assumptions))

    model_assumptions = list(plan.assumptions)
    canonical_assumptions: List[str] = []
    warnings: List[str] = []

    if plan.aggregation is None:
        raise PlanRejected(
            "The plan asks to compute something but names no aggregation.",
            kind="invalid",
        )

    # --- metric -----------------------------------------------------------
    metric: Optional[str] = None
    if plan.aggregation == "count":
        if plan.metric:
            warnings.append("A metric was given for a count and has been ignored.")
    else:
        if not plan.metric:
            raise PlanRejected(
                f"'{plan.aggregation}' needs a column to work on.", kind="invalid"
            )
        metric, metric_assumptions = resolve_metric(plan.metric)
        canonical_assumptions.extend(metric_assumptions)
        if metric not in COLUMN_TYPES or metric not in data.transactions.columns:
            raise PlanRejected(
                f"There is no column called '{plan.metric}'. Available numeric "
                "columns: " + ", ".join(sorted(NUMERIC_COLUMNS)),
                kind="invalid",
            )
        if metric not in NUMERIC_COLUMNS:
            raise PlanRejected(
                f"'{metric}' holds "
                f"{'dates' if metric in DATE_COLUMNS else 'text'}, so "
                f"'{plan.aggregation}' is not meaningful on it.",
                kind="unsupported",
            )

    # --- group_by ---------------------------------------------------------
    if plan.group_by:
        group_by = plan.group_by.strip().lower()
        if group_by not in COLUMN_TYPES or group_by not in data.transactions.columns:
            raise PlanRejected(
                f"There is no column called '{plan.group_by}'.", kind="invalid"
            )
        if group_by not in CATEGORICAL_COLUMNS:
            raise PlanRejected(
                f"Grouping by '{group_by}' is not supported; group by one of: "
                + ", ".join(sorted(CATEGORICAL_COLUMNS)),
                kind="unsupported",
            )
        plan = plan.model_copy(update={"group_by": group_by})
    else:
        if plan.sort or plan.limit:
            warnings.append(
                "Sorting and limiting only apply to grouped results; ignored."
            )

    # --- filters ----------------------------------------------------------
    validated_filters = [_validate_filter(f, data) for f in plan.filters]

    return ValidatedPlan(
        plan=plan,
        metric=metric,
        filters=validated_filters,
        assumptions=merge_assumptions(canonical_assumptions, model_assumptions),
        warnings=warnings,
    )
