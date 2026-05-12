"""Local-dev helper: provision a tenant + public.users row for a Supabase
auth user that already signed up via the FE.

Workflow:

1. Bring up the local Supabase stack (``supabase start``) OR point
   ``DATABASE_URL`` / ``SUPABASE_URL`` at the local Docker stack the
   `make dev` target boots.
2. Sign up through the FE at ``http://localhost:3000/signup``. Supabase
   inserts a row into ``auth.users`` with the email you typed.
3. Run this script — it reads ``auth.users``, picks the matching email
   (defaults to the most recently signed-up user) and inserts the
   matching ``tenants`` + ``public.users`` rows the rest of the
   application keys off.

Why this exists: ``public.users`` is the multi-tenancy join row, but
the FE's signup flow doesn't write to it yet (Sprint 4 backlog —
"onboarding wizard"). Until the wizard ships, running this script
once per new dev account is the fastest way to get past the
`/onboarding` redirect in the layout.

Usage::

    poetry run python scripts/seed_dev_user.py --email alice@example.com

If ``--email`` is omitted the script grabs the newest ``auth.users``
row, which is usually the one you just signed up with. Idempotent: a
second run for the same email is a no-op.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid

import asyncpg

logger = logging.getLogger(__name__)


async def seed(database_url: str, *, email: str | None, tenant_name: str | None) -> int:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    if pool is None:
        print("preflight: create_pool returned None", file=sys.stderr)
        return 2

    try:
        async with pool.acquire() as conn:
            if email is None:
                row = await conn.fetchrow(
                    "select id, email from auth.users "
                    "order by created_at desc limit 1"
                )
                if row is None:
                    print(
                        "no auth.users rows found. Sign up via the FE first "
                        "(http://localhost:3000/signup)",
                        file=sys.stderr,
                    )
                    return 1
            else:
                row = await conn.fetchrow(
                    "select id, email from auth.users where lower(email) = lower($1)",
                    email,
                )
                if row is None:
                    print(
                        f"no auth.users row for {email}. Sign up via the FE first.",
                        file=sys.stderr,
                    )
                    return 1

            auth_user_id = uuid.UUID(str(row["id"]))
            auth_email = str(row["email"])

            existing = await conn.fetchrow(
                "select tenant_id, role from public.users where id = $1",
                auth_user_id,
            )
            if existing is not None:
                print(
                    f"already provisioned: user_id={auth_user_id} "
                    f"tenant_id={existing['tenant_id']} role={existing['role']}"
                )
                return 0

            tenant_id = uuid.uuid4()
            tenant_label = tenant_name or auth_email.split("@", 1)[0]
            slug = (
                tenant_label.lower()
                .replace(" ", "-")
                .replace(".", "-")
                .replace("_", "-")
            ) + f"-{tenant_id.hex[:6]}"

            async with conn.transaction():
                await conn.execute(
                    "insert into tenants (id, name, slug) values ($1, $2, $3)",
                    tenant_id,
                    tenant_label,
                    slug,
                )
                await conn.execute(
                    """
                    insert into public.users (id, tenant_id, role, display_name)
                    values ($1, $2, 'owner', $3)
                    """,
                    auth_user_id,
                    tenant_id,
                    tenant_label,
                )

            print(
                f"seeded user_id={auth_user_id} tenant_id={tenant_id} "
                f"role=owner email={auth_email}"
            )
            return 0
    finally:
        await pool.close()


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--email",
        default=None,
        help="auth.users.email to provision. Defaults to the newest signup.",
    )
    parser.add_argument(
        "--tenant-name",
        default=None,
        help="Human-readable tenant name (defaults to email local-part).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN. Defaults to $DATABASE_URL.",
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "DATABASE_URL not set and --database-url not passed. "
            "Point this at the local Supabase or Docker Postgres.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(
        seed(
            args.database_url,
            email=args.email,
            tenant_name=args.tenant_name,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
