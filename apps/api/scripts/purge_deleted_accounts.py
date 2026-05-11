"""CLI to hard-delete soft-deleted users past the KVKK / GDPR grace window.

Wraps :mod:`src.billing.account_purge`. Designed to run nightly from a
Celery beat or a Railway cron job — idempotent, bounded by ``--limit``,
and dry-runnable.

Usage:

    poetry run python scripts/purge_deleted_accounts.py \\
        --database-url "$DATABASE_URL" \\
        --grace-days 30

    # Inspect candidates without deleting:
    poetry run python scripts/purge_deleted_accounts.py \\
        --database-url ... --dry-run

    # Limit blast radius on first prod run:
    poetry run python scripts/purge_deleted_accounts.py \\
        --database-url ... --limit 50

Per docs/09 §3.2:

> Hesap silinince ``proposals`` tablosundaki kayıtlar
> ``created_by = NULL`` olur, içerik kalır (tenant'ın diğer kullanıcıları
> erişebilsin), ``audit_log`` değişmez (yasal saklama).

The library nulls FK references in ``proposals`` / ``audit_log`` /
``tenant_usage_log`` BEFORE deleting ``public.users``. Each user is
processed in its own transaction so a single FK constraint problem
never blocks the rest of the run.

Exit codes:
- 0: success (even with per-user errors — see report).
- 2: invalid args / fatal config (no DSN, bad grace).
- 3: every candidate errored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

import asyncpg
from src.billing.account_purge import purge_expired_accounts

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purge_deleted_accounts",
        description=(
            "Hard-delete users whose soft-delete grace window has expired. "
            "Run nightly; idempotent."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (env: DATABASE_URL).",
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=int(os.environ.get("ACCOUNT_PURGE_GRACE_DAYS", "30")),
        help="Days a soft-deleted user must wait before purge (default: 30).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Hard cap on users purged per run (default: 1000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates without deleting anything.",
    )
    return parser


async def _amain(args: argparse.Namespace) -> int:
    if not args.database_url:
        print(
            "error: --database-url is required (or set DATABASE_URL).",
            file=sys.stderr,
        )
        return 2
    if args.grace_days < 0:
        print("error: --grace-days must be non-negative.", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=4)
    if pool is None:
        print("error: failed to open Postgres pool", file=sys.stderr)
        return 2

    try:
        try:
            report = await purge_expired_accounts(
                pool,
                grace_days=args.grace_days,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        await pool.close()

    print(json.dumps(report.as_dict(), indent=2))

    if (
        report.candidates > 0
        and not report.dry_run
        and len(report.errors) == report.candidates
    ):
        return 3
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
