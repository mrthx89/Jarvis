"""Tests for streaming sentence chunker in Hermes -> TTS pipeline."""

import pytest
from nexo_server.hermes.sentence_chunker import (
    extract_next_sentence,
    split_into_sentences,
    clean_line_is_banner_or_footer,
)


def test_sentence_chunker_basic_delimiters():
    text = "Halo, selamat pagi! Bagaimana kabarmu hari ini? Saya Nexo, siap membantu.\nSilakan tanyakan apa saja."
    sentences = split_into_sentences(text)
    assert sentences == [
        "Halo, selamat pagi!",
        "Bagaimana kabarmu hari ini?",
        "Saya Nexo, siap membantu.",
        "Silakan tanyakan apa saja.",
    ]


def test_sentence_chunker_streaming_incremental():
    stream_chunks = ["Halo, ", "apa ", "kabar? ", "Saya ", "Nexo. ", "Ada yang ", "bisa dibantu?"]
    buf = ""
    yielded = []
    for c in stream_chunks:
        buf += c
        while True:
            s, buf = extract_next_sentence(buf, at_eof=False)
            if s is None:
                break
            yielded.append(s)

    # Process EOF
    while True:
        s, buf = extract_next_sentence(buf, at_eof=True)
        if s is None:
            break
        yielded.append(s)

    assert yielded == [
        "Halo, apa kabar?",
        "Saya Nexo.",
        "Ada yang bisa dibantu?",
    ]
    assert buf == ""


def test_sentence_chunker_strips_banners_and_footers():
    raw = (
        "↻ Resumed session 'nexo' (local-12345)\n"
        "Model restored from session: hermes-agent\n"
        "\n"
        "Tentu, saya bisa bantu.\n"
        "Ini adalah baris kedua.\n"
        "session_id: local-12345\n"
    )
    sentences = split_into_sentences(raw)
    assert sentences == [
        "Tentu, saya bisa bantu.",
        "Ini adalah baris kedua.",
    ]


def test_sentence_chunker_preserves_decimals_abbreviations_and_lists():
    text = (
        "Dr. Sutomo tinggal di Jl. Sudirman No. 10. "
        "Nilai pi adalah 3.14159 dan harga barang Rp 25.000. "
        "1. Nyalakan perangkat. 2. Sambungkan Wi-Fi."
    )
    sentences = split_into_sentences(text)
    assert len(sentences) == 4
    assert sentences[0] == "Dr. Sutomo tinggal di Jl. Sudirman No. 10."
    assert sentences[1] == "Nilai pi adalah 3.14159 dan harga barang Rp 25.000."
    assert sentences[2] == "1. Nyalakan perangkat."
    assert sentences[3] == "2. Sambungkan Wi-Fi."


def test_sentence_chunker_eof_without_trailing_punctuation():
    text = "Satu kalimat pendek tanpa tanda titik"
    sentences = split_into_sentences(text)
    assert sentences == ["Satu kalimat pendek tanpa tanda titik"]


def test_sentence_chunker_empty_input():
    assert split_into_sentences("") == []
    assert split_into_sentences("   \n\n  ") == []
