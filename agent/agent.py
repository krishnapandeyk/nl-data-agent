"""The agent: the one place the pipeline is assembled.

    question
       -> prescreen        (deterministic: refuse out-of-scope requests)
       -> planner          (probabilistic: LLM writes a plan)
       -> validate_plan    (deterministic: check the plan against the data)
       -> execute          (deterministic: pandas computes the answer)
       -> Answer

Everything after the planner is deterministic, which is what makes the
numbers trustworthy: the model chooses *what* to compute, never *what the
answer is*.
"""

from __future__ import annotations

from typing import List

from .dataset import Dataset
from .executor import Answer, execute
from .guards import PlanRejected, prescreen, validate_plan
from .planner import Planner, PlannerError


class DataAnalysisAgent:
    def __init__(self, data: Dataset, planner: Planner) -> None:
        self.data = data
        self.planner = planner

    def ask(self, question: str) -> Answer:
        question = (question or "").strip()
        if not question:
            return Answer(
                question=question,
                action="clarify",
                explanation="No question was given.",
            )

        # 1. Refuse out-of-scope requests before the model is ever called.
        refusal = prescreen(question)
        if refusal:
            return Answer(question=question, action="refuse", explanation=refusal)

        # 2. Ask the model for a plan.
        try:
            plan = self.planner.plan(question, self.data)
        except PlannerError as exc:
            return Answer(
                question=question,
                action="error",
                explanation=f"No plan could be produced: {exc}",
            )

        # 3. The model may decline for its own reasons.
        if plan.action in {"refuse", "clarify", "unsupported"}:
            return Answer(
                question=question,
                action=plan.action,
                explanation=plan.message or f"The question was marked {plan.action}.",
                assumptions=list(plan.assumptions),
                plan=plan.model_dump(exclude_none=True),
            )

        # 4. Check the plan against the real dataset.
        try:
            validated = validate_plan(plan, self.data)
        except PlanRejected as exc:
            return Answer(
                question=question,
                action=exc.kind,
                explanation=exc.message,
                plan=plan.model_dump(exclude_none=True),
            )

        # 5. Compute.
        return execute(validated, self.data, question)

    def ask_all(self, questions: List[str]) -> List[Answer]:
        return [self.ask(q) for q in questions]
