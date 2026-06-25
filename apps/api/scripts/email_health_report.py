"""CLI: run the email-health analyzer + print a JSON report.

Designed for a Railway cron (e.g. hourly) so the ops Slack channel
gets a heartbeat. Exit codes are alert-aware so the cron host can
gate a notifier on a non-zero exit:

- 0: report produced, no thresholds tripped.
- 1: report produced, AT LEAST ONE threshold tripped — operator
     should investigate the sender domain in the Resend dashboard.
- 2: invalid args / fatal config (no DSN, bad threshold).
- 3: DB unreachable / unexpected error.

Usage::

    poetry run python scripts/email_health_report.py \\
        --database-url "$DATABASE_URL" \\
        --window-minutes 60 \\
        --hard-bounce-threshold 0.02 \\
        --complaint-threshold 0.001 \\
        --min-sample 50

Pipe the JSON into ``jq`` or a Slack webhook — the report always
includes the raw counts, not just the alert text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import timedelta

import asyncpg
from src.notifications.health import HealthThresholds, evaluate_health

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email_health_report",
        description=(
            "Compute hard-bounce / complaint rate over a rolling window "
            "from email_events and print the report as JSON."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (env: DATABASE_URL).",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=60,
        help="Window to aggregate over, in minutes (default: 60).",
    )
    parser.add_argument(
        "--hard-bounce-threshold",
        type=float,
        default=0.02,
        help="Alert threshold for the hard-bounce rate (default: 0.02 = 2%%).",
    )
    parser.add_argument(
        "--complaint-threshold",
        type=float,
        default=0.001,
        help="Alert threshold for the complaint rate (default: 0.001 = 0.1%%).",
    )
    parser.add_argument(
        "--min-sample",
        type=int,
        default=50,
        help="Minimum (delivered + bounces + complaints) to enable alerting (default: 50).",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> str | None:
    """Return the first arg error message, or None if everything's fine.

    Kept separate so :func:`_amain` doesn't fan out into 4 return
    statements before any real work.
    """

    if not args.database_url:
        return "error: --database-url is required (or set DATABASE_URL)."
    if args.window_minutes <= 0:
        return "error: --window-minutes must be > 0."
    if not (0 <= args.hard_bounce_threshold <= 1):
        return "error: --hard-bounce-threshold must be in [0, 1]."
    if not (0 <= args.complaint_threshold <= 1):
        return "error: --complaint-threshold must be in [0, 1]."
    return None


async def _amain(args: argparse.Namespace) -> int:
    validation_error = _validate_args(args)
    if validation_error is not None:
        print(validation_error, file=sys.stderr)
        return 2

    try:
        pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=2)
    except Exception as exc:
        print(f"error: failed to open Postgres pool: {exc}", file=sys.stderr)
        return 3
    if pool is None:
        print("error: failed to open Postgres pool", file=sys.stderr)
        return 3

    try:
        async with pool.acquire() as conn:
            report = await evaluate_health(
                conn,
                window=timedelta(minutes=args.window_minutes),
                thresholds=HealthThresholds(
                    hard_bounce=args.hard_bounce_threshold,
                    complaint=args.complaint_threshold,
                    min_sample=args.min_sample,
                ),
            )
    except Exception as exc:
        print(f"error: health evaluation failed: {exc}", file=sys.stderr)
        return 3
    finally:
        await pool.close()

    # Operator-facing JSON — always printed so a Slack webhook gets the
    # full picture, alert text or not.
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 1 if report.alerts else 0


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s %(message)s",
    )
    args = _build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
