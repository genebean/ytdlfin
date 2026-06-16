"""Unit tests for yt-dlp format selection logic — no network, no yt-dlp calls."""

from ytdlfin.ytdlp import FORMAT_1080P, FORMAT_BEST, _format_for_height, _make_progress_hook


class TestFormatConstants:
    def test_format_1080p_is_string(self):
        assert isinstance(FORMAT_1080P, str) and FORMAT_1080P

    def test_format_best_is_string(self):
        assert isinstance(FORMAT_BEST, str) and FORMAT_BEST

    def test_format_1080p_prefers_mp4_m4a(self):
        assert "ext=mp4" in FORMAT_1080P
        assert "ext=m4a" in FORMAT_1080P

    def test_format_best_prefers_mp4_m4a(self):
        assert "ext=mp4" in FORMAT_BEST
        assert "ext=m4a" in FORMAT_BEST

    def test_format_1080p_has_height_cap(self):
        assert "height<=1080" in FORMAT_1080P

    def test_format_1080p_has_fallback_chain(self):
        # Must end with an unconditional /best so yt-dlp always picks something
        assert FORMAT_1080P.endswith("/best")

    def test_format_best_has_fallback_chain(self):
        assert FORMAT_BEST.endswith("/best")

    def test_format_1080p_and_best_differ(self):
        # Sanity check — they should not be the same string
        assert FORMAT_1080P != FORMAT_BEST


class TestFormatForHeight:
    def test_height_in_all_format_parts(self):
        fmt = _format_for_height(720)
        # All three parts of the fallback chain should cap at 720
        assert fmt.count("height<=720") == 3

    def test_1080_matches_manual_format(self):
        # _format_for_height(1080) should be equivalent to FORMAT_1080P
        assert _format_for_height(1080) == FORMAT_1080P

    def test_prefers_mp4_m4a(self):
        fmt = _format_for_height(480)
        assert "ext=mp4" in fmt
        assert "ext=m4a" in fmt

    def test_ends_with_unconditional_best(self):
        assert _format_for_height(360).endswith("/best")

    def test_various_heights(self):
        for h in (360, 480, 720, 1080, 1440, 2160):
            fmt = _format_for_height(h)
            assert f"height<={h}" in fmt


class TestMakeProgressHook:
    def test_hook_calls_on_progress_with_status(self):
        calls = []
        hook = _make_progress_hook(lambda status, d: calls.append((status, d)))
        hook({"status": "downloading", "downloaded_bytes": 1024})
        assert calls == [("downloading", {"status": "downloading", "downloaded_bytes": 1024})]

    def test_hook_passes_full_dict(self):
        received = []
        hook = _make_progress_hook(lambda s, d: received.append(d))
        data = {"status": "finished", "filename": "video.mp4", "total_bytes": 9999}
        hook(data)
        assert received[0] is data
