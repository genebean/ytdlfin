"""Unit tests for NFO XML generation — pure functions, no mocking needed."""

import xml.etree.ElementTree as ET
from datetime import date

from ytdlfin.nfo import generate_nfo, write_nfo

# Minimal info dict matching what yt-dlp returns.
BASE_INFO = {
    "title": "Raw YT Title",
    "id": "abc123",
    "description": "A test video description.",
    "upload_date": "20240315",
    "channel": "Test Channel",
    "extractor_key": "Youtube",
    "categories": ["Education", "Science"],
    "tags": ["python", "testing"],
}


def _parse(info=BASE_INFO, display_title="Display Title") -> ET.Element:
    xml = generate_nfo(info, display_title)
    return ET.fromstring(xml.split("\n", 1)[1])  # strip the <?xml?> declaration


class TestGenerateNfo:
    def test_xml_declaration_present(self):
        xml = generate_nfo(BASE_INFO, "Title")
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"')

    def test_root_element_is_movie(self):
        root = _parse()
        assert root.tag == "movie"

    def test_display_title_in_title(self):
        root = _parse(display_title="My Custom Title")
        assert root.findtext("title") == "My Custom Title"

    def test_raw_title_in_originaltitle(self):
        root = _parse(display_title="Custom")
        assert root.findtext("originaltitle") == "Raw YT Title"

    def test_when_no_custom_title_both_titles_match(self):
        root = _parse(display_title="Raw YT Title")
        assert root.findtext("title") == root.findtext("originaltitle")

    def test_description_in_plot(self):
        root = _parse()
        assert root.findtext("plot") == "A test video description."

    def test_description_truncated_at_4000_chars(self):
        long_desc = "x" * 5000
        info = {**BASE_INFO, "description": long_desc}
        root = _parse(info)
        assert len(root.findtext("plot")) == 4000

    def test_empty_description(self):
        info = {**BASE_INFO, "description": None}
        root = _parse(info)
        assert root.findtext("plot") == ""

    def test_upload_date_year_and_premiered(self):
        root = _parse()
        assert root.findtext("year") == "2024"
        assert root.findtext("premiered") == "2024-03-15"

    def test_missing_upload_date_omits_year(self):
        info = {**BASE_INFO, "upload_date": None}
        root = _parse(info)
        assert root.find("year") is None
        assert root.find("premiered") is None

    def test_short_upload_date_omits_year(self):
        info = {**BASE_INFO, "upload_date": "202"}
        root = _parse(info)
        assert root.find("year") is None

    def test_dateadded_is_today(self):
        root = _parse()
        assert root.findtext("dateadded") == date.today().isoformat()

    def test_channel_used_as_studio(self):
        root = _parse()
        assert root.findtext("studio") == "Test Channel"

    def test_uploader_fallback_when_no_channel(self):
        info = {**BASE_INFO, "channel": None, "uploader": "Fallback Uploader"}
        root = _parse(info)
        assert root.findtext("studio") == "Fallback Uploader"

    def test_empty_studio_when_neither_channel_nor_uploader(self):
        info = {**BASE_INFO, "channel": None, "uploader": None}
        root = _parse(info)
        assert root.findtext("studio") == ""

    def test_uniqueid_text_is_video_id(self):
        root = _parse()
        uid = root.find("uniqueid")
        assert uid is not None
        assert uid.text == "abc123"

    def test_uniqueid_type_attribute_from_extractor_key(self):
        root = _parse()
        uid = root.find("uniqueid")
        assert uid.get("type") == "youtube"  # lowercased

    def test_uniqueid_default_attribute(self):
        root = _parse()
        uid = root.find("uniqueid")
        assert uid.get("default") == "true"

    def test_id_element_contains_video_id(self):
        root = _parse()
        assert root.findtext("id") == "abc123"

    def test_source_is_web_dl(self):
        root = _parse()
        assert root.findtext("source") == "WEB-DL"

    def test_categories_become_genre_elements(self):
        root = _parse()
        genres = [el.text for el in root.findall("genre")]
        assert genres == ["Education", "Science"]

    def test_no_genre_elements_when_no_categories(self):
        info = {**BASE_INFO, "categories": []}
        root = _parse(info)
        assert root.findall("genre") == []

    def test_tags_become_tag_elements(self):
        root = _parse()
        tags = [el.text for el in root.findall("tag")]
        assert tags == ["python", "testing"]

    def test_tags_capped_at_20(self):
        info = {**BASE_INFO, "tags": [f"tag{i}" for i in range(30)]}
        root = _parse(info)
        assert len(root.findall("tag")) == 20

    def test_no_tag_elements_when_tags_none(self):
        info = {**BASE_INFO, "tags": None}
        root = _parse(info)
        assert root.findall("tag") == []

    def test_fallback_extractor_key(self):
        info = {**BASE_INFO, "extractor_key": None}
        root = _parse(info)
        uid = root.find("uniqueid")
        assert uid.get("type") == "generic"


class TestWriteNfo:
    def test_write_nfo_creates_file(self, tmp_path):
        dest = tmp_path / "video.nfo"
        write_nfo(BASE_INFO, "My Title", dest)
        assert dest.exists()

    def test_write_nfo_content_is_valid_xml(self, tmp_path):
        dest = tmp_path / "video.nfo"
        write_nfo(BASE_INFO, "My Title", dest)
        content = dest.read_text(encoding="utf-8")
        # Should parse without errors after stripping the declaration line
        ET.fromstring(content.split("\n", 1)[1])

    def test_write_nfo_utf8_encoding(self, tmp_path):
        dest = tmp_path / "video.nfo"
        write_nfo({**BASE_INFO, "title": "Ünïcödé Títlé"}, "Ünïcödé Títlé", dest)
        content = dest.read_text(encoding="utf-8")
        assert "Ünïcödé Títlé" in content
