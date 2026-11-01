---
name: nexo
description: Use when operating Nexo. Manage and deploy smart appliance.
version: 1.1.0
author: Yanto Hariyono (YH Dev), mrthx89
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [nexo, appliance, smart-screen, voice-assistant, audio-gateway, faster-whisper, edge-tts, sentence-streaming, earcons, ambient-mode, systemd, cloudflare-tunnel]
    category: iot-appliance
---

# Nexo Dedicated Appliance Skill

Panduan operasional, deployment, arsitektur, dan integrasi resmi untuk **Nexo Dedicated Appliance** — asisten AI fisik otonom (monitor/smart screen + mikrofon/speaker) yang berjalan tanpa ketergantungan pada PC konvensional di meja kerja.

Skill ini dirancang agar setiap instance Hermes Agent baru di masa mendatang dapat langsung memahami arsitektur, mengoperasikan alur sentence-chunked streaming & penanganan latensi, memanfaatkan fitur smart screen kios 24/7 (ambient sleep mode, pixel shifting, earcons), melakukan deployment host baru, menautkan sesi terminal lokal, menjalankan verifikasi, serta mengatasi masalah (*troubleshooting*) secara mandiri.

---

## 1. Arsitektur Sistem & Invarian Utama

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
│            │                                      [EarconManager]      │
│            ▼                                      - Listening Chime    │
│   [Energy-based VAD]                              - Thinking Chime     │
│   - RMS Threshold                                            ▲         │
│   - Pre-roll Buffer                                          │         │
│            │                                        [Mascot Visualizer]│
│            ▼                                        - 60 FPS Canvas    │
│   ┌──────────────────────────────────────────────┐  - Pixel Shifting   │
│   │ NexoWebSocketClient (OkHttp, Keystore Auth)  │  - Ambient Sleep    │
│   └──────────────────────┬───────────────────────┴───────────▲─────────┘
└──────────────────────────┼───────────────────────────────────┼──────────┘
                           │ Full-Duplex WebSocket             │
                           │ - Inbound: Binary PCM16           │
                           │ - Outbound: Binary TTS MP3 frames │
                           │ - Bi-directional: JSON Control &  │
                           │   Cognitive Streaming Frames      │
                           ▼                                   │
┌──────────────────────────────────────────────────────────────┼──────────┐
│                   SERVER LAYER (FastAPI Async Gateway)       │          │
│                                                              │          │
│   ┌──────────────────────────────────────────────────────────┴─────┐    │
│   │    WebSocket Gateway & SessionHub (/ws/appliance, :8765)       │    │
│   └──────┬───────────────────────┬───────────────────────────┬─────┘    │
│          │                       │                           │          │
│          ▼                       ▼                           ▼          │
│   [Inbound Audio Queue]   [Session Event Bus]      [Outbound TTS Queue] │
│          │                       │                           ▲          │
│          ▼                       │                           │          │
│   [STT Faster-Whisper]           │                 [Pipelined Edge-TTS] │
│   (base model, ID lang)          │                 (id-ID-ArdiNeural)   │
│          │                       │                           ▲          │
│          ▼                       ▼                           │          │
│   [Transcribed Text] ────► [Hermes Core Bridge] ─────────────┘          │
│                            - Subprocess CLI Bridge                      │
│                            - SentenceChunker (Low TTFA)                 │
│                            - Emotion Tag Stripping (`[expr:...]`)       │
│                            - MP3 Frame Parser & Echo Guard (600ms)      │
│                            - SessionHub Locking (state.db)              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Invarian Arsitektur Utama

1. **Decoupled Architecture**: Klien Android tidak menjalankan model AI atau inferensi lokal. Seluruh komputasi kognitif (Hermes), transkripsi suara (STT), dan sintesis ucapan (TTS) berada di server Linux.
2. **Native over WebView**: Klien Android ditulis 100% menggunakan Native Kotlin (Min SDK 26, Target SDK 34). Menghindari WebView demi kestabilan 24/7 tanpa *memory leak*, kendali latensi audio rendah (low-latency PCM), dan pemanfaatan hardware DSP.
3. **Audio Ingestion & Hardware AEC**:
   - `AudioSource`: `MediaRecorder.AudioSource.VOICE_COMMUNICATION` memicu pemrosesan DSP perangkat keras pada chipset (Qualcomm/MediaTek/Exynos).
   - `AcousticEchoCanceler`: Mengikat `android.media.audiofx.AcousticEchoCanceler` ke `audioSessionId` dari `AudioRecord` untuk meniadakan suara speaker Nexo sendiri (*hands-free near monitor*).
   - Buffer Pool: Alokasi statis 3200-byte buffer (100 ms audio pada 16kHz 16-bit mono) untuk mencegah garbage collection (GC) pauses.
4. **Pre-roll Circular Buffer & VAD Histeresis**:
   - `AudioBuffer` berkapasitas 5 frame (500 ms) menampung audio secara kontinu sehingga konsonan pembuka kata tidak terpotong.
   - Dual-threshold histeresis (-35 dBFS ambang awal, -43 dBFS ambang kelanjutan) dengan jeda hening 1.5 detik sebelum finalisasi ucapan.
5. **Wajah Digital & Telemetri Reaktif 60 FPS**:
   - Visualizer maskot dirender pada 60 FPS menggunakan GPU Canvas / SurfaceView (`Choreographer.FrameCallback`).
   - Server memancarkan frame telemetri audio 5-band setiap 40 ms selama TTS berbicara untuk menggerakkan ekspresi dan getaran visual maskot secara real-time.
   - 16 State ekspresi maskot plus status khusus `SLEEPING` untuk mode malam/ambien.
6. **Pipeline STT & TTS**:
   - **STT**: `Faster-Whisper` (CTranslate2) menggunakan model `base`, bahasa Indonesia (`id`), kuantisasi `int8` (CPU) atau `float16` (GPU).
   - **TTS**: `Edge-TTS` menggunakan suara alami pria Indonesia `id-ID-ArdiNeural` dengan streaming per potongan kalimat.
7. **Hermes Agent Bridge & Canonical Session Locking**:
   - Integrasi server ke Hermes melalui subprocess CLI berkecepatan tinggi:
     ```bash
     hermes chat -c appliance --create-if-missing -q "<transkripsi>" -Q
     ```
   - Sesi kanonis `nexo` diikat ke `~/.hermes/state.db` dengan penguncian dua arah (*canonical session locking*) untuk mencegah percabangan konteks (*context drift*).

---

## 2. Pipelined Sentence-Chunked Streaming & Penanganan Latensi

Untuk memberikan pengalaman percakapan yang alami dan responsif, Nexo menerapkan arsitektur streaming berjenjang (*pipelined streaming*) yang memangkas waktu tunggu dari respons penuh menjadi per kalimat.

### 2.1 Konsep & Pengurangan Time to First Audio (TTFA)

Pada asisten suara konvensional non-streaming, pengguna harus menunggu seluruh respons LLM selesai diformulasikan sebelum sintesis suara (TTS) dimulai. Pada model penalaran modern (*reasoning models*) yang menghasilkan *thinking tokens*, penundaan ini dapat mencapai 3 hingga 8 detik.

Nexo memangkas **Time to First Audio (TTFA)** menjadi **< 800 ms**:
1. Server mengalirkan token jawaban dari Hermes Core secara real-time.
2. `SentenceChunker` mendeteksi akhir kalimat pertama.
3. Kalimat pertama langsung dilempar ke worker Edge-TTS (`synthesize_stream`) dan frame audio biner pertama langsung dikirimkan ke Android.
4. Klien Android mulai memutar audio kalimat pertama di speaker sementara server masih menerima dan memproses kalimat-kalimat berikutnya dari model LLM.

### 2.2 Algoritma SentenceChunker

Komponen `SentenceChunker` bertugas memecah aliran teks mentah menjadi potongan kalimat gramatikal tanpa memotong kalimat prematur:
- **Pemisah Kalimat Utama**: Tanda baca akhir (titik `.`, seru `! `, tanya `?`, atau jeda baris baru ganda `\n\n`) yang diikuti oleh spasi atau batas kata.
- **Pengecualian Cerdas**:
  - Gelar dan singkatan umum (misal: `Dr.`, `Prof.`, `Ir.`, `PT.`, `CV.`, `dkk.`, `dll.`, `dsb.`, `i.e.`, `e.g.`).
  - Nilai numerik dan desimal (misal: `Rp 50.000`, `3.14`, `v1.2.0`).
  - Tanda kutip langsung dan tanda kurung (memastikan klausa penutup tetap berada di dalam kalimat yang sama).
- **Buffer Flush**: Saat transmisi LLM selesai (*end of stream*), sisa token di buffer langsung diflush sebagai kalimat terakhir meskipun tidak diakhiri tanda baca formal.

### 2.3 Protokol Frame Kognitif (`cognitive`)

Server menyiarkan event stream kognitif real-time melalui WebSocket JSON frame dengan tipe `cognitive`:

```json
{
  "type": "cognitive",
  "stage": "streaming",
  "sentence": "Halo, saya siap membantu pekerjaan Anda hari ini.",
  "content": "Halo, saya siap membantu pekerjaan Anda hari ini.",
  "index": 0,
  "expression": "senang"
}
```

Field spesifikasi:
- `stage`: `"streaming"` untuk kalimat audio yang sedang dikirim ke TTS, atau `"thought"` untuk stream penalaran internal Hermes.
- `sentence` / `content`: Teks kalimat bersih (tanpa markup tag).
- `index`: Nomor indeks urutan kalimat dalam jawaban (0-indexed) untuk sinkronisasi teks subtitle di Android atau terminal watcher.
- `expression`: Ekspresi visual maskot yang terasosiasi dengan kalimat tersebut.

### 2.4 Parsing dan Penghapusan Otomatis Tag Emosi (`[expr:...]`)

Hermes Agent dapat mengekspresikan emosi dengan menyisipkan tag terstruktur pada awal kalimat, misalnya:
`[expr:senang] Selamat pagi! Senang sekali bisa membantu Anda.`
`[expr:bingung] Maaf, saya belum mengerti maksud perintah tersebut.`

Komponen `parse_and_strip_emotional_tags` melakukan dua tindakan krusial:
1. **Mapping Ekspresi**: Mengekstrak tag ekspresi pertama, memetakan alias bahasa Indonesia (`bahagia` → `senang`, `ngantuk` → `mengantuk`, `tidur` → `sleeping`), lalu mengirim frame `state_change` dan `cognitive` dengan field `expression`.
2. **Pembersihan Bersih (Stripping)**: Menghapus seluruh pola `[expr:...]` dari teks sebelum diteruskan ke antrian sintesis Edge-TTS dan transkrip layar. Ini menjamin mesin suara TTS **tidak pernah melafalkan tag teknis secara verbal**.

### 2.5 Penghitungan Durasi Audio MP3 & Echo Guard (`mp3_frame.py`)

Kombinasi streaming kalimat dan mikrofon aktif membutuhkan proteksi gema tingkat tinggi (*half-duplex safety*):
1. **Parser Header MP3**: Modul `parse_audio_duration` membaca frame header MPEG audio Layer 3 (bitrate, sampling rate, padding) pada potongan audio biner yang dihasilkan oleh Edge-TTS.
2. **Akumulasi `_playback_until`**: Server menghitung durasi eksak audio yang telah dikirim ke klien dan mengakumulasikan batas waktu pemutaran (`_playback_until`).
3. **Echo Guard 600 ms**: Server menahan perubahan status sistem kembali ke `IDLE` dan menolak aktivasi input mikrofon baru hingga waktu pemutaran speaker berakhir ditambah jeda pengaman 600 ms (*echo guard*). Ini mencegah suara speaker ditangkap kembali oleh mikrofon saat buffer speaker baru saja selesai mengalir.
4. **Android Audio Drain Guard**: Klien Android memastikan hardware buffer `AudioTrack` benar-benar kosong (*drained*) sebelum mengizinkan status visual berpindah dari `SPEAKING` ke status berikutnya.

---

## 3. Fitur Smart Screen Kios 24/7: Ambient Sleep Mode, Pixel Shifting, & Earcons

Sebagai perangkat fisik meja (*desktop companion*) yang beroperasi tanpa henti (24 jam sehari, 7 hari seminggu), Nexo dilengkapi fitur pendukung interaksi fisik dan perlindungan perangkat keras layar.

### 3.1 Sinyal Audio Lokal Instan (EarconManager)

Untuk menghilangkan persepsi keterlambatan (*perceived latency*) akibat transmisi jaringan dan inisiasi model STT, klien Android menjalankan `EarconManager` yang memutar sinyal audio lokal secara instan:

1. **Listening Chime (`earcon_listening.wav` / `chime_listening.wav`)**:
   - Dua nada berpadu naik (*rising chime*): E5 (659.25 Hz, 100 ms) berlanjut ke B5 (987.77 Hz, 220 ms dengan peluruhan bell). Total durasi 320 ms.
   - Diputar seketika tombol ditekan atau deteksi VAD mengenali suara pengguna, memberi konfirmasi audio langsung bahwa Nexo siap mendengarkan.
2. **Thinking Chime (`earcon_thinking.wav` / `chime_thinking.wav`)**:
   - Nada harmoni resolusi lembut (*subtle harmonic resolution*): A5 (880 Hz) → D6 (1174.66 Hz), total durasi 180 ms.
   - Diputar seketika pengguna selesai berbicara (VAD speech end), menandai transisi ke proses transkripsi dan penalaran kognitif.

**Karakteristik Teknis Audio Earcon**:
- Dihasilkan dengan presisi matematis via `scripts/generate_earcons.py` (16-bit PCM WAV, 44.1 kHz mono, kurva attack 8 ms & exponential decay).
- Dikelola melalui `SoundPool` / audio track berlatensi rendah independen di Android, sehingga tidak menginterupsi atau memutus audio playback utama dari TTS (`AudioTrack`).

### 3.2 Mode Tidur Ambien (Ambient Sleep Mode)

Pengoperasian 24/7 di ruang kerja atau kamar membutuhkan efisiensi visual saat tidak ada orang yang berinteraksi:
- **Transisi Otomatis**: Jika tidak ada aktivitas suara atau sentuhan selama periode inaktivitas terkonfigurasi (default 5 menit), sistem bertransisi ke status `SLEEPING`.
- **Wajah Maskot Tidur**: Ekspresi maskot beralih ke `sleeping` (`mascot_sleeping.xml`) — mata terpejam tenang, denyut partikel visualizer melambat drastis menjadi pendaran malam redup (*dim night glow*).
- **Zero Cold-Start Wake-Up**: Saat pengguna menyentuh layar atau mulai berbicara, perangkat langsung kembali ke status aktif (`IDLE` atau `LISTENING`) seketika tanpa perlu proses boot atau reconnect ulang.

### 3.3 Pencegahan Burn-In Layar (Anti Burn-In Pixel Shifting)

Panel layar OLED atau AMOLED pada tablet yang menyala 24/7 rentan mengalami degradasi piksel permanen (*screen burn-in*) bila menampilkan aset visual pada koordinat statis:
- **Mekanisme Pixel Shifting**: Kanvas visualizer Android menerapkan pergeseran koordinat halus (*subtle coordinate drift*) sebesar ±2 hingga ±6 piksel secara periodik (setiap 60 detik) mengikuti lintasan elips lembut.
- **Kenyamanan Visual**: Pergeseran piksel berada di bawah batas persepsi kasat mata pengguna (*visually imperceptible*), menjaga tampilan tetap tajam dan estetis tanpa degradasi visual, sekaligus menyebarkan beban luminansi ke seluruh sub-piksel layar secara merata demi memperpanjang umur panel display.

---

## 4. Panduan Instalasi Host Baru (Fresh Deployment)

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
command -v hermes || (curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/install-hermes.sh && bash /tmp/install-hermes.sh && rm -f /tmp/install-hermes.sh)
hermes --version
```

### Langkah 2: Setup Direktori dan Virtual Environment Python

Clone repositori dan pasang dependensi backend:
```bash
cd /data/home/Jarvis  # Atau direktori instalasi target
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

Salin template unit systemd ke direktori systemd mode user:
```bash
mkdir -p ~/.config/systemd/user
cp /data/home/Jarvis/server/deploy/nexo-appliance.service ~/.config/systemd/user/nexo-appliance.service

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
cd /data/home/Jarvis/server
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

## 5. Alur Koneksi Session Terminal Lokal (`scripts/nexo`)

Utilitas `scripts/nexo` menghubungkan terminal kerja Hermes lokal Anda langsung ke Nexo Dedicated Appliance.

### Registrasi Executable ke PATH
```bash
ln -sf /data/home/Jarvis/scripts/nexo /root/.local/bin/nexo
chmod +x /data/home/Jarvis/scripts/nexo /data/home/Jarvis/scripts/nexo_session.py
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

## 6. Pemecahan Masalah (Troubleshooting Matrix Pemutakhiran Komprehensif)

Gunakan matriks diagnostik komprehensif ini saat menemukan anomali operasional:

| Gejala Masalah | Kemungkinan Akar Masalah | Solusi / Tindakan Perbaikan |
|---|---|---|
| **Health OK, tapi koneksi WebSocket ditolak (401/403/1008)** | Token tidak cocok, device ID belum didaftarkan, origin WSS tidak sesuai, atau izin registry tidak aman. | 1. Buka bottom sheet di Android (tekan lama status), catat Device ID dan WSS Origin.<br>2. Periksa izin: `chmod 700 ~/.config/nexo-auth && chmod 600 ~/.config/nexo-auth/devices.json`.<br>3. Terbitkan ulang token via CLI `nexo_server.auth provision` atau `/nexo_token <ID>`. |
| **Sentence-Chunked Streaming: Jeda Antar Kalimat Terlalu Lama** | Kecepatan generasi model LLM lambat, atau batas `SentenceChunker` tertahan oleh kalimat panjang tanpa tanda baca. | 1. Pantau stream di terminal via `nexo watch`.<br>2. Pastikan model LLM menggunakan mode streaming (`query_stream`).<br>3. Atur parameter pemecah kalimat di server agar memotong pada koma/klausul jika kalimat melebihi 120 karakter. |
| **Tag Emosi `[expr:...]` Terbaca oleh Suara TTS** | Pipeline `parse_and_strip_emotional_tags` terlewati sebelum teks masuk ke antrian Edge-TTS. | 1. Periksa unit test `tests/test_expression_parser.py`.<br>2. Pastikan sanitasi teks diterapkan pada `synthesize_stream` sebelum teks diteruskan ke `edge_tts.Communicate`. |
| **Ekspresi Wajah Maskot Tidak Berubah** | Tag emosi menggunakan nama yang belum dipetakan, atau event `state_change` / `cognitive` tidak diterima klien. | Nexo mendukung nama emosi kanonis: `netral`, `senang`, `sangat_senang`, `tertawa`, `bingung`, `mengantuk`, `mengedip`, `terkejut`, `cinta`, `jahil`, `keren`, `malu`, `marah`, `menangis`, `sedih`, `takut`, dan `sleeping`. Periksa payload `cognitive` melalui `nexo watch`. |
| **Sinyal Audio Lokal (Earcons) Tidak Berbunyi** | Berkas audio `earcon_listening.wav` atau `earcon_thinking.wav` hilang di `res/raw/`, atau volume notifikasi Android dibungkam. | 1. Jalankan `python3 scripts/generate_earcons.py` untuk meregenerasi aset WAV.<br>2. Pastikan `EarconManager` memuat audio ke `SoundPool`.<br>3. Periksa pengaturan volume media dan izin audio di Android. |
| **Earcons Memotong atau Bertabrakan dengan Suara TTS** | Audio focus tumpang tindih antara `SoundPool` earcon dan `AudioTrack` TTS playback. | Pisahkan audio usage attribute: earcon menggunakan `USAGE_ASSISTANCE_SONIFICATION`, sedangkan TTS menggunakan `USAGE_MEDIA` dengan stream independen. |
| **Gema Suara / Audio Feedback Loop Sesaat Setelah TTS Berbicara** | Waktu *echo guard* terlalu singkat (< 600 ms) atau Hardware AEC pada chipset Android tidak aktif. | 1. Pastikan server menghitung durasi MP3 via `parse_audio_duration` dan menambahkan buffer pengaman minimal 600 ms sebelum kembali ke `IDLE`.<br>2. Verifikasi Android menggunakan `MediaRecorder.AudioSource.VOICE_COMMUNICATION`.<br>3. Turunkan volume speaker ke 60-70% untuk mencegah distorsi fisik membran mic. |
| **Ambient Sleep Mode Tidak Aktif atau Layar Android Mati Total (Sleep/Doze)** | Inactivity timer tidak terpicu, atau flag `FLAG_KEEP_SCREEN_ON` tidak diaktifkan pada Activity Android. | 1. Pastikan `getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)` aktif pada `MainActivity`.<br>2. Periksa inaktivitas listener di Android: timer 5 menit harus mereset setiap ada sentuhan layar atau event audio.<br>3. Pastikan mode hemat daya agresif (*Doze mode*) dinonaktifkan untuk aplikasi Nexo. |
| **Tampilan Maskot Bergeser Jauh atau Jitter (Pixel Shifting Anomali)** | Offset pixel shifting terakumulasi tanpa batasan bound rentang koordinat. | Batasi pergeseran maksimal koordinat gambar pada rentang aman ±2 hingga ±6 piksel dari titik pusat jangkar (*anchor point*). Gunakan interpolasi sinusoidal halus. |
| **Hermes Bridge Error: Command Not Found / Timeout** | Binary `hermes` tidak berada di PATH systemd service, atau state.db terkunci. | 1. Periksa `Environment=PATH=...` di `nexo-appliance.service`. Pastikan `hermes_binary` di `server/config.yaml` menunjuk path absolut (misal `/root/.local/bin/hermes`).<br>2. Uji langsung: `hermes chat -c appliance -q "tes" -Q`. |
| **STT Lambat atau Hening Tanpa Transkripsi** | Unduhan awal model Faster-Whisper tertunda, atau microphone gain terlalu rendah. | 1. Jalankan pre-cache model Faster-Whisper: `.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"`.<br>2. Pastikan izin `RECORD_AUDIO` aktif di Android.<br>3. Pantau VAD threshold di log server. |
| **Edge-TTS Gagal Menghasilkan Audio** | Koneksi internet ke server Microsoft Edge TTS terhambat atau timeout DNS. | 1. Pastikan host server memiliki koneksi keluar port 443.<br>2. Uji CLI: `edge-playback --text "Tes audio"`.<br>3. Jika terblokir oleh ISP, gunakan DNS 1.1.1.1 / 8.8.8.8. |
| **Cloudflare Tunnel Putus-Putus / 502 Bad Gateway** | Service `nexo-appliance` mati, port 8765 tidak listening, atau WebSocket upgrade belum aktif. | 1. Cek `systemctl --user status nexo-appliance`.<br>2. Verifikasi socket: `ss -tulpn \| grep 8765`.<br>3. Cek log tunnel: `journalctl -u cloudflared -n 50 --no-pager`. |

---

## 7. Checklist Verifikasi Perangkat & Operasional

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

### Checklist Klien Android Native & Fitur Kios 24/7
- [ ] APK terpasang pada layar fisik (Min SDK 26, Target SDK 34).
- [ ] ⚠️ Build APK DILARANG dilakukan di server (kompilasi hanya di mesin lokal Android Studio).
- [ ] Izin mikrofon (`android.permission.RECORD_AUDIO`) diberikan.
- [ ] Origin WSS cocok dengan domain Cloudflare / LAN server.
- [ ] Token bearer tersimpan aman di Android Keystore.
- [ ] Earcon chimes terdengar jernih (Listening chime saat mic aktif, Thinking chime saat user selesai).
- [ ] Visualizer 60 FPS aktif dan menampilkan animasi maskot reaktif.
- [ ] Ambient Sleep Mode aktif otomatis saat inaktivitas 5 menit dengan ekspresi `sleeping`.
- [ ] Pixel Shifting aktif perlahan untuk melindungi panel OLED/AMOLED dari burn-in.

### Checklist Integrasi End-to-End
- [ ] Handshake WebSocket berhasil: status Android berubah menjadi hijau (`LISTENING`).
- [ ] Ucapkan salam ke monitor: status beralih ke `PROCESSING` lalu `SPEAKING`.
- [ ] Pipelined sentence streaming bekerja: audio kalimat pertama diputar dalam < 800 ms (TTFA rendah).
- [ ] Tag emosi `[expr:...]` otomatis diubah menjadi ekspresi wajah dan tidak dilafalkan oleh TTS.
- [ ] Suara TTS keluar jernih dari speaker tanpa memicu loop suara ke mikrofon (AEC + echo guard 600 ms bekerja).
- [ ] Terminal CLI `nexo status` menampilkan appliance terhubung dan `nexo watch` menangkap stream transkrip & frame kognitif secara live.

---

## 8. Berkas Pendukung & Otomasi (Scripts & References)

Paket skill ini dilengkapi berkas otomasi dan referensi teknis yang dapat langsung dieksekusi atau dipelajari:

- **`scripts/setup_host.sh`**: Skrip deployment otomatis satu perintah untuk menyiapkan host Linux baru (membuat venv, memasang requirements, mengunduh model Faster-Whisper, memverifikasi Edge-TTS, mengonfigurasi systemd, dan menautkan CLI `nexo`).
  ```bash
  /data/home/Jarvis/skills/nexo/scripts/setup_host.sh
  ```
- **`scripts/verify_appliance.sh`**: Skrip diagnostik otomatis untuk memvalidasi kesehatan sistem (ketersediaan service, izin file autentikasi, port listening, health endpoint, status CLI, dan kelulusan test suite).
  ```bash
  /data/home/Jarvis/skills/nexo/scripts/verify_appliance.sh
  ```
- **`scripts/generate_earcons.py`**: Skrip generator audio PCM WAV untuk listening chime (E5 -> B5, 320 ms) dan thinking chime (A5 -> D6, 180 ms).
  ```bash
  python3 /data/home/Jarvis/scripts/generate_earcons.py
  ```
- **`references/architecture.md`**: Spesifikasi mendalam model buffer audio 16kHz PCM16, rincian state machine maskot 60 FPS, sentence-chunked streaming, MP3 frame duration parser, dan struktur frame biner/JSON WebSocket `/ws/appliance`.
- **`references/troubleshooting.md`**: Buku panduan (*runbook*) langkah demi langkah pemecahan masalah dan kode error spesifik (HTTP 401/403/1008, DNS timeout, VAD tuning, echo guard, dan recovery registry).
