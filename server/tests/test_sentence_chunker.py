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


def test_sentence_chunker_punctuation_variants():
    # Multiple marks like ?! and ...
    text = "Apakah kamu yakin?! Tentu saja! Sedang memeriksa data... Semua aman."
    sentences = split_into_sentences(text)
    assert sentences == [
        "Apakah kamu yakin?!",
        "Tentu saja!",
        "Sedang memeriksa data...",
        "Semua aman.",
    ]

    # Quotes and brackets
    text2 = "Pastikan baterai terpasang (minimal 50%). Lalu tekan tombol power."
    sentences2 = split_into_sentences(text2)
    assert len(sentences2) == 2
    assert sentences2[0] == "Pastikan baterai terpasang (minimal 50%)."
    assert sentences2[1] == "Lalu tekan tombol power."


def test_sentence_chunker_streaming_char_by_char():
    source = "Halo kawan! Apa kabar? Baik."
    buf = ""
    yielded = []
    for ch in source:
        buf += ch
        while True:
            s, buf = extract_next_sentence(buf, at_eof=False)
            if s is None:
                break
            yielded.append(s)

    s, buf = extract_next_sentence(buf, at_eof=True)
    if s is not None:
        yielded.append(s)

    assert yielded == ["Halo kawan!", "Apa kabar?", "Baik."]
    assert buf == ""


def test_sentence_chunker_multiple_sentences_single_buffer():
    buf = "Kalimat pertama. Kalimat kedua! Kalimat ketiga?\nKalimat keempat."
    yielded = []
    while True:
        s, buf = extract_next_sentence(buf, at_eof=False)
        if s is None:
            break
        yielded.append(s)

    s, buf = extract_next_sentence(buf, at_eof=True)
    if s is not None:
        yielded.append(s)

    assert yielded == [
        "Kalimat pertama.",
        "Kalimat kedua!",
        "Kalimat ketiga?",
        "Kalimat keempat.",
    ]


def test_sentence_chunker_indonesian_abbreviations_regression():
    text = (
        "Pertemuan bersama Prof. Dr. Sutomo dan Ir. Sukarno di Jl. Sudirman No. 10. "
        "Kerja sama ini melibatkan PT. Maju Lancar dan CV. Abadi, dll."
    )
    sentences = split_into_sentences(text)
    assert len(sentences) == 2
    assert sentences[0] == "Pertemuan bersama Prof. Dr. Sutomo dan Ir. Sukarno di Jl. Sudirman No. 10."
    assert sentences[1] == "Kerja sama ini melibatkan PT. Maju Lancar dan CV. Abadi, dll."


def test_sentence_chunker_dual_compatibility():
    import jarvis_server.hermes.sentence_chunker as jarvis_sc
    import nexo_server.hermes.sentence_chunker as nexo_sc

    sample = "Halo dunia! Ini pengujian dual compatibility."
    assert jarvis_sc.split_into_sentences(sample) == nexo_sc.split_into_sentences(sample)

