# Nexo Server

Gateway audio Python untuk Nexo Dedicated Appliance. Klien Android menangani capture/playback dan UI; server menangani VAD, STT, Hermes, TTS, dan mirror jawaban final dari sesi terminal `nexo`.

## Persyaratan

- Python 3.11 atau lebih baru.
- Hermes CLI tersedia pada PATH layanan.
- Satu percakapan lokal Hermes bernama `nexo` sudah tersedia pada home Hermes yang sama.
- Konfigurasi YAML dan registry autentikasi perangkat disimpan di luar paket/Git.

## Instalasi paket

```sh
python3 -m venv .venv
.venv/bin/python -m pip install /path/to/nexo_server-0.1.0-py3-none-any.whl
NEXO_CONFIG_PATH=/path/to/config.yaml .venv/bin/python -m nexo_server.main
```

Gunakan `config.example.yaml` sebagai template. Atur registry perangkat dan provider sesuai deployment. Tanpa registry valid, autentikasi perangkat ditolak. Jangan memasukkan token, registry, atau konfigurasi produksi ke paket distribusi.

## Pengembangan dan build

Dari direktori `server`:

```sh
python3 -m pip install build
python3 -m build
```

Dari root repositori:

```sh
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q
```

Unit `deploy/nexo-appliance.service` memakai source checkout dengan `PYTHONPATH`, bukan wheel yang terpasang. Restart layanan diperlukan setelah source diperbarui. Verifikasi `/health` dan `/session/status`; status harus menunjukkan `session_locked: true` serta ID sesi kanonis yang sama setelah restart. Paket Python dan deployment systemd adalah dua hasil yang berbeda.

Server mengikat ID percakapan secara persisten di `~/.config/nexo/session_binding.json`. Binding yang konflik atau sesi yang hilang membuat startup gagal, bukan berpindah sesi. Untuk terminal, buka `hermes chat -c <hermes_session_id>` menggunakan ID dari `/session/status`. Mirror tidak memanggil model kedua dan tidak memutar ulang riwayat sebelum startup.

Referensi lengkap berada di `docs/TERMINAL_SESSION_BRIDGE.md` pada repositori utama. Tes deterministik server tidak membuktikan suara speaker fisik atau perilaku APK Android.
