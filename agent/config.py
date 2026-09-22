"""Settings.

Credentials come from the environment or from an untracked .env file in the
project root, never from a tracked file. Real environment variables take
precedence over .env values.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Loaded here, before anything below reads os.environ, so .env values apply to
# these settings as well as to ANTHROPIC_API_KEY read later by the planner.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    pass
else:
    load_dotenv(PROJECT_ROOT / ".env")

DATA_PATH = Path(os.environ.get("DATA_PATH", PROJECT_ROOT / "data" / "transactions.csv"))

# Override with e.g. MODEL=claude-haiku-4-5-20251001 for cheaper runs.
MODEL = os.environ.get("MODEL", "claude-sonnet-5")
API_KEY_ENV = "ANTHROPIC_API_KEY"

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("RETRY_BACKOFF_SECONDS", "1.0"))
