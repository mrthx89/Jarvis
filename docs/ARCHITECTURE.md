# Architecture & System Design Document
## Project: Nexo Dedicated Appliance (formerly Nexo)
**Architect**: Yanto Hariyono (YH Dev)  
**Version**: 1.1.0  
**Ethos**: Berkarya dengan hati @ 2026  

---

### 1. Desain Arsitektur Sistem

Arsitektur dirancang dengan prinsip **Separation of Concerns (SoC)** yang tegas:
- **Klien Android Native (Indra & Wajah)**: Mengelola penangkapan audio mikrofon dengan AEC, pemutaran audio berlatensi rendah, sinyal audio lokal (*earcons*), visualisasi gelombang spektrum audio 60 FPS, render ekspresi maskot, serta perlindungan layar kios 24/7.
- **Server Gateway & Hermes Core (Intelegensi Kognitif)**: Mengelola autentikasi perangkat, *circular pre-roll buffer*, VAD berhisteresis, STT (Faster-Whisper), penalaran agen AI (Hermes Agent), *sentence-chunked streaming*, parsing tag emosi maskot, sintesis suara berjenjang (Edge-TTS), perhitungan durasi frame MP3, dan sinkronisasi sesi terminal.

```text
+-------------------------------------------------------------------------------------------------+
|                                     PHYSICAL CLIENT LAYER                                       |
|                                                                                                 |
|  [Hardware Mic Array]                                                         [Speaker Hardware]|
|           |                                                                           ^         |
|           v                                                                           |         |
|  [AudioRecord (16kHz)]                                                      [AudioTrack/Player] |
|           |                                                                           ^         |
|           +---> [AcousticEchoCanceler (AEC)]                                          |         |
|           |                                                                  [Audio Ring Buffer]|
|           v                                                                           ^         |
|  [HalfDuplexGate] <====================================================+              |         |
|           |         (Mute during Earcon, Thinking & Speaking)          |              |         |
|           v                                                            |              |         |
|  [Inbound PCM 16kHz]                                            [EarconManager]       |         |
|           |                                                     (Chime Playback)      |         |
|           |                                                            ^              |         |
|           |                                                            |              |         |
|           v                                                            v              |         |
|   +--------------------------------------------------------------------------+        |         |
|   |                  NexoWebSocketClient (OkHttp WebSocket)                  |        |         |
|   +-------------------+--------------------------------------+---------------+        |         |
|                       |                                      ^                        |         |
|                       |                                      |                        |         |
|                       |            [VisualizerView: 60 FPS GPU Canvas]                |         |
|                       |            - 16 Mascot Expressions + Ambient Sleep            |         |
|                       |            - Screen Burn-In Pixel Shifting                    |         |
+-----------------------|--------------------------------------|------------------------|---------+
                        | (Full-Duplex WebSocket)              |                        |
                        | Binary: Inbound PCM                  | JSON: Telemetry/States | Binary: Outbound
                        v                                      |                        | MP3 Audio
+--------------------------------------------------------------+------------------------+---------+
|                                        SERVER LAYER                                             |
|                                                                                                 |
|   +-----------------------------------------------------------------------------------------+   |
|   |                      FastAPI WebSocket Gateway (nexo_server)                           |   |
|   +-------------------+--------------------------------------------------+------------------+   |
|                       |                                                  ^                      |
|                       v                                                  |                      |
|         [Circular Pre-roll Ring Buffer]                                  |                      |
|         (500 ms PCM buffer / 5x 100 ms)                                  |                      |
|                       |                                                  |                      |
|                       v                                                  |                      |
|         [VAD Hysteresis Engine]                                          |                      |
|         - Onset: -35 dBFS | Cont: -43 dBFS                               |                      |
|                       |                                                  |                      |
|                       v                                                  |                      |
|         [STT Engine: Faster-Whisper]                                     |                      |
|                       |                                                  |                      |
|                       v (User Text)                                      |                      |
|         [Mascot Command Router] ---- (Direct Match) ------------> [Direct Speech]               |
|                       |                                                  |                      |
|                       v (Natural Query)                                  |                      |
|         [Hermes Agent Core (query_stream)]                               |                      |
|         - Persistent Session State (nexo)                                |                      |
|                       |                                                  |                      |
|                       v (Token Stream)                                   |                      |
|         [SentenceChunker (Delimiter Segmentation)]                       |                      |
|                       |                                                  |                      |
|                       v (Sentence Stream)                                |                      |
|         [Emotion Tag Parser ([expr:...])] --------> [State/Mascot Event] |                      |
|                       |                                                  |                      |
|                       v (Clean Sentence Stream)                          |                      |
|         [Edge-TTS Stream Pipeline]                                       |                      |
|                       |                                                  |                      |
|                       v (MP3 Audio Chunks)                               |                      |
|         [MP3 Frame Duration Parser] ------------------------------------>+ (Dynamic Duration)   |
|                       |                                                  |                      |
|                       +------------------------------------------------->+ (Outbound Binary)    |
|                                                                                                 |
|   +-----------------------------------------------------------------------------------------+   |
|   |      SessionHub & AnswerMirror (CLI nexo watch, nexo bridge, Telegram Device Access)    |   |
|   +-----------------------------------------------------------------------------------------+   |
+-------------------------------------------------------------------------------------------------+
```

---

### 2. Klien Android: Lapisan Native, Audio & Perlindungan Tampilan

#### 2.1 Audio Ingestion, AEC & HalfDuplexGate
- **Audio Source**: `MediaRecorder.AudioSource.VOICE_COMMUNICATION`. Memicu hardware DSP echo-cancellation bawaan chipset perangkat.
- **Hardware AEC**: `android.media.audiofx.AcousticEchoCanceler` diikatkan ke `audioSessionId` dari `AudioRecord`.
- **HalfDuplexGate**: Komponen kontrol lokal yang secara ketat menonaktifkan pengiriman byte audio rekaman mikrofon ketika:
  1. Earcon lokal sedang diputar.
  2. Status sistem berada pada `THINKING` atau `SPEAKING`.
  3. Buffer speaker `AudioTrack` masih menyisakan data yang belum tuntas terdengar (*audio drain guard*).
- **Zero-Allocation Audio Loop**: Menggunakan buffer array byte statis 3200 byte (100 ms pada 16kHz 16-bit mono) untuk mencegah alokasi memori berulang dan jeda *Garbage Collection (GC)* pada Android.

#### 2.2 Sinyal Audio Lokal (EarconManager)
Untuk memberikan kepastian status secara instan tanpa menunggu latensi jaringan:
- **Listening Chime (`earcon_listening.wav`)**: Dua nada berpadu naik (E5 659.25 Hz -> B5 987.77 Hz) berdurasi 320 ms yang menandakan mikrofon siap menyimak.
- **Thinking Chime (`earcon_thinking.wav`)**: Nada resolusi harmonik lembut (A5 880 Hz -> D6 1174.66 Hz) berdurasi 180 ms yang menandakan suara pengguna telah selesai dan penalaran dimulai.
- Audio dikelola melalui pemutar berlatensi rendah dengan *audio focus* yang terjaga.

#### 2.3 Visualizer View & Ekspresi Maskot 60 FPS
- **Hardware-Accelerated Canvas**: Rendering wajah maskot digital dan spektrum frekuensi audio dijalankan pada 60 FPS dengan koordinasi `Choreographer.FrameCallback`.
- **16 Ekspresi Digital Maskot**: Aset vektor ekspresi (`netral`, `senang`, `sangat_senang`, `tertawa`, `bingung`, `mengantuk`, `mengedip`, `terkejut`, `cinta`, `jahil`, `keren`, `malu`, `marah`, `menangis`, `sedih`, `takut`) plus status khusus `sleeping`.
- **Ambient Sleep Mode & Anti Burn-In Pixel Shifting**:
  - Setelah periode waktu inaktivitas yang ditentukan, klien secara otomatis mengaktifkan mode tidur dengan ekspresi maskot `sleeping` (`mascot_sleeping.xml`).
  - Untuk mencegah *screen burn-in* pada panel OLED/AMOLED dalam mode operasional 24/7, kanvas menggeser koordinat gambar maskot secara perlahan (*subtle pixel shifting*) sebesar beberapa piksel setiap interval waktu tertentu tanpa mengganggu estetika tampilan.

---

### 3. Server: Audio Gateway & Pipeline Pemrosesan

#### 3.1 Pipelined Sentence-Chunked Streaming
Untuk memangkas waktu tunggu pengguna (*Time to First Audio / Latency*):
1. **Streaming Hermes Agent (`query_stream`)**: Mengalirkan token jawaban LLM segera setelah dihasilkan oleh model.
2. **SentenceChunker**:
   - Memantau aliran token dan mendeteksi batas kalimat secara cerdas (tanda titik `.`, seru `!`, tanya `?`, atau baris baru yang diikuti spasi atau batas kata).
   - Menangani singkatan umum dan tanda petik tanpa memotong kalimat secara prematur.
3. **Pipelined Edge-TTS Synthesis**:
   - Kalimat pertama yang lengkap langsung dikirimkan ke worker sintesis Edge-TTS (`synthesize_stream`).
   - Server tidak menunggu kalimat kedua atau sisa jawaban selesai dipikirkan oleh agen.
   - Client menerima audio frame pertama saat agen masih memproses kalimat berikutnya, menciptakan pengalaman dialog yang alami dan responsif.

#### 3.2 Pre-roll Circular Buffer & VAD Histeresis
- **Circular Buffer (`AudioBuffer`)**: Buffer sirkular berkapasitas 5 frame (500 ms) menampung audio terus-menerus. Saat VAD mendeteksi suara ucapan, isi pre-roll langsung disisipkan ke rekaman STT sehingga huruf dan kata pembuka tidak hilang.
- **Dual-Threshold Hysteresis**:
  - Ambang batas awal (-35 dBFS) menyaring desah ruangan/noise.
  - Ambang batas kelanjutan (-43 dBFS) menjaga agar intonasi yang merendah di tengah/akhir kalimat tidak dianggap sebagai jeda hening.
  - Jeda hening 1.5 detik memfinalisasi ucapan pengguna.

#### 3.3 Akurasi Durasi Audio & Echo Guard (`mp3_frame.py`)
- Potongan audio MP3 yang dihasilkan Edge-TTS diparsing header framenya (bitrate, sample rate, padding) oleh `parse_audio_duration`.
- Server mengakumulasikan durasi riil audio yang dikirim ke variabel `_playback_until`.
- Server menahan peralihan ke status `IDLE` hingga durasi playback selesai ditambah *echo guard* 600 ms. Ini menjamin suara asisten telah tuntas keluar dari speaker sebelum mikrofon dibuka kembali, meniadakan loop feedback pada sistem half-duplex.

#### 3.4 Parsing & Stripping Tag Emosi (`[expr:...]`)
- Hermes Agent dapat menyisipkan tag emosi terstruktur, misalnya: `[expr:senang] Halo! Ada yang bisa saya bantu?`.
- Komponen `parse_and_strip_emotional_tags`:
  1. Mengekstrak tag emosi pertama dan memetakan alias bahasa Indonesia (`bahagia` -> `senang`, `ngantuk` -> `mengantuk`, `tidur` -> `sleeping`).
  2. Mengirimkan event `state_change` atau `cognitive` dengan field `expression`.
  3. Menghapus tag dari teks sebelum dikirim ke TTS dan transkripsi layar agar asisten tidak menyuarakan teks teknis tag.

#### 3.5 SessionHub & Terminal Session Bridge
- Memetakan percakapan kanonis `nexo` di `~/.hermes/state.db` secara dua arah.
- Mendukung pemantauan telemetri kognitif real-time melalui CLI `scripts/nexo watch` (`cognitive` stream) dan percakapan dua arah via `scripts/nexo bridge`.
- Menyediakan penguncian sesi kanonis (*canonical session locking*) untuk mencegah desinkronisasi konteks percakapan antar klien.

---

### 4. Strategi Ketahanan & Pencegahan Masalah (Resilience)

1. **Jaringan Terputus (Network Drop & Exponential Backoff)**:
   - Klien Android menerapkan auto-reconnect berjenjang: 1s, 2s, 4s, 8s, hingga maks 30s.
   - Status visual berpindah ke mode `DISCONNECTED` tanpa menyebabkan force-close (ANR/Crash).
2. **Buffer Overrun / Underrun Protection**:
   - Ring buffer audio berkapasitas adaptif di Android menampung potongan audio streaming TTS secara tertib jika terjadi fluktuasi latensi jaringan.
3. **Pencegahan Memory Leak**:
   - Zero-allocation pada hot path audio capture dan audio playback.
   - Penggunaan `WeakReference` pada context dan binding service di Android.
   - Pembersihan sesi terputus (*session cleanup*) otomatis pada SessionHub server.
