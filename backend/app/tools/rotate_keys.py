"""Re-encrypt every stored secret under the current encryption key.

    python -m app.tools.rotate_keys [--dry-run]

Rotation, in three steps:

1. Put a new key first in SUITE_ENCRYPTION_KEYS, keeping the old one after it, and
   restart. Everything still reads (MultiFernet tries every key); new writes use the new one.
2. Run this. It re-encrypts every stored secret under the first key.
3. Remove the old key from SUITE_ENCRYPTION_KEYS and restart.

It stops at the first value no configured key can read and changes nothing, rather than
skipping it: skipping would let step 3 destroy that key for good.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from sqlmodel import Session, select

from app import crypto
from app.models.ai import AIProvider
from app.models.library import WebSearchProvider

# Every table and column holding a value from app.crypto.encrypt. A new secret column
# must be added here, or rotation will leave it behind on the old key.
SECRET_COLUMNS = ((AIProvider, "api_key"), (WebSearchProvider, "api_key"))


def rotate(session: Session, *, dry_run: bool = False) -> int:
    count = 0
    for model, column in SECRET_COLUMNS:
        for row in session.exec(select(model)).all():
            stored = getattr(row, column)
            if not stored:
                continue
            try:
                setattr(row, column, crypto.reencrypt(stored))
            except Exception as exc:
                session.rollback()
                raise SystemExit(
                    f"{model.__tablename__} {row.id}: no configured key can read this value. Nothing "
                    "was changed. Put back the key it was written with before removing any."
                ) from exc
            session.add(row)
            count += 1
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return count


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt every stored secret under the current key.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from app.database import engine

    with Session(engine) as session:
        count = rotate(session, dry_run=args.dry_run)
    print(f"{'Would re-encrypt' if args.dry_run else 'Re-encrypted'} {count} secret(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
