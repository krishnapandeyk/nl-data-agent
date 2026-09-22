"""The analysis plan: the only structure the LLM is allowed to produce.

The model never sees the data rows, never does arithmetic, and never emits
code. It fills in this plan, and `guards.py` checks the plan before
`executor.py` runs it with pandas.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

FilterOp = Literal["eq", "neq", "in", "gt", "gte", "lt", "lte", "between"]
Aggregation = Literal["sum", "mean", "median", "min", "max", "count"]
Action = Literal["compute", "clarify", "refuse", "unsupported"]

FilterValue = Union[str, float, int, bool, List[Union[str, float, int]]]


class Filter(BaseModel):
    """One row-selection condition, e.g. region == "UK"."""

    # extra="forbid" makes a hallucinated key a hard parse error rather than
    # something we silently ignore.
    model_config = ConfigDict(extra="forbid")

    column: str
    op: FilterOp
    value: FilterValue


class AnalysisPlan(BaseModel):
    """A complete, machine-checkable description of one analysis.

    action:
        compute      -- run the aggregation described by the other fields
        clarify      -- the question is ambiguous; ask the user instead
        refuse       -- the question asks for something out of scope
                        (code execution, file access, credentials)
        unsupported  -- the question is legitimate but cannot be answered
                        from these columns
    """

    model_config = ConfigDict(extra="forbid")

    action: Action
    aggregation: Optional[Aggregation] = None
    metric: Optional[str] = None
    filters: List[Filter] = Field(default_factory=list)
    group_by: Optional[str] = None
    sort: Optional[Literal["asc", "desc"]] = None
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    message: Optional[str] = None
    assumptions: List[str] = Field(default_factory=list)
