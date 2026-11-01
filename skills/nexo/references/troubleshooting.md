# Panduan Pemecahan Masalah & Diagnostik Nexo Appliance

Dokumen referensi praktis untuk mendiagnosis, menganalisis log, dan memulihkan insiden pada **Nexo Dedicated Appliance**.

---

## 1. Perintah Diagnostik Cepat

Jalankan perintah ini di server untuk memeriksa status seluruh komponen:

```bash
# 1. Periksa unit systemd
systemctl --user status nexo-appliance.service

# 2. Periksa 50 baris log terakhir
journalctl --user -u nexo-appliance.service -n 50 --no-pager

# 3. Periksa apakah port 8765 aktif mendengarkan
ss -tulpn | grep 8765

# 4. Uji endpoint kesehatan HTTP
curl -fsS http://127.0.0.1:8765/health

# 5. Uji status appliance via CLI Nexo
/data/home/Jarvis/scripts/nexo status

# 6. Pantau streaming kognitif real-time
/data/home/Jarvis/scripts/nexo watch
```

---

## 2. Analisis & Solusi Masalah Spesifik

### Masalah A: Handshake Ditolak (Error HTTP 401/403 atau WebSocket Close 1008)

**Penyebab:**
1. Device ID belum terdaftar di `devices.json`.
2. Hash token yang dikirimkan klien tidak cocok dengan token yang terdaftar.
3. WSS Origin pada token berbeda dengan host server yang dituju.
4. Izin berkas `devices.json` terlalu longgar (server menolak registry jika bukan 0600 atau direktori bukan 0700).

**Langkah Perbaikan:**
1. Di layar Android, tekan lama status mikrofon untuk melihat `Device ID` dan `WSS Origin`.
2. Verifikasi isi registry server:
   ```bash
   cat ~/.config/nexo-auth/devices.json
   ```
3. Pastikan izin direktori dan file aman:
   ```bash
   chmod 700 ~/.config/nexo-auth
   chmod 600 ~/.config/nexo-auth/devices.json
   ```
4. Terbitkan ulang token untuk perangkat tersebut:
   ```bash
   cd /data/home/Jarvis/server
   PYTHONPATH=src .venv/bin/python -m nexo_server.auth provision \
     --registry ~/.config/nexo-auth/devices.json \
     --device-id "<DEVICE_ID>" \
     --origin "wss://domain-anda/ws/appliance" \
     --output ~/.config/nexo-auth/delivery.json
   ```
5. Masukkan token baru di Android, lalu hapus `delivery.json`.

---

### Masalah B: Sentence-Chunked Streaming: Jeda Antar Kalimat Lambat

**Penyebab:**
1. Model LLM tidak mengalirkan token (*buffered full output*), atau LLM endpoint lambat.
2. `SentenceChunker` tertahan oleh kalimat panjang tanpa tanda baca penutup.

**Langkah Perbaikan:**
1. Pantau stream kalimat secara real-time via `nexo watch`.
2. Verifikasi mode streaming aktif pada server backend.
3. Pastikan token pertama diterima dalam < 800 ms.

---

### Masalah C: Tag Emosi `[expr:...]` Terucap oleh Suara TTS

**Penyebab:**
Fungsi `parse_and_strip_emotional_tags` terlewati sebelum teks masuk ke `synthesize_stream`.

**Langkah Perbaikan:**
1. Jalankan unit test: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_expression_parser.py`.
2. Pastikan sanitasi regex `\[expr:[a-zA-Z0-9_\-]+\]` diterapkan sebelum teks diteruskan ke `edge_tts.Communicate`.

---

### Masalah D: Sinyal Audio Lokal (Earcons) Tidak Berbunyi

**Penyebab:**
1. Berkas audio `earcon_listening.wav` / `earcon_thinking.wav` belum digenerate di `android/app/src/main/res/raw/`.
2. Volume notifikasi / media di Android dibungkam (*muted*).

**Langkah Perbaikan:**
1. Jalankan script generator earcon:
   ```bash
   python3 /data/home/Jarvis/scripts/generate_earcons.py
   ```
2. Pastikan `EarconManager` menggunakan usage `USAGE_ASSISTANCE_SONIFICATION` dan volume perangkat diatur pada 70%.

---

### Masalah E: Gema / Feedback Audio Terus Menerus Sesaat Setelah TTS Berbicara

**Penyebab:**
1. Buffer `_playback_until` tidak mencukupi atau jeda *echo guard* 600 ms terlewati.
2. Hardware AEC tidak aktif pada chipset Android.
3. Volume speaker terlalu keras sehingga mendistorsi membran mikrofon.

**Langkah Perbaikan:**
1. Pastikan server memparsing durasi frame MP3 via `parse_audio_duration` (`mp3_frame.py`).
2. Pastikan jeda *echo guard* 600 ms aktif sebelum server berpindah ke `IDLE` dan membuka mikrofon.
3. Pastikan audio source di Android adalah `VOICE_COMMUNICATION`.
4. Turunkan volume media speaker ke tingkat moderat (50-70%).

---

### Masalah F: Ambient Sleep Mode Tidak Aktif / Layar Android Mati Total

**Penyebab:**
1. Inactivity timer Android terputus atau tidak terinisialisasi.
2. Flag `FLAG_KEEP_SCREEN_ON` tidak terpasang, menyebabkan Android OS masuk ke Doze mode.

**Langkah Perbaikan:**
1. Pastikan `FLAG_KEEP_SCREEN_ON` disetel pada window `MainActivity`.
2. Verifikasi state `SLEEPING` dipicu setelah 5 menit inaktivitas dan maskot menggunakan `mascot_sleeping.xml`.

---

### Masalah G: Hermes Agent CLI Gagal Merespons / Timeout

**Penyebab:**
1. Binary `hermes` tidak ditemukan dalam PATH akun systemd.
2. Model LLM atau provider di Hermes mengalami limit kuota atau timeout jaringan.
3. Database sesi Hermes (`state.db`) terkunci oleh proses lain.

**Langkah Perbaikan:**
1. Uji pemanggilan Hermes secara manual dari akun pengguna:
   ```bash
   hermes chat -c appliance --create-if-missing -q "Tes respon cepat" -Q
   ```
2. Pastikan file unit systemd memuat PATH lengkap:
   ```ini
   Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
   Environment=HOME=/root
   ```
3. Cek konfigurasi `server/config.yaml`:
   ```yaml
   hermes:
     hermes_binary: "/root/.local/bin/hermes" # Gunakan path absolut
     timeout_seconds: 60
   ```

---

### Masalah H: STT Lambat / Faster-Whisper Bermasalah

**Penyebab:**
1. Model Faster-Whisper belum di-cache dan koneksi ke HuggingFace terputus saat pertama kali dijalankan.
2. Kehabisan RAM saat memuat model.
3. VAD memotong audio terlalu awal karena background noise atau mikrofon terlalu kecil.

**Langkah Perbaikan:**
1. Pra-unduh model secara mandiri:
   ```bash
   cd /data/home/Jarvis/server
   .venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
   ```
2. Jika memori RAM terbatas (< 2 GB), gunakan model `tiny`:
   Ubah di `server/config.yaml`:
   ```yaml
   stt:
     model: "tiny"
   ```
3. Pantau log transkripsi saat berbicara:
   ```bash
   /data/home/Jarvis/scripts/nexo watch
   ```

---

### Masalah I: Edge-TTS Gagal Menghasilkan Audio

**Penyebab:**
1. Server Edge-TTS memblokir IP atau terjadi kegagalan resolusi DNS luar.
2. Sintesis kalimat mengandung karakter kontrol yang tidak valid.

**Langkah Perbaikan:**
1. Uji sintesis audio sederhana via Python:
   ```bash
   cd /data/home/Jarvis/server
   .venv/bin/python -c "import asyncio, edge_tts; asyncio.run(edge_tts.Communicate('Halo', 'id-ID-ArdiNeural').save('/tmp/test.mp3'))"
   ```
2. Periksa apakah berkas `/tmp/test.mp3` terbuat dengan ukuran > 0 byte.
3. Jika diblokir oleh ISP, pertimbangkan penggunaan DNS 1.1.1.1 / 8.8.8.8.
