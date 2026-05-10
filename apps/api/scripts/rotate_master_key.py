"""CLI to rotate ``LLM_MASTER_ENCRYPTION_KEY`` over every BYOK column.

Wraps :mod:`src.billing.key_rotation`. The actual decrypt-then-encrypt
runs entirely inside Postgres — plaintext never crosses the Python
boundary.

Usage:

    poetry run python scripts/rotate_master_key.py \\
        --old-master "$OLD_MASTER" \\
        --new-master "$NEW_MASTER" \\
        --database-url "$DATABASE_URL"

    # Dry-run first (no writes, just count):
    poetry run python scripts/rotate_master_key.py \\
        --old-master ... --new-master ... --dry-run

    # Rotate only one tenant (recovery flow):
    poetry run python scripts/rotate_master_key.py \\
        --old-master ... --new-master ... \\
        --tenant-id 11111111-1111-1111-1111-111111111111

Each flag also reads from an env var:

    OLD_MASTER → LLM_MASTER_ENCRYPTION_KEY_OLD
    NEW_MASTER → LLM_MASTER_ENCRYPTION_KEY
    DATABASE_URL

Exit codes: 0 success (even with per-tenant errors — the report shows
them); 2 invalid args / fatal config; 3 every tenant errored.

The runbook in
``infra/supabase/migrations/20260510120000_byok_hardening.sql`` lays
out the full deploy procedure (add NEW_MASTER alongside the old one,
run this script, then swap and remove the old key).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from uuid import UUID

import asyncpg
from src.billing.key_rotation import rotate_all

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rotate_master_key",
        description="Rotate LLM_MASTER_ENCRYPTION_KEY across BYOK columns.",
    )
    parser.add_argument(
        "--old-master",
        default=os.environ.get("LLM_MASTER_ENCRYPTION_KEY_OLD"),
        help="Current master key (env: LLM_MASTER_ENCRYPTION_KEY_OLD).",
    )
    parser.add_argument(
        "--new-master",
        default=os.environ.get("LLM_MASTER_ENCRYPTION_KEY"),
        help="Replacement master key (env: LLM_MASTER_ENCRYPTION_KEY).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (env: DATABASE_URL).",
    )
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        default=None,
        help="If set, rotate only this tenant. Useful for replays.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count tenants that WOULD be rotated; don't write anything.",
    )
    return parser


async def _amain(args: argparse.Namespace) -> int:
    if not args.old_master or not args.new_master:
        print(
            "error: --old-master and --new-master are required "
            "(or set env vars LLM_MASTER_ENCRYPTION_KEY_OLD / "
            "LLM_MASTER_ENCRYPTION_KEY).",
            file=sys.stderr,
        )
        return 2
    if not args.database_url:
        print(
            "error: --database-url is required (or set DATABASE_URL).",
            file=sys.stderr,
        )
        return 2

    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=4)
    if pool is None:
        print("error: failed to open Postgres pool", file=sys.stderr)
        return 2

    try:
        try:
            report = await rotate_all(
                pool,
                old_master=args.old_master,
                new_master=args.new_master,
                only_tenant_id=args.tenant_id,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            # old/new equal or empty → operator mistake.
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        await pool.close()

    print(json.dumps(report.as_dict(), indent=2))

    # Exit 3 if everything failed; 0 otherwise (partial failure is still
    # "process kept going as designed").
    if (
        report.tenants_processed > 0
        and len(report.errors) == report.tenants_processed
    ):
        return 3
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
