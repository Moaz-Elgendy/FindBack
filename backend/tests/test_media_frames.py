from pathlib import Path
import pytest
from app.services import media_understanding as media


def test_frames_are_capped_and_scaled(monkeypatch, tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        (tmp_path / 'frame-001.jpg').write_bytes(b'jpeg')
    monkeypatch.setattr(media.subprocess, 'run', run)
    monkeypatch.setenv('MEDIA_FRAME_CAP', '99')
    frames = media.sample_frames(tmp_path / 'video.mp4', tmp_path, 30)
    assert len(frames) == 1
    assert calls[0][calls[0].index('-frames:v') + 1] == '12'
    assert 'scale=720' in calls[0][calls[0].index('-vf') + 1]
    assert 'scene' in calls[0][calls[0].index('-vf') + 1]


def test_ocr_supports_arabic_and_english(monkeypatch, tmp_path):
    calls = []
    class Result: stdout = 'مهارة كتابة الاختبارات\nClaude Code'
    def run(args, **kwargs): calls.append(args); return Result()
    monkeypatch.setattr(media.subprocess, 'run', run)
    assert 'Claude Code' in media.read_frame(tmp_path / 'frame.jpg')
    assert calls[0][calls[0].index('-l') + 1] == 'eng+ara'
