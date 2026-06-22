from __future__ import annotations

from workforce_runtime.workers.process_runner import STREAM_EVENT_MAX_CHARS, _stream_text_flushes


def test_stream_text_flushes_wait_for_sentence_or_line_boundary() -> None:
    chunks, remainder = _stream_text_flushes("Thinking through")
    assert chunks == []
    assert remainder == "Thinking through"

    chunks, remainder = _stream_text_flushes("Thinking through the task. Next step")
    assert chunks == ["Thinking through the task. "]
    assert remainder == "Next step"

    chunks, remainder = _stream_text_flushes("First line\nSecond line")
    assert chunks == ["First line\n"]
    assert remainder == "Second line"


def test_stream_text_flushes_long_or_forced_fragments() -> None:
    long_text = "word " * (STREAM_EVENT_MAX_CHARS // 3)
    chunks, remainder = _stream_text_flushes(long_text)
    assert chunks
    assert len(remainder) < STREAM_EVENT_MAX_CHARS

    chunks, remainder = _stream_text_flushes("partial fragment", force=True)
    assert chunks == ["partial fragment"]
    assert remainder == ""
