"""Small server-side pagination helpers for HTML views."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from flask import request, url_for
from sqlalchemy import func, select

from extensions import db


@dataclass(frozen=True)
class Page:
    items: list
    page: int
    per_page: int
    total: int
    pages: int
    param: str

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first_item(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def last_item(self) -> int:
        return min(self.page * self.per_page, self.total)


def paginate(statement, *, param: str = "page", per_page: int = 25) -> Page:
    try:
        requested_page = int(request.args.get(param, "1"))
    except (TypeError, ValueError):
        requested_page = 1
    requested_page = max(1, requested_page)
    total = db.session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    pages = max(1, ceil(total / per_page))
    page = min(requested_page, pages)
    items = db.session.execute(
        statement.limit(per_page).offset((page - 1) * per_page)
    ).unique().scalars().all()
    return Page(items, page, per_page, total, pages, param)


def page_navigation(page: Page) -> dict:
    args = request.args.to_dict(flat=True)
    view_args = request.view_args or {}

    def href(number: int) -> str:
        return url_for(request.endpoint, **{**view_args, **args, page.param: number})

    visible = {1, page.pages}
    visible.update(range(max(1, page.page - 2), min(page.pages, page.page + 2) + 1))
    links = []
    previous_number = None
    for number in sorted(visible):
        if previous_number is not None and number - previous_number > 1:
            links.append({"ellipsis": True})
        links.append(
            {
                "ellipsis": False,
                "number": number,
                "url": href(number),
                "current": number == page.page,
            }
        )
        previous_number = number
    return {
        "page": page,
        "links": links,
        "previous_url": href(page.page - 1) if page.has_prev else None,
        "next_url": href(page.page + 1) if page.has_next else None,
    }
