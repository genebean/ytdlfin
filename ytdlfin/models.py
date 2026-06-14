"""Pydantic request/response models for API endpoints."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


class DownloadRequest(BaseModel):
    url: str
    category_id: int
    quality: str = "1080p"
    custom_title: str | None = None

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: str) -> str:
        if v == "best" or re.match(r"^\d+p$", v):
            return v
        return "1080p"


class CategoryCreate(BaseModel):
    name: str
    path: str
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str
    path: str
    description: str | None = None
