# Product Requirements Document (PRD)
## Project: Jarvis Dedicated Appliance
**Architect & Owner**: Yanto Hariyono (YH Dev)  
**Version**: 1.0.0  
**Status**: Approved / In Development  
**Ethos**: "Berkarya dengan hati @ 2026"  

---

### 1. Executive Summary & Visi Produk
Jarvis Dedicated Appliance adalah sistem interaksi kecerdasan buatan ambient (hands-free physical appliance) yang menggabungkan layar monitor dan mikrofon/speaker array menjadi sebuah entitas cerdas di meja kerja tanpa memerlukan unit Personal Computer (PC) konvensional. 

Pengguna dapat berinteraksi secara verbal secara alami, melihat ekspresi/status visual Jarvis di layar ("wajah digital"), dan menerima respons audio-visual secara real-time tanpa menyentuh keyboard, mouse, ataupun layar sentuh.

---

### 2. Latar Belakang & Problem Statement
1. **Friksi Interaksi Keyboard/PC**: Interaksi agen cerdas saat ini umumnya terikat pada jendela browser atau terminal di PC, mengharuskan pengetikan teks dan memakan ruang kerja mental maupun fisik.
2. **Kerapuhan Solusi WebView**: Eksperimen Kiosk berbasis web wrapper/WebView kerap mengalami crash, pemborosan memori (Chromium memory leaks), dan desinkronisasi audio setelah beroperasi puluhan jam.
3. **Kebutuhan Dedicated Ambient Assistant**: Diperlukan asisten meja yang mandiri (appliance), selalu siaga (always-on), hening, bebas gema (echo-free), dan memiliki estetika visual yang tenang serta berwibawa.

---

### 3. Arsitektur Tingkat Tinggi & Pemisahan Peran
Sistem mengadopsi arsitektur terdistribusi (Decoupled Client-Server Model):

```
+-------------------------------------------------------------+
|                     JARVIS APPLIANCE                        |
|                                                             |
|   +--------------------------+   +----------------------+   |
|   |   CLIENT (Android)       |   |   SERVER (Hermes)    |   |
|   |   - Native Kotlin        |   |   - Python Async GW  |   |
|   |   - Hardware Audio/AEC   |   |   - STT Engine       |   |
|   |   - AudioReactive Orb UI |   |   - Hermes Agent LLM |   |
|   |   - Full-Duplex WS       |   |   - Neural TTS       |   |
|   +------------+-------------+   +-----------+----------+   |
|                ^                             ^              |
|                +====== WebSocket (LAN) ======+              |
+-------------------------------------------------------------+
```

1. **Client (Android Native Kiosk)**:
   - Berjalan pada Android Tablet, Smart TV Box, atau Smart Monitor Android.
   - Bertindak murni sebagai aktuator visual dan sensor audio.
   - Tidak melakukan komputasi LLM atau AI berat di perangkat.
2. **Server (Hermes Agent & Audio Gateway)**:
   - Berjalan di server lokal/cloud.
   - Menangani pengenalan suara (STT), penalaran otonom (Hermes Agent runtime), dan sintesis suara (TTS).

---

### 4. Kebutuhan Fungsional (Functional Requirements)

#### 4.1 Audio Pipeline (Input)
- **FR-A01 (Audio Capture)**: Merekam audio suara pengguna dalam format PCM mentah (16-bit, 16kHz, mono).
- **FR-A02 (Hardware AEC)**: Mengaktifkan Acoustic Echo Canceler (AEC) bawaan OS Android untuk membuang audio speaker agar tidak memicu transkripsi suara sendiri.
- **FR-A03 (Voice Activity Detection - VAD)**: Mendeteksi hening (silence) dan ambang suara (voice threshold) sehingga data suara hanya ditransmisikan saat pengguna berbicara.
- **FR-A04 (Audio Streaming)**: Mengirimkan potongan audio (audio chunks 100-200ms) melalui WebSocket biner ke server secara real-time.

#### 4.2 Brain & Reasoning Pipeline (Server)
- **FR-B01 (Streaming STT)**: Mengubah aliran audio menjadi teks dengan latensi di bawah 400ms (menggunakan Whisper/Faster-Whisper/Groq STT).
- **FR-B02 (Hermes Core Integration)**: Menyalurkan teks transkripsi ke Hermes Agent Runtime, mempertahankan memori interaksi, pemanggilan tool, dan kepribadian tenang berwibawa.
- **FR-B03 (Streaming TTS)**: Menghasilkan audio respons berbahasa Indonesia dengan intonasi natural (Edge TTS / ElevenLabs) secara bertahap (chunk streaming).
- **FR-B04 (State Management)**: Mengirimkan sinyal status real-time ke klien: `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, dan `ERROR`.

#### 4.3 Visual Interface & Wajah Digital (Output)
- **FR-V01 (Audioreactive Orb)**: Layar menampilkan wujud visual bola energi/gelombang frekuensi bercahaya dengan latar belakang gelap pekat (OLED-friendly black).
- **FR-V02 (State Animation)**:
  - `IDLE`: Denyut respirasi lambat (tenang, siaga).
  - `LISTENING`: Cincin melebar dan berpendar mengikuti desibel suara pengguna.
  - `THINKING`: Partikel berotasi konvergen menandakan kalkulasi data.
  - `SPEAKING`: Gelombang frekuensi berdenyut selaras dengan amplitudo audio respons.
- **FR-V03 (Subtitles / Readout)**: Teks jawaban mengalir halus di bawah visual orb (opsional, dapat diaktifkan/dinonaktifkan).

#### 4.4 Kiosk & Appliance Reliability
- **FR-K01 (Keep Screen On)**: Layar tidak boleh mati atau sleep secara otomatis selama daya terhubung.
- **FR-K02 (Auto Boot)**: Aplikasi langsung menyala dan mengunci layar (Lock Task / Kiosk) saat perangkat dihidupkan ulang.
- **FR-K03 (Auto Reconnect)**: Klien otomatis mencoba menghubungkan kembali WebSocket dengan strategi exponential backoff jika koneksi jaringan terputus.

---

### 5. Kebutuhan Non-Fungsional (Non-Functional Requirements)

1. **Stabilitas & Reliability**:
   - Zero-crash dalam operasional 24 jam x 7 hari secara terus-menerus.
   - Penggunaan memori (RAM) pada perangkat Android tidak boleh melebihi 80 MB (bebas dari Chromium memory leak).
2. **Latensi (Latency SLA)**:
   - Voice-to-first-sound: Waktu dari pengguna selesai berbicara hingga respons audio pertama terdengar di bawah 1.5 detik (pada jaringan lokal stabil).
   - Animasi visual: Konstan di 60 FPS tanpa frame drop.
3. **Privasi & Keamanan**:
   - Audio ditransmisikan secara tertutup melalui LAN atau WebSocket aman (WSS).
   - Mikrofon tidak merekam saat status offline.

---

### 6. Batasan Sistem (Constraints)
- Tidak menggunakan web wrapper / WebView dalam aplikasi Android.
- Tidak memerlukan pengetikan atau perangkat input keyboard/mouse di meja pengguna.
- Perangkat keras klien menggunakan arsitektur ARM64 / Android 8.0+ (API Level 26 ke atas).

---

### 7. Rencana Rilis & Milestone
- **Milestone 1**: Spesifikasi, protokol, dan kerangka repositori (Selesai).
- **Milestone 2**: Server WebSocket Audio Bridge + Mock Echo Audio.
- **Milestone 3**: Klien Android Audio Recorder + Player + AEC Validation.
- **Milestone 4**: Integrasi penuh STT + Hermes Agent + Edge TTS.
- **Milestone 5**: Visualizer OpenGL/Canvas Orb & Uji Coba Kiosk 24 Jam.
