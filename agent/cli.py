"""Command line.

    python -m agent.cli "What is the total revenue for UK transactions?"
    python -m agent.cli --all              # the sample questions in the data file
    python -m agent.cli --all --json results.json
    python -m agent.cli --interactive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from .agent import DataAnalysisAgent
from .config import DATA_PATH
from .dataset import load_dataset
from .executor import Answer
from .planner import ClaudePlanner, PlannerError

SYMBOLS = {
    "compute": "=",
    "refuse": "refused",
    "clarify": "needs input",
    "unsupported": "unsupported",
    "unknown_value": "not in data",
    "invalid": "invalid plan",
    "empty": "no matching rows",
    "error": "error",
}


def render(answer: Answer) -> str:
    lines = [f"Q: {answer.question}"]
    if answer.action == "compute":
        value = answer.value
        if isinstance(value, dict):
            lines.append("A:")
            for key, item in value.items():
                lines.append(f"     {key}: {item}")
        else:
            lines.append(f"A: {value}")
    else:
        lines.append(f"A: [{SYMBOLS.get(answer.action, answer.action)}] "
                     f"{answer.explanation}")
    if answer.action == "compute" and answer.explanation:
        lines.append(f"   How: {answer.explanation}")
    for note in answer.assumptions:
        lines.append(f"   Assumption: {note}")
    for note in answer.warnings:
        lines.append(f"   Note: {note}")
    return "\n".join(lines)


def build_agent(data_path: Path) -> DataAnalysisAgent:
    data = load_dataset(data_path)
    for issue in data.issues:
        print(f"[data] {issue}", file=sys.stderr)
    return DataAnalysisAgent(data=data, planner=ClaudePlanner())


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask questions about the data.")
    parser.add_argument("question", nargs="?", help="a question in plain English")
    parser.add_argument("--all", action="store_true",
                        help="answer the sample questions in the data file")
    parser.add_argument("--interactive", action="store_true", help="ask repeatedly")
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--json", type=Path, help="also write results to this file")
    args = parser.parse_args(argv)

    try:
        agent = build_agent(args.data)
    except PlannerError as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 2

    answers: List[Answer] = []

    if args.all:
        questions = [q for _, q in agent.data.questions]
        if not questions:
            print("No questions found in the data file.", file=sys.stderr)
            return 1
        for question in questions:
            answer = agent.ask(question)
            answers.append(answer)
            print(render(answer))
            print()
    elif args.interactive:
        print("Ask a question, or press Enter to quit.")
        while True:
            try:
                question = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not question:
                break
            print(render(agent.ask(question)))
            print()
    elif args.question:
        answer = agent.ask(args.question)
        answers.append(answer)
        print(render(answer))
    else:
        parser.print_help()
        return 1

    if args.json and answers:
        args.json.write_text(
            json.dumps([a.to_dict() for a in answers], indent=2, default=str)
        )
        print(f"Wrote {len(answers)} result(s) to {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
