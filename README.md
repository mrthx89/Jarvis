# Jarvis Dedicated Appliance

> **"Berkarya dengan hati @ 2026"** — Menggabungkan stabilitas teknis tingkat tinggi, ketajaman logika rekayasa, dan nilai guna humanis.

Jarvis Dedicated Appliance adalah sistem asisten kecerdasan buatan ambient yang mengubah layar monitor dan perangkat audio (mikrofon + speaker) menjadi entitas meja mandiri. Berkomunikasi secara verbal tanpa pengetikan, tanpa mouse, dan tanpa memerlukan Personal Computer (PC) konvensional di meja Anda.

---

## 1. Konsep & Arsitektur Utama

Sistem memisahkan peran fisik dan kecerdasan secara tegas (Decoupled Client-Server Architecture):

- **Klien (Android Native Kiosk)**: Berjalan pada Tablet Android atau Smart Monitor. Berfungsi murni sebagai **"Indra dan Wajah"** (menangkap audio dengan Acoustic Echo Cancellation, memutar respons suara, dan menampilkan visualizer audio-reaktif berkecepatan 60 FPS).
- **Server (Hermes Agent & Audio Gateway)**: Berjalan di server/cloud. Berfungsi sebagai **"Otak dan Suara"** (Speech-to-Text streaming, penalaran otonom agen cerdas, dan sintesis Text-to-Speech).

```
                      +-----------------------------+
                      |   Jarvis Client (Android)   |
                      |   - Native Kotlin           |
                      |   - Low-latency Audio (PCM) |
                      |   - Hardware Echo Canceler  |
                      |   - Audio-Reactive Orb UI   |
                      +--------------+--------------+
                                     ^
                                     | Full-Duplex WebSocket
                                     v
                      +--------------+--------------+
                      |   Jarvis Server (Python)    |
                      |   - FastAPI WebSocket Gateway|
                      |   - STT Engine (Whisper)    |
                      |   - Hermes Agent Core       |
                      |   - Neural TTS Engine       |
                      +-----------------------------+
```

---

## 2. Struktur Repositori

```
Jarvis/
├── docs/                      # Dokumentasi komprehensif & spesifikasi
│   ├── PRD.md                 # Product Requirements Document
│   ├── ARCHITECTURE.md        # Arsitektur sistem & rekayasa audio
│   ├── PROTOCOL.md            # Spesifikasi protokol WebSocket & pesan
│   └── HARDWARE_SETUP.md      # Panduan instalasi perangkat keras & kiosk 24/7
│
├── android/                   # Klien Native Android Kiosk (Kotlin)
│   ├── app/
│   │   ├── src/main/java/id/my/yhdev/jarvis/
│   │   │   ├── audio/         # AudioRecord, AudioPlayer, Hardware AEC
│   │   │   ├── network/       # WebSocket Client & Protocol Parser
│   │   │   ├── service/       # Foreground Kiosk Service
│   │   │   └── ui/            # Audioreactive Orb Visualizer & Activity
│   │   └── AndroidManifest.xml
│   ├── build.gradle.kts
│   └── settings.gradle.kts
│
├── server/                    # Gateway Audio & Jembatan Hermes (Python)
│   ├── src/jarvis_server/
│   │   ├── gateway/           # Handler WebSocket & connection manager
│   │   ├── pipeline/          # Modul STT, TTS, dan VAD
│   │   ├── hermes/            # Jembatan ke runtime Hermes Agent
│   │   └── models/            # Skema pesan dan protokol
│   ├── config.example.yaml
│   ├── pyproject.toml
│   └── requirements.txt
│
├── AGENTS.md                  # Panduan agen AI pengembang & aturan proyek
├── LICENSE                    # Lisensi MIT (Yanto Hariyono / YH Dev)
└── README.md                  # Dokumentasi utama proyek
```

---

## 3. Dokumentasi Resmi

- [Product Requirements Document (PRD)](docs/PRD.md)
- [System Architecture & Audio Engineering](docs/ARCHITECTURE.md)
- [WebSocket Communication Protocol](docs/PROTOCOL.md)
- [Hardware & Kiosk Deployment Guide](docs/HARDWARE_SETUP.md)
- [Firebase Remote Config Dynamic Server URL Guide](docs/REMOTE_CONFIG_SETUP.md)

---

## 4. Memulai Pengembangan

### Persyaratan Awal:
1. **Android Studio**: Ladybug / Hedgehog atau yang lebih baru dengan Android SDK 34 (Java 17+).
2. **Python**: Versi 3.10 atau lebih baru untuk menjalankan server gateway.
3. **Hermes Agent Runtime**: Terpasang di lingkungan server.

### Menjalankan Server Gateway:
```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python -m jarvis_server.main
```

### Menjalankan Klien Android:
1. Buka folder `android/` di Android Studio.
2. Sinkronkan Gradle.
3. Hubungkan perangkat Android (Tablet atau Smart Monitor) via ADB.
4. Sesuaikan alamat IP server pada `res/values/strings.xml` atau konfigurasi aplikasi.
5. Jalankan aplikasi ke perangkat.

---

## 5. Lisensi & Hak Cipta
Dikelola oleh **Yanto Hariyono (YH Dev)** di bawah lisensi MIT. Lihat file [LICENSE](LICENSE) untuk informasi lebih lanjut.
