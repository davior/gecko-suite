"""What was spent, by whom, through what, for which app.

Every cost is a list-price estimate unless a row says otherwise, and a total over rows
with unknown prices is a floor, not a sum. So each group reports `priced_events` beside
`events` (GAM's rule): the screen can say "at least $4.20 (3 calls unpriced)".
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.models.usage import UsageEvent
from app.schemas import DataResponse
from app.services import access

router = APIRouter()


class UsageGroup(BaseModel):
    key: dict[str, Optional[str]]
    events: int
    priced_events: int
    # Sum of the priced events only. A floor whenever priced_events < events.
    cost: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


class UsageReport(BaseModel):
    days: int
    scope: str
    # Costs are summed as one currency; every price in the catalogue today is USD. A
    # second currency would need per-currency totals, and is refused until then.
    currency: str = "USD"
    totals: UsageGroup
    by_model: list[UsageGroup]
    by_app: list[UsageGroup]
    by_task: list[UsageGroup]
    by_day: list[UsageGroup]


def _group(session: Session, base, *columns) -> list[UsageGroup]:
    query = select(
        *columns,
        func.count(UsageEvent.id),
        func.count(UsageEvent.cost),  # COUNT(col) skips NULLs: exactly the priced rows
        func.coalesce(func.sum(UsageEvent.cost), 0.0),
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cache_read_tokens), 0),
    ).where(*base)
    if columns:
        query = query.group_by(*columns).order_by(*columns)
    groups = []
    for row in session.exec(query).all():
        keys, numbers = row[: len(columns)], row[len(columns):]
        groups.append(UsageGroup(
            key={c.key: (str(v) if v is not None else None) for c, v in zip(columns, keys)},
            events=numbers[0], priced_events=numbers[1], cost=round(float(numbers[2]), 6),
            input_tokens=numbers[3], output_tokens=numbers[4], cache_read_tokens=numbers[5],
        ))
    return groups


@router.get("", response_model=DataResponse[UsageReport])
def usage_report(
    user: CurrentUser,
    days: int = Query(default=30, ge=1, le=3650),
    app: Optional[str] = None,
    subject_ref: Optional[str] = None,
    scope: str = Query(default="me", pattern="^(me|all)$"),
    session: Session = Depends(get_session),
) -> DataResponse[UsageReport]:
    """The caller's own usage, or with `scope=all` (admins only) everyone's."""
    if scope == "all" and not access.caller_is_admin(session, user):
        raise access.admin_required("Only an administrator can see everyone's usage")

    base = [UsageEvent.created_at >= utcnow() - timedelta(days=days)]
    if scope == "me":
        base.append(UsageEvent.user_id == user.id)
    if app:
        base.append(UsageEvent.app == app)
    if subject_ref:
        base.append(UsageEvent.subject_ref == subject_ref)

    # date(), not CAST(... AS DATE): SQLite has no date type, and its CAST returns the
    # year as a number. date() returns "YYYY-MM-DD", which is also what the frontend wants.
    day = func.date(UsageEvent.created_at).label("day")
    totals = _group(session, base)[0]
    return DataResponse(data=UsageReport(
        days=days,
        scope=scope,
        totals=totals,
        by_model=_group(session, base, UsageEvent.vendor, UsageEvent.provider_name, UsageEvent.model),
        by_app=_group(session, base, UsageEvent.app),
        by_task=_group(session, base, UsageEvent.task),
        by_day=_group(session, base, day),
    ))
