# Arsitektur & Desain Sistem Nexo Dedicated Appliance

Dokumen referensi mendalam mengenai arsitektur teknis, aliran data biner/JSON, dan model konkurensi **Nexo Dedicated Appliance**.

---

## 1. Filosofi & Batasan Sistem (Invariants)

1. **Physical Client as Sensor & Canvas Only**:
   Tablet/smart screen Android tidak pernah memuat bobot model neural network besar (LLM, Whisper STT, TTS). Komputasi inferensi dan status memori sepenuhnya di-offload ke server Linux.
2. **Native Over WebView**:
   - WebView memiliki overhead alokasi memory yang besar, latency audio yang tidak menentu, serta kerentanan memory leak pada siklus hidup 24/7.
   - Klien Android Nexo dibangun dengan Kotlin murni, memanfaatkan audio stack level rendah (PCM 16-bit 16kHz) dan hardware acceleration OpenGL ES / Canvas untuk 60 FPS visual rendering.
3. **Pemberantasan Gema Fisik (Acoustic Echo Cancellation - AEC)**:
   - Monitor pintar menempatkan mikrofon dan speaker pada jarak yang sangat dekat (< 30 cm).
   - Penggunaan `MediaRecorder.AudioSource.VOICE_COMMUNICATION` memicu integrasi DSP bawaan chipset smartphone/tablet.
   - Android `AcousticEchoCanceler` diikat langsung ke session ID dari `AudioRecord`.
4. **Resilience & Zero Touch**:
   - Reconnect backoff otomatis (1s, 2s, 4s, 8s, 16s, 30s) ketika koneksi terputus.
   - Screen wake lock menjaga tampilan tetap aktif tanpa intervensi pengguna.
   - Status visual berpindah ke `DISCONNECTED` tanpa menyebabkan app crash.

---

## 2. Rincian Lapisan Klien Android

### 2.1 Audio Ingestion & Buffer Pool
- **Sampling Rate**: 16.000 Hz, 16-bit Linear PCM, Mono.
- **Ukuran Buffer Frame**: 3.200 byte (mewakili 100 milidetik audio).
- **Buffer Pool**: Buffer dialokasikan di awal (pre-allocated byte buffer pool) untuk mencegah Garbage Collection (GC) pauses di Android VM yang dapat menimbulkan stuttering pada audio streaming.
- **Voice Activity Detection (VAD)**:
  - Mengukur Root Mean Square (RMS) dari frame audio.
  - Jika level suara berada di bawah ambang batas kesunyian selama lebih dari 800 ms, klien mengirim pesan kontrol JSON `{"type": "vad_speech_end"}` dan menghentikan pengiriman stream audio PCM sementara.

### 2.2 Visualizer & Digital Mascot
- **Framerate Target**: 60 FPS stabil.
- **Komponen**: `SurfaceView` atau GPU-accelerated Custom View.
- **Model Reaktivitas**:
  - `IDLE`: Animasi bernapas (breathing pulse) halus, warna cyan/biru lembut.
  - `LISTENING`: Reaktif terhadap level input mikrofon lokal (amplitudo suara pengguna).
  - `PROCESSING`: Partikel memutar (*thinking spiral*), indikator bahwa Hermes sedang memproses instruksi.
  - `SPEAKING`: Reaktif terhadap 5-band frequency telemetry yang dikirimkan oleh server gateway setiap 40 ms selama TTS berbicara.
  - `ERROR`: Denyut merah redup jika terjadi kesalahan otentikasi atau kegagalan jaringan.
  - `DISCONNECTED`: Pendaran abu-abu/redup saat mencari sambungan server.

---

## 3. Rincian Lapisan Server Gateway (`nexo_server`)

### 3.1 Async Event Loop & Worker Architecture
Gateway dibangun menggunakan FastAPI dan Python `asyncio`. Setiap sesi klien yang terhubung menjalankan empat worker independen yang berkomunikasi melalui queue `asyncio`:

1. **`inbound_receiver`**:
   - Membaca frame WebSocket yang masuk dari klien Android.
   - Memisahkan pesan kontrol JSON (hello, ping, vad_speech_end, cancel) dan payload biner audio PCM.
2. **`audio_transcription_worker`**:
   - Mengumpulkan potongan PCM biner yang dikirim oleh klien saat berbicara.
   - Meneruskan audio terkumpul ke instance Faster-Whisper.
   - Menghasilkan transkrip teks dan mengirimkan event kognitif ke SessionHub.
3. **`agent_reasoning_worker`**:
   - Menerima teks transkripsi dari STT.
   - Memanggil Hermes Agent melalui subprocess CLI bridge.
   - Mengalirkan respons teks secara inkremental (*sentence streaming*).
4. **`tts_streaming_worker`**:
   - Menerima kalimat/frasa dari agent reasoning worker.
   - Memanggil Edge-TTS secara asinkron.
   - Mengirim potongan audio biner kembali ke klien via WebSocket.
   - Menghitung FFT 5-band frequency dan memancarkan frame telemetri JSON secara bersamaan.

### 3.2 Kontrak Hermes Subprocess Bridge
Server mengeksekusi Hermes CLI dengan perintah:
```text
hermes chat -c appliance --create-if-missing -q "<transkripsi>" -Q
```
- `-c appliance`: Menjaga percakapan berada di dalam sesi bernama `appliance` (atau sesi tertaut kustom via `nexo link`).
- `--create-if-missing`: Membuat sesi baru jika sesi belum pernah dibuka.
- `-q "<transkripsi>"`: Mengirim teks masukan sebagai pertanyaan tunggal.
- `-Q`: Single-query mode, mencegah prompt interaktif dan langsung mengembalikan respons.

Bridge membersihkan metadata CLI (seperti header percakapan atau token counter) sebelum teks dikirimkan ke mesin TTS.

---

## 4. Protokol Komunikasi WebSocket (`/ws/appliance`)

### 4.1 Handshake & Hello
Frame pertama yang dikirimkan oleh klien **wajib** berupa pesan JSON Hello:
```json
{
  "type": "hello",
  "protocol_version": "1.0",
  "client_type": "android_native",
  "device_id": "ABC1234XYZ",
  "token": "BEARER_TOKEN_256_BIT",
  "audio_format": {
    "sample_rate": 16000,
    "channels": 1,
    "bit_depth": 16,
    "codec": "pcm"
  }
}
```

Jika autentikasi valid dan origin cocok, server membalas:
```json
{
  "type": "hello_ack",
  "status": "ready",
  "session_id": "sess_89a0b1c2",
  "server_version": "1.0.0"
}
```

### 4.2 Frame Kontrol & Telemetri Server ke Klien
- **State Change**:
  ```json
  {"type": "state_change", "state": "LISTENING"}
  {"type": "state_change", "state": "PROCESSING"}
  {"type": "state_change", "state": "SPEAKING"}
  ```
- **Frequency Telemetry (dikirim setiap 40 ms saat berbicara)**:
  ```json
  {"type": "telemetry", "bands": [0.12, 0.45, 0.88, 0.32, 0.05]}
  ```
- **Cognitive Transcript Text**:
  ```json
  {"type": "transcript", "role": "user", "text": "Halo Nexo"}
  {"type": "transcript", "role": "assistant", "text": "Halo! Ada yang bisa saya bantu hari ini?"}
  ```
