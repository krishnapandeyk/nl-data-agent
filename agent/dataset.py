"""Loading and cleaning the transaction CSV.

The source file mixes two kinds of row in one table: real transactions, and
saved example questions parked in a `question` column with every other field
blank. Treating those question rows as transactions would quietly corrupt
every count and average, so they are separated on load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

# The type of each column decides which operations are legal on it.
COLUMN_TYPES: Dict[str, str] = {
    "id": "identifier",
    "date": "date",
    "region": "categorical",
    "product": "categorical",
    "units": "numeric",
    "unit_price": "numeric",
    "discount": "numeric",
    "gross_revenue": "numeric",
    "net_revenue": "numeric",
}

NUMERIC_COLUMNS = {c for c, t in COLUMN_TYPES.items() if t == "numeric"}
CATEGORICAL_COLUMNS = {c for c, t in COLUMN_TYPES.items() if t == "categorical"}
DATE_COLUMNS = {c for c, t in COLUMN_TYPES.items() if t == "date"}

# "Revenue" is not a column in the source file, and the question set uses the
# word without defining it. Rather than guessing silently, the word resolves
# to a named column and the choice is reported in every answer that uses it.
METRIC_ALIASES: Dict[str, str] = {
    "revenue": "net_revenue",
    "sales": "net_revenue",
    "turnover": "net_revenue",
    "amount": "net_revenue",
    "value": "net_revenue",
}

REVENUE_ASSUMPTION = (
    "'revenue' is read as net_revenue = units * unit_price * (1 - discount). "
    "Ask for 'gross revenue' to exclude the discount."
)

REQUIRED_NUMERIC = ["units", "unit_price", "discount"]


@dataclass
class Dataset:
    """A cleaned dataset plus a record of what had to be cleaned."""

    transactions: pd.DataFrame
    questions: List[Tuple[str, str]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    def domain(self, column: str) -> List[str]:
        """The distinct values of a categorical column, for validation."""
        if column not in self.transactions.columns:
            return []
        return sorted(self.transactions[column].dropna().unique().tolist())

    def schema_description(self) -> str:
        """A compact column listing to put in the planner prompt."""
        lines = []
        for column, kind in COLUMN_TYPES.items():
            if column not in self.transactions.columns:
                continue
            if kind == "categorical":
                values = ", ".join(str(v) for v in self.domain(column))
                lines.append(f"- {column} ({kind}): one of [{values}]")
            elif kind == "date":
                lines.append(f"- {column} (date): ISO format YYYY-MM-DD")
            else:
                lines.append(f"- {column} ({kind})")
        return "\n".join(lines)


def load_dataset(path: str | Path) -> Dataset:
    """Read the CSV, split out the question rows and add revenue columns."""
    raw = pd.read_csv(path, dtype=str)
    issues: List[str] = []

    # 1. Split question rows off from transaction rows.
    questions: List[Tuple[str, str]] = []
    if "question" in raw.columns:
        is_question = raw["question"].notna() & (raw["question"].str.strip() != "")
        for _, row in raw[is_question].iterrows():
            questions.append((str(row.get("id", "")), str(row["question"]).strip()))
        transactions = raw[~is_question].drop(columns=["question"]).copy()
        if len(questions):
            issues.append(
                f"{len(questions)} row(s) held a saved question rather than a "
                "transaction and were excluded from all calculations."
            )
    else:
        transactions = raw.copy()

    # 2. Tidy the categorical text so "uk", " UK " and "UK" are one value.
    for column in CATEGORICAL_COLUMNS & set(transactions.columns):
        transactions[column] = transactions[column].str.strip()

    # 3. Coerce types. Anything unparseable becomes NaN/NaT rather than
    #    crashing, and is reported instead of being hidden.
    for column in ["units", "unit_price", "discount"]:
        if column in transactions.columns:
            transactions[column] = pd.to_numeric(transactions[column], errors="coerce")
    if "date" in transactions.columns:
        transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")

    # 4. Drop rows that carry no usable data at all (e.g. trailing blank lines).
    before = len(transactions)
    transactions = transactions.dropna(how="all").reset_index(drop=True)
    if len(transactions) < before:
        issues.append(f"Dropped {before - len(transactions)} completely empty row(s).")

    # 5. Report missing required values. The rows are kept: pandas skips NaN
    #    in aggregations, and the executor reports how many it skipped.
    for column in REQUIRED_NUMERIC:
        if column in transactions.columns:
            missing = int(transactions[column].isna().sum())
            if missing:
                issues.append(f"{missing} row(s) have no value for '{column}'.")
    if "date" in transactions.columns:
        missing_dates = int(transactions["date"].isna().sum())
        if missing_dates:
            issues.append(f"{missing_dates} row(s) have an unreadable date.")

    # 6. Derive both revenue definitions so the ambiguity can be made explicit
    #    at answer time instead of being resolved silently here.
    if {"units", "unit_price"} <= set(transactions.columns):
        discount = transactions.get("discount")
        transactions["gross_revenue"] = transactions["units"] * transactions["unit_price"]
        if discount is not None:
            transactions["net_revenue"] = transactions["gross_revenue"] * (
                1 - discount.fillna(0)
            )
        else:
            transactions["net_revenue"] = transactions["gross_revenue"]

    return Dataset(transactions=transactions, questions=questions, issues=issues)


def resolve_metric(name: str) -> Tuple[str, List[str]]:
    """Map a loose metric name onto a real column, reporting any assumption."""
    key = (name or "").strip().lower().replace(" ", "_")
    if key in METRIC_ALIASES:
        return METRIC_ALIASES[key], [REVENUE_ASSUMPTION]
    if key in {"gross_revenue", "gross"}:
        return "gross_revenue", []
    return key, []
