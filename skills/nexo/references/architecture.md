# Arsitektur & Desain Sistem Nexo Dedicated Appliance

Dokumen referensi mendalam mengenai arsitektur teknis, aliran data biner/JSON, model konkurensi, dan ketahanan sistem **Nexo Dedicated Appliance**.

---

## 1. Filosofi & Batasan Sistem (Invariants)

1. **Physical Client as Sensor & Canvas Only**:
   Tablet/smart screen Android tidak pernah memuat bobot model neural network besar (LLM, Faster-Whisper STT, Edge-TTS). Komputasi inferensi dan status memori sepenuhnya di-offload ke server Linux.
2. **Native Over WebView**:
   - WebView memiliki overhead alokasi memory yang besar, latency audio yang tidak menentu, serta kerentanan memory leak pada siklus hidup 24/7.
   - Klien Android Nexo dibangun dengan Kotlin murni, memanfaatkan audio stack level rendah (PCM 16-bit 16kHz) dan hardware acceleration Canvas / SurfaceView untuk 60 FPS visual rendering.
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
- **Buffer Pool**: Buffer dialokasikan di awal (*pre-allocated byte buffer pool*) untuk mencegah Garbage Collection (GC) pauses di Android VM yang dapat menimbulkan stuttering pada audio streaming.
- **Voice Activity Detection (VAD)**:
  - Mengukur Root Mean Square (RMS) dari frame audio dengan histeresis dual-threshold.
  - Jika level suara berada di bawah ambang batas kesunyian selama lebih dari 800 ms, klien mengirim pesan kontrol JSON `{"type": "vad_speech_end"}` dan menghentikan pengiriman stream audio PCM sementara.

### 2.2 Sinyal Audio Lokal Instan (EarconManager)
- Mengatasi persepsi latensi (*perceived latency*) dengan umpan balik audio lokal seketika:
  - **Listening Chime (`earcon_listening.wav`)**: Dua nada naik E5 (659.25 Hz) -> B5 (987.77 Hz), 320 ms saat mikrofon siap menyimak.
  - **Thinking Chime (`earcon_thinking.wav`)**: Nada harmoni A5 (880 Hz) -> D6 (1174.66 Hz), 180 ms saat pengguna selesai berbicara.
- Dikelola melalui `SoundPool` independen dengan pemisahan audio focus agar tidak bertabrakan dengan `AudioTrack` TTS.

### 2.3 Visualizer & Digital Mascot 60 FPS
- **Framerate Target**: 60 FPS stabil dikoordinasikan via `Choreographer.FrameCallback`.
- **Komponen**: `SurfaceView` atau GPU-accelerated Custom View.
- **Model Reaktivitas & 16 State Ekspresi**:
  - `IDLE`: Animasi bernapas (*breathing pulse*) halus, warna cyan lembut.
  - `LISTENING`: Reaktif terhadap level input mikrofon lokal (amplitudo suara pengguna).
  - `PROCESSING`: Partikel memutar (*thinking spiral*), indikator bahwa Hermes sedang memproses instruksi.
  - `SPEAKING`: Reaktif terhadap 5-band frequency telemetry yang dikirimkan oleh server gateway setiap 40 ms selama TTS berbicara.
  - `ERROR`: Denyut merah redup jika terjadi kesalahan otentikasi atau kegagalan jaringan.
  - `DISCONNECTED`: Pendaran abu-abu/redup saat mencari sambungan server.
  - `SLEEPING`: Mode tidur dengan ekspresi `sleeping` (`mascot_sleeping.xml`) saat inaktivitas 5 menit tercapai.
- **Anti Burn-In Pixel Shifting**:
  - Menggeser titik jangkar rendering kanvas sebesar ±2 hingga ±6 piksel secara periodik (setiap 60 detik) untuk melindungi sub-piksel OLED/AMOLED pada operasi kios 24/7.

---

## 3. Rincian Lapisan Server Gateway (`nexo_server`)

### 3.1 Pipelined Sentence-Chunked Streaming
1. **Low TTFA (< 800 ms)**: Token respons dari Hermes Core dialirkan secara inkremental via generator `query_stream`.
2. **SentenceChunker**:
   - Memantau aliran token teks dan mendeteksi batas kalimat secara cerdas (titik `.`, seru `! `, tanya `?`, atau jeda baris ganda `\n\n`).
   - Pengecualian pada singkatan umum (`Dr.`, `PT.`, `dkk.`), angka desimal (`3.14`), dan klausa kutipan.
3. **Pipelined Edge-TTS Worker**:
   - Kalimat pertama langsung dialirkan ke `synthesize_stream`.
   - Potongan audio biner MP3 kalimat pertama langsung dipancarkan ke Android via WebSocket saat kalimat berikutnya masih diproses.
4. **Parsing & Stripping Tag Emosi (`[expr:...]`)**:
   - Mengekstrak emosi untuk memperbarui state ekspresi maskot pada frame `state_change` dan `cognitive`.
   - Menghapus bersih tag `[expr:...]` sebelum audio disintesis agar tag teknis tidak pernah diucapkan oleh TTS.
5. **Akurasi Durasi MP3 & Echo Guard 600 ms**:
   - Header frame audio MP3 diparsing oleh `parse_audio_duration` (`mp3_frame.py`).
   - Batas waktu playback diakumulasikan ke `_playback_until`.
   - Server menahan peralihan ke `IDLE` dan pembukaan mikrofon hingga seluruh audio selesai diputar ditambah buffer pengaman 600 ms untuk mencegah feedback loop.

### 3.2 Kontrak Hermes Subprocess Bridge & SessionHub
- Server mengeksekusi Hermes CLI dengan perintah:
  ```text
  hermes chat -c appliance --create-if-missing -q "<transkripsi>" -Q
  ```
- Mendukung pemetaan sesi kanonis `nexo` di `~/.hermes/state.db` secara dua arah.
- Sesi terkunci (*canonical session locking*) untuk mencegah desinkronisasi memori kerja antar klien.

---

## 4. Protokol Komunikasi WebSocket (`/ws/appliance`)

### 4.1 Handshake & Hello
Frame pertama dari klien **wajib** berupa pesan JSON Hello:
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

Balasan server (Hello ACK):
```json
{
  "type": "hello_ack",
  "status": "ready",
  "session_id": "sess_89a0b1c2",
  "server_version": "1.0.0"
}
```

### 4.2 Frame Kontrol, Telemetri, & Cognitive Streaming
- **State Change**:
  ```json
  {"type": "state_change", "state": "LISTENING", "expression": "netral"}
  {"type": "state_change", "state": "PROCESSING", "expression": "berpikir"}
  {"type": "state_change", "state": "SPEAKING", "expression": "senang"}
  {"type": "state_change", "state": "SLEEPING", "expression": "sleeping"}
  ```
- **Frequency Telemetry (dikirim setiap 40 ms saat berbicara)**:
  ```json
  {"type": "telemetry", "bands": [0.12, 0.45, 0.88, 0.32, 0.05]}
  ```
- **Cognitive Stream Frame**:
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
- **Transcript Text**:
  ```json
  {"type": "transcript", "role": "user", "text": "Halo Nexo"}
  {"type": "transcript", "role": "assistant", "text": "Halo! Ada yang bisa saya bantu hari ini?"}
  ```
