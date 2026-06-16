"""Unit tests for shared validation utilities."""

import os
import pytest

from ytdlfin.utils import _validate_url_scheme, _validate_path


class TestValidateUrlScheme:
    def test_http_allowed(self):
        assert _validate_url_scheme("http://example.com/video") is True

    def test_https_allowed(self):
        assert _validate_url_scheme("https://www.youtube.com/watch?v=abc") is True

    def test_file_scheme_rejected(self):
        assert _validate_url_scheme("file:///etc/passwd") is False

    def test_rtmp_rejected(self):
        assert _validate_url_scheme("rtmp://stream.example.com/live") is False

    def test_ftp_rejected(self):
        assert _validate_url_scheme("ftp://files.example.com/video.mp4") is False

    def test_empty_string_rejected(self):
        assert _validate_url_scheme("") is False

    def test_no_scheme_rejected(self):
        assert _validate_url_scheme("example.com/video") is False

    def test_javascript_rejected(self):
        assert _validate_url_scheme("javascript:alert(1)") is False

    def test_data_url_rejected(self):
        assert _validate_url_scheme("data:text/html,<h1>test</h1>") is False


class TestValidatePath:
    def test_valid_writable_dir(self, tmp_path):
        # tmp_path is a real writable directory — should not raise
        _validate_path(str(tmp_path))

    def test_nonexistent_path_raises_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_path("/nonexistent/path/that/does/not/exist")
        assert exc_info.value.status_code == 400

    def test_file_not_dir_raises_400(self, tmp_path):
        from fastapi import HTTPException

        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(HTTPException) as exc_info:
            _validate_path(str(f))
        assert exc_info.value.status_code == 400

    def test_path_outside_media_directories_raises_400(self, tmp_path, monkeypatch):
        from fastapi import HTTPException
        from pathlib import Path
        import ytdlfin.utils as utils_module

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        other = tmp_path / "other"
        other.mkdir()

        monkeypatch.setattr(utils_module, "MEDIA_DIRECTORIES", [allowed.resolve()])

        with pytest.raises(HTTPException) as exc_info:
            _validate_path(str(other))
        assert exc_info.value.status_code == 400
        assert "allowed media directory" in exc_info.value.detail

    def test_path_inside_media_directories_passes(self, tmp_path, monkeypatch):
        from pathlib import Path
        import ytdlfin.utils as utils_module

        root = tmp_path / "media"
        root.mkdir()
        subdir = root / "movies"
        subdir.mkdir()

        monkeypatch.setattr(utils_module, "MEDIA_DIRECTORIES", [root.resolve()])

        # Should not raise
        _validate_path(str(subdir))

    def test_path_equal_to_media_directory_passes(self, tmp_path, monkeypatch):
        import ytdlfin.utils as utils_module

        monkeypatch.setattr(utils_module, "MEDIA_DIRECTORIES", [tmp_path.resolve()])
        _validate_path(str(tmp_path))
