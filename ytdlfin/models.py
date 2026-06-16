"""Pydantic request/response models for API endpoints."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_QUALITY_RE = re.compile(r"^\d+p$")


def normalize_quality(q: str) -> str:
    """Return q if valid ('best' or e.g. '1080p'), else '1080p'."""
    if q == "best" or _QUALITY_RE.match(q):
        return q
    return "1080p"


class DownloadRequest(BaseModel):
    url: str
    category_id: int
    quality: str = "1080p"
    custom_title: str | None = None

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: str) -> str:
        return normalize_quality(v)


class CategoryCreate(BaseModel):
    name: str
    path: str
    description: str | None = None


class CategoryUpdate(CategoryCreate):
    pass
