"""Unit tests for Pydantic models and shared quality normalization."""

import pytest

from ytdlfin.models import CategoryCreate, CategoryUpdate, DownloadRequest, normalize_quality


class TestNormalizeQuality:
    def test_valid_resolution(self):
        assert normalize_quality("1080p") == "1080p"

    def test_other_valid_resolutions(self):
        for q in ("360p", "480p", "720p", "1440p", "2160p"):
            assert normalize_quality(q) == q

    def test_best(self):
        assert normalize_quality("best") == "best"

    def test_invalid_falls_back_to_1080p(self):
        assert normalize_quality("high") == "1080p"
        assert normalize_quality("") == "1080p"
        assert normalize_quality("1080") == "1080p"  # missing "p"
        assert normalize_quality("p1080") == "1080p"
        assert normalize_quality("1080P") == "1080p"  # uppercase P not matched


class TestDownloadRequest:
    def test_quality_validator_passes_through_valid(self):
        req = DownloadRequest(url="https://example.com", category_id=1, quality="720p")
        assert req.quality == "720p"

    def test_quality_validator_normalizes_invalid(self):
        req = DownloadRequest(url="https://example.com", category_id=1, quality="ultra")
        assert req.quality == "1080p"

    def test_quality_defaults_to_1080p(self):
        req = DownloadRequest(url="https://example.com", category_id=1)
        assert req.quality == "1080p"

    def test_custom_title_optional(self):
        req = DownloadRequest(url="https://example.com", category_id=1)
        assert req.custom_title is None

    def test_custom_title_set(self):
        req = DownloadRequest(
            url="https://example.com", category_id=1, custom_title="My Title"
        )
        assert req.custom_title == "My Title"


class TestCategoryModels:
    def test_category_update_is_subclass_of_create(self):
        assert issubclass(CategoryUpdate, CategoryCreate)

    def test_category_update_has_same_fields(self):
        assert CategoryCreate.model_fields.keys() == CategoryUpdate.model_fields.keys()

    def test_category_description_optional(self):
        cat = CategoryCreate(name="Movies", path="/media/movies")
        assert cat.description is None

    def test_category_description_set(self):
        cat = CategoryCreate(name="Movies", path="/media/movies", description="Feature films")
        assert cat.description == "Feature films"
