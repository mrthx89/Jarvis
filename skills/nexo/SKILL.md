---
name: nexo
description: Use when operating Nexo. Manage and deploy smart appliance.
version: 1.0.0
author: Yanto Hariyono (YH Dev), mrthx89
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [nexo, appliance, smart-screen, voice-assistant, audio-gateway, faster-whisper, edge-tts, systemd, cloudflare-tunnel]
    category: iot-appliance
---

# Nexo Dedicated Appliance Skill

Panduan operasional, deployment, dan integrasi resmi untuk **Nexo Dedicated Appliance** — asisten AI fisik otonom (monitor/smart screen + mikrofon/speaker) yang berjalan tanpa ketergantungan pada PC konvensional.

Skill ini dirancang agar setiap instance Hermes Agent baru di masa mendatang dapat langsung memahami arsitektur, melakukan instalasi baru (fresh deployment), mengoperasikan CLI session bridge, memverifikasi perangkat, serta menyelesaikan masalah (troubleshooting) secara mandiri.

---

## 1. Arsitektur Sistem

Nexo dibangun dengan filosofi rekayasa **"Berkarya dengan hati @ 2026"** oleh Yanto Hariyono (YH Dev). Arsitektur menerapkan pemisahan tanggung jawab (*Separation of Concerns*) yang tegas: klien fisik Android berfungsi sebagai **"Indra dan Wajah"**, sedangkan server mengelola **"Pikiran dan Suara"**.

### Diagram Topologi End-to-End

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   PHYSICAL CLIENT LAYER (Android Native)               │
│                                                                        │
│   [Hardware Mic Array]                               [Speaker/Monitor] │
│            │                                                 ▲         │
│            ▼                                                 │         │
│   [AudioRecord (16kHz)]                             [AudioTrack]       │
│            │                                                 ▲         │
│            ├──────► [Hardware AEC]                           │         │
│            │                                                 │         │
│            ▼                                                 │         │
│   [Energy-based VAD]                             [Mascot Visualizer]   │
│            │                                        (60 FPS OpenGL)    │
│            ▼                                                 ▲         │
│   ┌──────────────────────────────────────────────────────────┴─────┐   │
│   │        NexoWebSocketClient (OkHttp WSS, Keystore Auth)         │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ Full-Duplex WebSocket
                                   │ (Binary PCM16 + JSON Control)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   SERVER LAYER (FastAPI Async Gateway)                 │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │    WebSocket Gateway & SessionHub (/ws/appliance, :8765)       │   │
│   └──────┬───────────────────────┬───────────────────────────┬─────┘   │
│          │                       │                           │         │
│          ▼                       ▼                           ▼         │
│   [Inbound Audio Queue]   [Session Event Bus]      [Outbound TTS Queue]│
│          │                       │                           ▲         │
│          ▼                       │                           │         │
│   [STT Faster-Whisper]           │                 [Edge-TTS Engine]   │
│   (base model, ID lang)          │                 (id-ID-ArdiNeural)  │
│          │                       │                           ▲         │
│          ▼                       ▼                           │         │
│   [Transcribed Text] ────► [Hermes Agent Core] ──────────────┘         │
│                            - Subprocess CLI Bridge                     │
│                            - Persistent state.db                       │
│                            - Memory & Tool Invocations                 │
└────────────────────────────────────────────────────────────────────────┘
```

### Invarian Arsitektur Utama

1. **Decoupled Architecture**: Klien Android tidak menjalankan model AI atau inferensi lokal. Seluruh komputasi kognitif (Hermes), transkripsi suara (STT), dan sintesis ucapan (TTS) berada di server.
2. **Native over WebView**: Klien Android ditulis 100% menggunakan Native Kotlin (Min SDK 26, Target SDK 34). Menghindari WebView demi kestabilan 24/7 tanpa *memory leak*, kendali latensi audio rendah (low-latency PCM), dan pemanfaatan hardware DSP.
3. **Audio Ingestion & Hardware AEC**:
   - `AudioSource`: `MediaRecorder.AudioSource.VOICE_COMMUNICATION` memicu pemrosesan DSP perangkat keras pada chipset (Qualcomm/MediaTek/Exynos).
   - `AcousticEchoCanceler`: Mengikat `android.media.audiofx.AcousticEchoCanceler` ke `audioSessionId` dari `AudioRecord` untuk meniadakan suara speaker Nexo sendiri (*hands-free near monitor*).
   - Buffer Pool: Alokasi statis 3200-byte buffer (100 ms audio pada 16kHz 16-bit mono) untuk mencegah garbage collection (GC) pauses.
4. **Voice Activity Detection (VAD)**:
   - Berbasis energi suara (RMS Amplitude Threshold). Jika amplitudo berada di bawah ambang batas selama > 800 ms, transmisi dihentikan dan sinyal end-of-speech dikirimkan.
5. **Wajah Digital & Telemetri Reaktif**:
   - Visualizer maskot dirender pada 60 FPS menggunakan GPU Canvas / SurfaceView.
   - Server memancarkan frame telemetri audio 5-band setiap 40 ms selama TTS berbicara untuk menggerakkan ekspresi dan getaran visual maskot secara real-time.
   - State maskot: `IDLE`, `LISTENING`, `PROCESSING`, `SPEAKING`, `ERROR`, `DISCONNECTED`.
6. **Pipeline STT & TTS**:
   - **STT**: `Faster-Whisper` (CTranslate2) menggunakan model `base`, bahasa Indonesia (`id`), kuantisasi `int8` (CPU) atau `float16` (GPU).
   - **TTS**: `Edge-TTS` menggunakan suara alami pria Indonesia `id-ID-ArdiNeural`.
7. **Hermes Agent Bridge**:
   - Integrasi server ke Hermes melalui subprocess CLI berkecepatan tinggi:
     ```bash
     hermes chat -c appliance --create-if-missing -q "<transkripsi>" -Q
     ```
   - Membersihkan metadata header/footer CLI sebelum meneruskan respons teks ke engine TTS.
   - Mendukung penautan sesi (*session linking*) dinamis via `scripts/nexo`.

---

## 2. Panduan Instalasi Host Baru (Fresh Deployment)

Panduan langkah demi langkah untuk menyiapkan host Linux (VPS / Home Server / Edge Mini PC) agar siap menjalankan layanan Nexo secara otomatis.

### Langkah 1: Prasyarat Sistem

Pastikan paket sistem dasar dan Python 3.11+ telah terpasang:
```bash
sudo apt update && sudo apt install -y \
    python3 python3-venv python3-pip \
    ffmpeg curl git build-essential
```

Pastikan Hermes Agent CLI telah terpasang dan terkonfigurasi pada akun sistem:
```bash
# Verifikasi instalasi Hermes
command -v hermes || curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes --version
```

### Langkah 2: Setup Direktori dan Virtual Environment Python

Clone repositori dan pasang dependensi backend:
```bash
cd /data/home/Nexo  # Atau direktori instalasi target
python3 -m venv server/.venv
source server/.venv/bin/activate
pip install --upgrade pip
pip install -r server/requirements.txt
```

### Langkah 3: Pre-download & Cache Model STT dan TTS

Untuk mencegah timeout atau lag saat permintaan pertama, unduh model Faster-Whisper di awal:
```bash
python3 - << 'EOF'
from faster_whisper import WhisperModel
print("Mengunduh dan memverifikasi model Faster-Whisper (base)...")
model = WhisperModel("base", device="cpu", compute_type="int8")
print("Model Faster-Whisper siap digunakan!")
EOF
```

Uji konektivitas Edge-TTS:
```bash
python3 - << 'EOF'
import asyncio, edge_tts
async def test_tts():
    communicate = edge_tts.Communicate("Koneksi Nexo siap.", "id-ID-ArdiNeural")
    data = b"".join([chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"])
    print(f"Edge-TTS sukses! Dihasilkan {len(data)} byte audio.")
asyncio.run(test_tts())
EOF
```

### Langkah 4: Konfigurasi Server (`config.yaml`)

Buat berkas konfigurasi `server/config.yaml` dari template:
```bash
cp server/config.example.yaml server/config.yaml
```

Isi standar `server/config.yaml`:
```yaml
server:
  host: "127.0.0.1"
  port: 8765
  ws_path: "/ws/appliance"
  ping_interval: 15
  ping_timeout: 30
  device_registry_path: "/root/.config/nexo-auth/devices.json"
  max_sessions: 4
  hello_timeout_seconds: 5
  max_json_bytes: 4096

stt:
  provider: "faster-whisper"
  model: "base"
  language: "id"

tts:
  provider: "edge-tts"
  voice: "id-ID-ArdiNeural"

hermes:
  bridge_mode: "cli"
  hermes_binary: "/root/.local/bin/hermes"
  timeout_seconds: 60

telemetry:
  frequency_bands_count: 5
  emit_interval_ms: 40
```

Siapkan direktori autentikasi perangkat dengan izin ketat:
```bash
umask 077
mkdir -p /root/.config/nexo-auth
chmod 700 /root/.config/nexo-auth
```

### Langkah 5: Konfigurasi dan Aktivasi Systemd Service

Salin template unit systemd ke direktori systemd (pilih mode user atau system):

**Mode User Systemd (Direkomendasikan bila berjalan sebagai akun khusus):**
```bash
mkdir -p ~/.config/systemd/user
cp /data/home/Nexo/server/deploy/nexo-appliance.service ~/.config/systemd/user/nexo-appliance.service

# Sesuaikan WorkingDirectory dan path python bila berbeda
systemctl --user daemon-reload
systemctl --user enable --now nexo-appliance.service
systemctl --user status nexo-appliance.service
```

Untuk memastikan service tetap berjalan setelah logout:
```bash
loginctl enable-linger $USER
```

Periksa kesehatan service:
```bash
curl -fsS http://127.0.0.1:8765/health
# Output yang diharapkan: {"status": "ok", "appliance_connected": false, ...}
```

### Langkah 6: Konfigurasi Cloudflare Tunnel (Ekspos Publik TLS/WSS)

Gateway mengikat loopback `127.0.0.1:8765`. Untuk akses perangkat Android dari luar LAN atau via HTTPS/WSS aman:

1. Buat tunnel Cloudflare (`cloudflared`):
   ```bash
   cloudflared tunnel create nexo-tunnel
   ```
2. Konfigurasikan ingress pada `/etc/cloudflared/config.yml`:
   ```yaml
   tunnel: <TUNNEL_UUID>
   credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

   ingress:
     - hostname: nexo.domain-anda.id
       service: http://127.0.0.1:8765
       originRequest:
         noTLSVerify: false
     - service: http_status:404
   ```
3. Tambahkan DNS route:
   ```bash
   cloudflared tunnel route dns nexo-tunnel nexo.domain-anda.id
   ```
4. Jalankan service tunnel:
   ```bash
   sudo systemctl restart cloudflared
   ```
5. Endpoint WebSocket publik Anda sekarang adalah:
   `wss://nexo.domain-anda.id/ws/appliance`

### Langkah 7: Pembuatan Token Perangkat (Device Authentication)

Nexo menerapkan autentikasi Stage 1 berbasis token bearer 256-bit acak. Server hanya menyimpan hash SHA-256 di `devices.json`.

#### Cara A: Melalui CLI Offline Server
```bash
cd /data/home/Nexo/server
PYTHONPATH=src .venv/bin/python -m nexo_server.auth provision \
  --registry /root/.config/nexo-auth/devices.json \
  --device-id "<DEVICE_ID_DARI_LAYAR_ANDROID>" \
  --origin "wss://nexo.domain-anda.id/ws/appliance" \
  --output /root/.config/nexo-auth/device-delivery.json
```
Baca token mentah sekali pakai dari `device-delivery.json`:
```bash
cat /root/.config/nexo-auth/device-delivery.json
# Hapus file delivery setelah token disalin ke perangkat:
rm -f /root/.config/nexo-auth/device-delivery.json
```

#### Cara B: Melalui Perintah Slash Telegram Pemilik
Jika bot Telegram Hermes aktif, pemilik dapat mengirim perintah langsung di chat privat:
```text
/nexo_token <DEVICE_ID>
```
Bot akan mengirim token secara aman beserta tombol salin langsung.

#### Memasukkan Token pada Klien Android:
1. Pada aplikasi Nexo di Android, **tekan lama (long-press)** teks status koneksi/mikrofon.
2. Bottom sheet pengaturan autentikasi akan terbuka.
3. Masukkan token bearer ke dalam input bertopeng.
4. Verifikasi bahwa WSS Origin cocok dengan endpoint server.
5. Klik **Simpan**. Klien akan mengenkripsi token menggunakan Android Keystore (AES-GCM) dan melakukan koneksi ulang otomatis.

---

## 3. Alur Koneksi Session Terminal Lokal (`scripts/nexo`)

Utilitas `scripts/nexo` menghubungkan terminal kerja Hermes lokal Anda langsung ke Nexo Dedicated Appliance.

### Registrasi Executable ke PATH
```bash
ln -sf /data/home/Nexo/scripts/nexo /root/.local/bin/nexo
chmod +x /data/home/Nexo/scripts/nexo /data/home/Nexo/scripts/nexo_session.py
```

### Panduan Perintah Operasional

| Perintah | Deskripsi |
|---|---|
| `nexo status` | Menampilkan kesehatan server, status appliance terhubung, device ID, state maskot, dan thread Hermes tertaut. |
| `nexo send "Pesan"` | Mengirim instruksi teks ke Hermes; jawaban penalaran ditampilkan di terminal. |
| `nexo send "Pesan" -s` | Mengirim instruksi dan **menyuarakan jawaban** langsung di speaker appliance. |
| `nexo watch` | Menyimak event stream kognitif real-time (transkripsi STT pengguna, proses *thinking*, dan teks respons). |
| `nexo watch --telemetry` | Menyimak event stream lengkap dengan visualisasi amplitudo & frekuensi audio 5-band. |
| `nexo transcript -n 15` | Membaca riwayat transkrip percakapan sesi (default 20, format rapi atau `--json`). |
| `nexo link --latest` | **Menautkan sesi appliance ke sesi terminal Hermes lokal terbaru**, menyatukan memori kerja. |
| `nexo link "nama-thread"` | Menautkan sesi appliance ke nama session thread tertentu secara manual. |
| `nexo speak "Teks" -e senang` | Memicu ucapan suara instan di speaker appliance dengan animasi ekspresi maskot tertentu. |
| `nexo bridge` | Membuka mode konsol interaktif 2 arah: ketik pesan sambil menyimak stream appliance live. |

### Contoh Alur Kerja Praktis Operator

1. **Sinkronisasi Konteks Terminal ke Ruang Fisik**:
   Saat Anda sedang bekerja di terminal pada thread riset/coding tertentu:
   ```bash
   nexo link --latest
   ```
   Setelah itu, saat Anda berbicara langsung pada layar pintar Nexo di meja kerja, Nexo sudah memiliki memori lengkap dari apa yang baru saja Anda kerjakan di terminal.

2. **Notifikasi Suara CI/CD / Tugas Selesai**:
   Tambahkan ke akhir script kompilasi atau task panjang:
   ```bash
   ./build.sh && nexo speak "Kompilasi selesai tanpa error. Sistem siap diuji." -e senang
   ```

3. **Pemantauan Pasif (Background Watcher)**:
   Di panel tmux terpisah:
   ```bash
   nexo watch
   ```

---

## 4. Pemecahan Masalah (Troubleshooting Matrix)

Gunakan matriks ini saat menemukan anomali operasional:

| Gejala Masalah | Kemungkinan Akar Masalah | Solusi / Tindakan Perbaikan |
|---|---|---|
| **Health OK, tapi koneksi WebSocket ditolak (401/403/1008)** | Token tidak cocok, device ID belum didaftarkan, atau origin WSS tidak sesuai. | 1. Buka bottom sheet di Android (tekan lama status), catat Device ID dan WSS Origin.<br>2. Periksa `/root/.config/nexo-auth/devices.json`.<br>3. Terbitkan ulang token via CLI `nexo_server.auth provision` atau `/nexo_token <ID>`. |
| **Hermes Bridge Error: Command Not Found** | Binary `hermes` tidak berada di PATH systemd service. | Periksa `Environment=PATH=...` di `nexo-appliance.service`. Pastikan `hermes_binary` di `server/config.yaml` menunjuk path absolut (misal `/root/.local/bin/hermes`). |
| **STT lambat atau hening tanpa transkripsi** | Unduhan awal model Faster-Whisper tertunda, atau microphone gain terlalu rendah. | 1. Jalankan pre-cache model Faster-Whisper.<br>2. Pastikan izin RECORD_AUDIO aktif di Android.<br>3. Periksa VAD threshold di log server.<br>4. Uji STT lokal: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_stt.py`. |
| **Gema Suara / Audio Feedback Loop** | Hardware AEC tidak aktif pada chipset Android. | 1. Pastikan audio source menggunakan `VOICE_COMMUNICATION`.<br>2. Jauhkan jarak speaker eksternal dari mikrofon jika monitor bergetar kuat.<br>3. Pastikan volume speaker tidak mengalami distorsi/clipping fisik. |
| **Edge-TTS Gagal Menghasilkan Audio** | Koneksi internet ke server Microsoft Edge TTS terhambat atau timeout DNS. | 1. Pastikan host server memiliki koneksi internet keluar (port 443).<br>2. Uji CLI: `edge-playback --text "Tes audio"`.<br>3. Jika terblokir, gunakan provider fallback (`elevenlabs` atau model lokal). |
| **Ekspresi Wajah Maskot Tidak Berubah** | Frasa perintah suara tidak cocok dengan kamus intent maskot. | Nexo mendukung frasa suara terarah: *"Nexo tersenyum"*, *"coba tertawa"*, *"ekspresi jahil"*, *"kembali normal"*. Periksa transkripsi STT yang diterima server melalui `nexo watch`. |
| **Cloudflare Tunnel Putus-Putus / 502 Bad Gateway** | Service `nexo-appliance` mati, port 8765 tidak listening, atau WebSocket upgrade belum aktif. | 1. Cek `systemctl --user status nexo-appliance`.<br>2. Verifikasi socket: `ss -tulpn \| grep 8765`.<br>3. Cek log tunnel: `journalctl -u cloudflared -n 50 --no-pager`. |

---

## 5. Checklist Verifikasi Perangkat & Operasional

Lakukan verifikasi bertahap berikut setelah deployment baru atau pembaruan kode:

### Checklist Host & Service Backend
- [ ] Python venv aktif dan dependensi terpasang (`server/.venv`).
- [ ] Model Faster-Whisper `base` terunduh dan ter-cache di local filesystem.
- [ ] Konfigurasi `server/config.yaml` valid dan `device_registry_path` menunjuk berkas mode 0600.
- [ ] Service systemd aktif (`systemctl --user is-active nexo-appliance` mengembalikan `active`).
- [ ] Endpoint health merespons HTTP 200: `curl -fsS http://127.0.0.1:8765/health`.
- [ ] Uji unit backend lulus 100%: `pytest tests -q`.

### Checklist Autentikasi & Keamanan
- [ ] Direktori `~/.config/nexo-auth` berizin `0700`, berkas `devices.json` berizin `0600`.
- [ ] Tidak ada token mentah yang tersimpan di dalam git repositori atau konfigurasi publik.
- [ ] Token perangkat terverifikasi lewat SHA-256 hash matching di registry.
- [ ] Plugin Telegram `/nexo-token` terdaftar dan merespons hanya pada chat privat pemilik.

### Checklist Klien Android Native
- [ ] APK terpasang pada layar fisik (Min SDK 26, Target SDK 34).
- [ ] Izin mikrofon (`android.permission.RECORD_AUDIO`) diberikan.
- [ ] Origin WSS cocok dengan domain Cloudflare / LAN server.
- [ ] Token bearer tersimpan aman di Android Keystore.
- [ ] Visualizer 60 FPS aktif dan menampilkan animasi idle (bernapas/pendaran cyan lembut).

### Checklist Integrasi End-to-End
- [ ] Handshake WebSocket berhasil: status Android berubah menjadi hijau (`LISTENING`).
- [ ] Ucapkan salam ke monitor: status beralih ke `PROCESSING` lalu `SPEAKING`.
- [ ] Suara TTS keluar jernih dari speaker tanpa memicu loop suara ke mikrofon (AEC bekerja).
- [ ] Maskot bereaksi terhadap perintah ekspresi suara (*"Nexo tersenyum"*).
- [ ] Terminal CLI `nexo status` menampilkan appliance terhubung dan `nexo watch` menangkap stream transkrip secara live.

---

## 6. Berkas Pendukung & Otomasi (Scripts & References)

Paket skill ini dilengkapi berkas otomasi dan referensi teknis yang dapat langsung dieksekusi atau dipelajari:

- **`scripts/setup_host.sh`**: Skrip deployment otomatis satu perintah untuk menyiapkan host Linux baru (membuat venv, memasang requirements, mengunduh model Faster-Whisper, memverifikasi Edge-TTS, mengonfigurasi systemd, dan menautkan CLI `nexo`).
  ```bash
  /data/home/Nexo/skills/nexo/scripts/setup_host.sh
  ```
- **`scripts/verify_appliance.sh`**: Skrip diagnostik otomatis untuk memvalidasi kesehatan sistem (ketersediaan service, izin file autentikasi, port listening, health endpoint, status CLI, dan kelulusan test suite).
  ```bash
  /data/home/Nexo/skills/nexo/scripts/verify_appliance.sh
  ```
- **`references/architecture.md`**: Spesifikasi mendalam model buffer audio 16kHz PCM16, rincian state machine maskot 60 FPS, dan struktur pesan frame biner/JSON WebSocket `/ws/appliance`.
- **`references/troubleshooting.md`**: Buku panduan (runbook) langkah-demi-langkah pemecahan masalah dan kode error spesifik (HTTP 401/403/1008, DNS timeout, VAD tuning, dan recovery registry).

