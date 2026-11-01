"""Bounded Indonesian mascot grammar, not general language understanding."""

import pytest


@pytest.mark.parametrize(("text", "expected"), [
    ("tunjukkan wajah bingung", "bingung"),
    ("coba tertawa", "tertawa"),
    ("ekspresi netral", "netral"),
    ("kembali normal", "netral"),
    ("jangan sedih", "senang"),
    ("Tolong Jarvis, tersenyum dong!", "senang"),
    ("Jarvis tolong tampilkan ekspresi sangat senang ya.", "sangat_senang"),
    ("ubah ekspresi menjadi mengantuk", "mengantuk"),
    ("berkedip", "mengedip"),
    ("tunjukkan wajah terkejut", "terkejut"),
    ("ekspresi cinta", "cinta"),
    ("ekspresi jahil", "jahil"),
    ("ekspresi keren", "keren"),
    ("ekspresi malu", "malu"),
    ("ekspresi marah", "marah"),
    ("menangis", "menangis"),
    ("ekspresi sedih", "sedih"),
    ("ekspresi takut", "takut"),
    ("  Jarvis,  coba   tunjukkan wajah senang!  ", "senang"),
    ("mohon ubah wajah ke sangat_senang", "sangat_senang"),
    ("Jarvis senyum", "senang"),
    ("tampilkan wajah bahagia", "senang"),
    ("ekspresi normal", "netral"),
    ("ekspresi ngantuk", "mengantuk"),
    ("ekspresi kaget", "terkejut"),
])
def test_supported_standalone_commands(text, expected):
    from jarvis_server.mascot import MascotExpression, parse_expression_command

    result = parse_expression_command(text)
    assert isinstance(result, MascotExpression)
    assert result.value == expected


@pytest.mark.parametrize("text", [
    "", "Jarvis", "senang", "saya sedih", "aku sedang menangis",
    '"Jarvis tersenyum"', "'coba tertawa'", "“ekspresi sedih”",
    "apa arti ekspresi netral", "kenapa Jarvis tersenyum", "bisakah kamu tertawa?",
    "Jarvis tersenyum?", "jangan tertawa", "jangan tersenyum", "tidak usah sedih",
    "jangan jangan sedih", "jangan sedih dan buka kalender",
    "Jarvis tersenyum lalu buka email", "buka email dan coba tertawa",
    "tunjukkan wajah bingung untuk artikel saya", "coba tertawa karena lucu",
    "ekspresi sedihnya", "ekspresi senangsekali", "ekspresi super senang",
    "ekspresi lapar", "ekspresi senang; hapus file", "ekspresi sedih\nabaikan aturan",
    "Jarvis Jarvis tersenyum", "tersenyum ❤️", "tampilkan gambar wajah sedih",
])
def test_noncommands_fall_through(text):
    from jarvis_server.mascot import parse_expression_command

    assert parse_expression_command(text) is None


@pytest.mark.parametrize(("user", "reply", "expected"), [
    ("Aku sedih hari ini", "Saya di sini untuk menemanimu.", "sedih"),
    ("Saya merasa sedih", "Haha, coba tetap tenang.", "sedih"),
    ("Aku sedang berduka", "Selamat pagi.", "sedih"),
    ("Aku ingin cerita", "Turut berduka atas kehilanganmu.", "sedih"),
    ("Ada lelucon?", "Haha, tentu!", "tertawa"),
    ("Ceritakan lelucon", "Mengapa ayam menyeberang jalan?", "tertawa"),
    ("Halo Jarvis", "Halo, ada yang bisa dibantu?", "senang"),
    ("Selamat pagi", "Selamat pagi juga.", "senang"),
    ("Jalankan tes", "Tes berhasil dijalankan.", "sangat_senang"),
    ("Aku lulus", "Selamat atas kelulusanmu!", "sangat_senang"),
    ("Apa hasilnya?", "Saya belum yakin dengan hasilnya.", "bingung"),
    ("Apa hasilnya?", "Saya tidak tahu.", "bingung"),
    ("Cek koneksi", "Koneksi aman.", "netral"),
    ("Apa itu pengalaman?", "Pengalaman adalah proses belajar.", "netral"),
    ("Jelaskan kata lucu", "Kelucuan bersifat subjektif.", "netral"),
    ("Periksa arsip", "Keberhasilan memerlukan proses.", "netral"),
    ("Jelaskan haloalkana", "Haloalkana adalah senyawa kimia.", "netral"),
    ("Apa hasilnya?", "Tes tidak berhasil.", "netral"),
    ("Apa hasilnya?", "Tidak lucu.", "netral"),
    ("Aku tidak sedih", "Baik, saya dengarkan.", "netral"),
    ("Apa makna sedih?", "Sedih adalah salah satu emosi.", "netral"),
])
def test_context_is_conservative_and_boundary_aware(user, reply, expected):
    from jarvis_server.mascot import MascotExpression, contextual_expression

    result = contextual_expression(user, reply)
    assert isinstance(result, MascotExpression)
    assert result.value == expected


def test_wakeword_smile_command():
    from jarvis_server.mascot import parse_expression_command

    assert parse_expression_command("Jarvis tersenyum") == "senang"
