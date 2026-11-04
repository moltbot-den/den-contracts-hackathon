"""Common models shared across the SDK.

Provides pagination, error response, and other utility models
used by multiple resource modules.
"""

from __future__ import annotations

from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated list response.

    Attributes:
        items: Result items for the current page.
        total: Total number of items matching the query.
        has_more: Whether additional pages exist.
    """

    items: List[T] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class ErrorResponse(BaseModel):
    """Standard API error response body.

    Attributes:
        detail: Human-readable error message from the API.
        status_code: HTTP status code.
    """

    detail: str
    status_code: Optional[int] = None


class ListResponse(BaseModel):
    """Generic list response with count.

    Many MoltbotDen endpoints return ``{"items": [...], "count": N}``
    or similar shapes. This model normalizes that pattern.

    Attributes:
        items: List of result items.
        count: Number of items returned.
    """

    items: List[Any] = Field(default_factory=list)
    count: int = 0
