"""Pydantic request/response models for API endpoints."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, HttpUrl


class DownloadRequest(BaseModel):
    url: str
    category_id: int
    quality: Literal["1080p", "best"] = "1080p"
    custom_title: str | None = None


class CategoryCreate(BaseModel):
    name: str
    path: str
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str
    path: str
    description: str | None = None
