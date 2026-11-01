# Architecture & System Design Document
## Project: Jarvis Dedicated Appliance
**Architect**: Yanto Hariyono (YH Dev)  
**Version**: 1.0.0  

---

### 1. Desain Arsitektur Sistem

Arsitektur dirancang dengan prinsip **Separation of Concerns (SoC)** yang tegas: Klien Android mengelola peranti keras sensor/aktuator, sedangkan Server mengelola intelegensi buatan.

```
+-------------------------------------------------------------------------------+
|                             PHYSICAL CLIENT LAYER                             |
|                                                                               |
|  [Hardware Mic Array]                                       [Monitor/Speaker] |
|           |                                                         ^         |
|           v                                                         |         |
|  [AudioRecord (16kHz)]                                  [AudioTrack/Playback] |
|           |                                                         ^         |
|           +---> [AcousticEchoCanceler (AEC)]                        |         |
|           |                                                         |         |
|           v                                                         |         |
|  [VoiceActivityDetector]                                [Visualizer (60 FPS)] |
|           |                                                         ^         |
|           v                                                         |         |
|   +---------------------------------------------------------------+ |         |
|   |                  JarvisWebSocketClient (OkHttp)               | |         |
|   +-------------------------------+-------------------------------+ |         |
+-----------------------------------|---------------------------------|---------+
                                    | (Full-Duplex WebSocket)         |
                                    v                                 |
+---------------------------------------------------------------------+---------+
|                              SERVER LAYER                                     |
|                                                                               |
|   +---------------------------------------------------------------+           |
|   |                     FastAPI WebSocket Gateway                 |           |
|   +-------------------------------+-------------------------------+           |
|                                   |                                           |
|         +-------------------------+-------------------------+                 |
|         |                                                   |                 |
|         v                                                   v                 |
|  [Inbound Audio Stream]                             [Outbound Stream Queue]   |
|         |                                                   ^                 |
|         v                                                   |                 |
|  [STT Engine (Faster-Whisper/Groq)]                 [TTS Engine (Edge/Neural)]|
|         |                                                   ^                 |
|         v                                                   |                 |
|   [Transcribed Text]                                [Generated Response]      |
|         |                                                   ^                 |
|         +-------------> [Hermes Agent Core] ----------------+                 |
|                         - Session Memory                                      |
|                         - Tool Invocations                                    |
|                         - Personality Alignment                               |
+-------------------------------------------------------------------------------+
```

---

### 2. Klien Android: Lapisan Native & Audio

#### 2.1 Audio Ingestion & Acoustic Echo Cancellation (AEC)
Kunci utama keberhasilan komunikasi hands-free di dekat monitor adalah peniadaan gema suara Jarvis sendiri:
- **Audio Source**: `MediaRecorder.AudioSource.VOICE_COMMUNICATION`. Sumber ini secara otomatis memicu pemrosesan DSP perangkat keras pada chipset Android (Qualcomm/MediaTek/Exynos).
- **AcousticEchoCanceler**: Kelas `android.media.audiofx.AcousticEchoCanceler` diikatkan ke `audioSessionId` dari `AudioRecord`.
- **Buffer Pool**: Menggunakan array byte statis (pre-allocated byte buffers) berukuran 3200 byte (100 ms audio pada 16kHz 16-bit mono) untuk mencegah garbage collection (GC) pauses di Android.

#### 2.2 Voice Activity Detection (VAD)
Klien menerapkan VAD berbasis energi (RMS Amplitude Threshold) atau Silero VAD ringan:
- Saat amplitudo suara di bawah ambang batas selama > 800 ms, audio transmisi dihentikan dan sinyal end-of-speech dikirimkan.
- Menghemat penggunaan bandwidth jaringan dan sumber daya komputasi server.

#### 2.3 Visualizer View (Wajah Digital)
- Menggunakan `SurfaceView` atau Custom `View` dengan akselerasi GPU (Hardware Accelerated Canvas).
- Loop rendering berjalan pada utas terpisah atau `Choreographer.FrameCallback` pada 60 FPS.
- Vektor bentuk berupa kombinasi partikel radial atau lingkaran energi bercahaya yang bereaksi langsung terhadap data frekuensi dan status sistem.

---

### 3. Server: Audio Gateway & Pipeline Pemrosesan

#### 3.1 Concurrency & Asynchronous Design
- Server dibangun di atas Python `asyncio` dan `FastAPI / websockets`.
- Setiap koneksi klien memiliki task loop terpisah:
  1. `inbound_receiver`: Membaca pesan teks JSON dan frame biner audio.
  2. `audio_transcription_worker`: Mengalirkan audio ke model STT.
  3. `agent_reasoning_worker`: Mengirim transkripsi ke Hermes Agent dan memproses respons secara streaming.
  4. `tts_streaming_worker`: Mengonversi teks keluaran Hermes menjadi potongan audio suara secara inkremental tanpa menunggu seluruh kalimat selesai.

#### 3.2 Hermes Agent Bridge
- Server berkomunikasi dengan runtime Hermes Agent melalui integrasi Python API / CLI / Socket internal.
- Menjaga konteks percakapan tetap persisten di `state.db` Hermes.

---

### 4. Strategi Penanganan Masalah & Ketahanan (Resilience)

1. **Jaringan Terputus (Network Drop)**:
   - Klien Android menerapkan exponential backoff reconnect: 1s, 2s, 4s, 8s, maksimal 30s.
   - Status visual berpindah ke mode `DISCONNECTED` (pendaran redup) tanpa menyebabkan aplikasi berhenti paksa (crash).
2. **Buffer Underrun / Overrun**:
   - Jika audio dari TTS datang lebih cepat dari kemampuan pemutaran, ring buffer di Android menampung potongan audio secara tertib.
3. **Pencegahan Memory Leak**:
   - Tidak ada alokasi objek baru di dalam callback audio loop.
   - Menggunakan referensi lemah (`WeakReference`) pada activity dan context di dalam background service.
