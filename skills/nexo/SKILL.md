---
name: nexo
description: Use when operating Nexo. Manage and deploy smart appliance.
version: 1.6.0
author: Yanto Hariyono (YH Dev), mrthx89
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [nexo, appliance, smart-screen, voice-assistant, audio-gateway, faster-whisper, edge-tts, sentence-streaming, earcons, ambient-mode, systemd, cloudflare-tunnel, speaker-verification, voiceprint, campplus, vad-tuning, local-vu-meter, multi-device, barge-in, touch-spring-physics, smart-briefing]
    category: iot-appliance
---

# Nexo Dedicated Appliance Skill

Panduan operasional, deployment, arsitektur, dan integrasi resmi untuk **Nexo Dedicated Appliance** — asisten AI fisik otonom (monitor/smart screen + mikrofon/speaker) yang berjalan tanpa ketergantungan pada PC konvensional di meja kerja.

Skill ini dirancang agar setiap instance Hermes Agent baru di masa mendatang dapat langsung memahami arsitektur, mengoperasikan alur sentence-chunked streaming & penanganan latensi, memanfaatkan fitur smart screen kios 24/7 (ambient sleep mode, pixel shifting, earcons, tactile touch physics), melakukan deployment host baru, menautkan sesi terminal lokal, menjalankan verifikasi, serta mengatasi masalah (*troubleshooting*) secara mandiri.

---

## 1. Arsitektur Sistem & Invarian Utama

Nexo dibangun dengan filosofi rekayasa **"Berkarya dengan hati @ 2026"** oleh Yanto Hariyono (YH Dev). Arsitektur menerapkan pemisahan tanggung jawab (*Separation of Concerns*) yang tegas: klien fisik Android berfungsi sebagai **"Indra dan Wajah"**, sedangkan server mengelola **"Pikiran dan Suara"**.

### Diagram Topologi End-to-End

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   PHYSICAL CLIENT LAYER (Android Native)               │
│                                                                        │
│   [Mic internal HP/tablet]                               [Speaker/Monitor] │
│            │                                                 ▲         │
│            ▼                                                 │         │
│   [AudioRecord (16kHz)]                             [AudioTrack]       │
│            │                                                 ▲         │
│            ├──────► [Hardware AEC]                           │         │
│            │                                      [EarconManager]      │
│            ▼                                      - Listening Chime    │
│   [Capture + local RMS]                           - Thinking Chime     │
│   - Half-duplex gate                                         ▲         │
│   - PCM dikirim saat siap                                    │         │
│            │                                        [Mascot Visualizer]│
│            ▼                                        - 60 FPS Canvas    │
│   ┌──────────────────────────────────────────────┐  - Pixel Shifting   │
│   │ NexoWebSocketClient (OkHttp, Keystore Auth)  │  - Ambient Sleep    │
│   └──────────────────────┬───────────────────────┴──- Touch Physics────┘
└──────────────────────────┼───────────────────────────────────┼──────────┘
                           │ Full-Duplex WebSocket             │
                           │ - Inbound: Binary PCM16           │
                           │ - Outbound: PCM16 LE, 24 kHz mono │
                           │ - Bi-directional: JSON Control &  │
                           │   Cognitive Streaming Frames      │
                           ▼                                   │
┌──────────────────────────────────────────────────────────────┼──────────┐
│                   SERVER LAYER (FastAPI Async Gateway)       │          │
│                                                              │          │
│   ┌──────────────────────────────────────────────────────────┴─────┐    │
│   │    WebSocket Gateway & SessionHub (/ws/appliance, :8766)       │    │
│   └──────┬───────────────────────┬───────────────────────────┬─────┘    │
│          │                       │                           │          │
│          ▼                       ▼                           ▼          │
│   [VAD + pre-roll/buffer] [Session Event Bus]      [Outbound TTS Queue] │
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
│                            - PCM Duration & Echo Guard (600ms)      │
│                            - SessionHub Locking (state.db)              │
│                            - Smart Briefing & Contextual Greeting       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Invarian Arsitektur Utama

1. **Decoupled Architecture**: Tempatkan Hermes, STT dan TTS di server; jangan melarang pemrosesan audio ringan atau VAD lokal di Android. HP harus memiliki lifecycle capture dan buffer yang tidak bergantung pada jaringan. Bedakan implementasi saat ini dari rancangan capture lokal/utterance utuh yang belum diverifikasi; perekaman lokal tidak berarti percakapan offline.
2. **Native over WebView**: Gunakan klien Android native; Kotlin menangani integrasi platform, sedangkan core Java bebas Android dapat dipakai untuk state machine audio yang dapat diuji di host. Jangan menjanjikan bebas memory leak hanya karena tidak memakai WebView. Periksa versi SDK dari manifest/build configuration aktual.
3. **Smart App Zero-Option UX & Resilient Continuous Voice (Prinsip Smart App & Anti-Mati Sendiri)**:
   - **Zero-Option Simplicity**: Pengguna menginginkan aplikasi pintar yang langsung memilih opsi terbaik secara otomatis tanpa membebani layar dengan tumpukan tombol, sakelar mode (otomatis vs manual), atau jargon teknis (`(VAD lokal)`, `(manual)`).
   - **Tombol Lingkaran Kompak & Indikator Wave Ala Gemini Live (`LiveWaveformView`)**: Layar utama Android hanya menampilkan maskot di tengah, status ringkas di atas, dan **1 tombol aksi utama di bawah** (`toggleVoice`) berbentuk **lingkaran kompak `56dp x 56dp`** (*Floating Circular Pill* dengan sudut `radius="28dp"` dan padding ramping `10dp/4dp` pada `bg_hud_control.xml`), bukan banner balok lebar yang memenuhi layar. Saat nonaktif/siaga, di tengah lingkaran tampil ikon mikrofon bersih (`@+id/ivToggleVoiceMic` / `@drawable/ic_mic_24`). Saat mikrofon aktif, ikon mikrofon disembunyikan dan tepat di tengah lingkaran tampil indikator gelombang suara hidup 4-bar (`id.my.yhdev.nexo.ui.LiveWaveformView` / `@+id/liveWaveformView`, `28dp x 20dp`) yang berdenyut 60 FPS mengikuti `onTelemetryReceived(amplitude)` serta berganti warna sesuai status (`ledColor`). Pertahankan atribut wajib `toggleVoice` (`minHeight="48dp"`, `minWidth="48dp"`, `focusable="true"`, `background="@drawable/bg_hud_control"`, `enabled="false"`, `layout_constraintBottom_toBottomOf="parent"`, dan `contentDescription` dinamis) agar tes statis (`test_android_enrollment_ui_static.py`, `test_android_mobile_layout_static.py`) tetap hijau, serta sertakan `liveWaveformView` dan `ivToggleVoiceMic` dalam `pixelShiftRunnable`. Panel `captureControls` (`captureMode`, `startUtterance`, `finishUtterance`) adalah murni *safety-net fallback* (mode walkie-talkie saat Silero VAD tidak tersedia atau pemulihan rekaman lokal 20 detik saat koneksi terputus) dan wajib tetap `View.GONE` selama operasi otomatis normal.
   - **Header Status Atas Tunggal Minimalis (`pillStatus` Micro-Capsule & `pillVoiceprint` Hidden)**: Sembunyikan `pillVoiceprint` (`android:visibility="gone"` di `activity_main.xml` dan pertahankan `pillVoiceprint.visibility = View.GONE` saat bangun dari sleep di `MainActivity.applySleepModeUi`), namun jangan hapus elemen XML-nya agar tes regresi statis tetap menemukan ID tersebut. Tempatkan `pillStatus` sebagai **kapsul mikro tunggal tepat di tengah atas** (`app:layout_constraintStart_toStartOf="parent"`, `app:layout_constraintEnd_toEndOf="parent"`, `app:layout_constraintTop_toTopOf="parent"`, `minHeight="32dp"`, `minWidth="48dp"`, `elevation="2dp"`, `background="@drawable/bg_status_pill"` berlatar kaca gelap `#CC0F1622` dengan border halus `1dp` `#338892B0` dan `radius="999dp"`), dilengkapi micro-LED `7dp` serta teks ringkas `12sp` (`"Menghubungkan…"` / `"Siaga"` / `"Menyimak"`, `singleLine="true"`, `maxLines="1"`, `ellipsize="end"`). Biarkan `visualizerView` membentang penuh dari atas (`app:layout_constraintTop_toTopOf="parent"`), dan jaga tes statis (`test_android_enrollment_ui_static.py`, `test_android_mobile_layout_static.py`) agar mengizinkan `minHeight` `32dp` serta `@drawable/bg_status_pill`.
   - **Pinning ONNX Runtime Android (`onnxruntime-android:1.20.0`) untuk Silero VAD**: Pertahankan `com.microsoft.onnxruntime:onnxruntime-android:1.20.0` yang menjalankan `SileroVad.java` dengan model terpin `assets/vad/silero-v6.2.3.onnx` (`MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"`). Jangan menaikkan ke versi mayor jauh (seperti `1.31.x`) tanpa memperbarui `ort-host-1.20.0.jar`, `onnxruntime-ThirdPartyNotices.txt`, dan memverifikasi kompatibilitas OpSet Silero v6.2.3 karena loncatan runtime C++ berisiko memicu `OrtException: auto_unavailable` serta membengkakkan ukuran APK.
   - **Zero Accidental OFF (User Intent adalah Raja)**: Sekali pengguna menekan `Mulai mikrofon`, status mikrofon **dikunci AKTIF**. Dilarang keras membiarkan kegagalan jaringan sementara (*transient drop*), hening/desau angin, *duration limit* 20 detik, transkripsi kosong (`stt_empty`), atau timeout pemrosesan server memicu cascading `fail()` / `persistOff()` yang mematikan tombol ke posisi OFF dan membuat Nexo menangis.
   - **Silent Auto-Recovery**: Jika internet terputus sesaat, klien tetap berada dalam status `ACTIVE` di UI, melakukan reconnect senyap di latar belakang, dan langsung me-rearm listening loop Silero VAD secara otomatis begitu data pulih. Satu-satunya alasan mikrofon mati adalah jika pengguna mengetuk tombol `Matikan mikrofon` atau aplikasi ditutup.
   - **Toleransi Deadline Negosiasi**: Batas waktu negosiasi izin percakapan (seperti `resumeRequest` di `AudioOwnership`) harus cukup longgar (minimal 15 detik, bukan 3 detik) untuk mengakomodasi jitter dan latensi koneksi seluler outdoor.
4. **Multi-Device Session Architecture & AnswerMirror Compaction Safety**:
   - **Isolasi Sesi Per Perangkat (`nexo-<device_id>`)**: Setiap fisik HP Android memiliki `device_id` unik (`ANDROID_ID`) dan sesi WebSocket terpisah. HP utama (`nexo_appliance_9f72a8160cf886ca`, `owner`, `tablet`, `tablet_01`) terikat ke session kanonis `nexo` (tersinkronisasi dengan terminal lokal via `AnswerMirror` dan ID terikat `local-c5bfcc83...`), sedangkan HP kedua dan perangkat tambahan otomatis dipetakan ke session mandiri `nexo-<clean_device_id>`. `HermesBridge.query()` dan `query_stream()` mengizinkan target yang berawalan `nexo-` (hingga 64 karakter) tanpa memicu penolakan kaku *session locked*.
   - **Mock Call Signature Fallback**: Saat memanggil `hermes.query(..., session_name=...)` atau `hermes.query_stream(..., session_name=...)`, selalu bungkus dengan `try...except TypeError` agar kompatibel dengan mock test atau wrapper yang hanya menerima 1 argumen `(text)`. Tanpa penanganan ini, mock di test suite (seperti `test_outdoor_pipeline.py`) melempar `TypeError: query() got an unexpected keyword argument 'session_name'`, memicu kegagalan hening `hermes_failed` dan timeout pengujian.
   - **Instant Voice Barge-In / Interupsi Alami**: Saat Nexo sedang berbicara (`SPEAKING` / `playbackPending`), sentuhan pada tombol lingkaran `toggleVoice` di Android tidak mematikan mikrofon, melainkan menginterupsi suara asisten seketika (`interruptSpeaking()` $\rightarrow$ `audioPlayer.stop()`), mengirim sinyal kontrol `user_interrupted` / `barge_in` ke server untuk membatalkan `_output_tasks` TTS di server, dan seketika membuka mikrofon kembali (`maybeNextTurn()`).
   - **Compaction Replay Pitfall (`session_store.py` & `AnswerMirror`)**: Saat Hermes melakukan *context compaction* pada sesi `nexo` di `~/.hermes/state.db` (misalnya setelah pergantian/error model atau batas token), Hermes menulis ulang riwayat pesan lama dengan **`id` baru** tetapi **`timestamp` lama**. Jika `HermesAnswerReader.read_new()` hanya memfilter `WHERE id > ?`, `AnswerMirror` akan mengira puluhan pesan lama tersebut sebagai jawaban baru dan membacakannya satu per satu ke speaker HP. Selalu pertahankan filter kesegaran waktu `AND CAST(timestamp AS REAL) >= ?` (`time.time() - 120`) di `read_new()`, dan perbarui skema tabel `messages` pada fixture test agar menyertakan kolom `timestamp REAL`.
   - **Larangan Probe Langsung ke Sesi `nexo` Saat Perangkat Aktif**: Jangan pernah menjalankan `hermes chat -c nexo -q "..."` untuk sekadar tes diagnostik ketika HP pemilik sedang terhubung; balasan asisten yang tersimpan ke `state.db` akan langsung diambil `AnswerMirror` dan disuarakan tiba-tiba di HP pemilik.
5. **Background Resilient Listening & Android MediaSession (Spotify-Style Ongoing & Dynamic Island)**:
   - Pemilik memilih mikrofon tetap menyimak saat aplikasi di-minimize atau layar mati, selama pengguna sendiri belum menekan `Matikan mikrofon`. Saat `keepVoiceAlive == true`, `setUiForeground(false)` tidak boleh memanggil `pauseLocal("background")`; `permitted()` menerima `uiForeground || keepVoiceAlive`. Hanya stop eksplisit (tombol layar, tombol notifikasi `ACTION_STOP_VOICE`, force stop) yang mematikan mic.
   - **Gunakan native `android.media.session.MediaSession` + `Notification.MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0)`**: aktifkan sesi (`isActive = true`, `PlaybackState.STATE_PLAYING`, actions STOP/PAUSE), isi `MediaMetadata` (judul `Nexo Appliance`, artist = status liveness, album art = bitmap maskot sesuai ekspresi), tambahkan `Notification.Action` `Matikan` (`PendingIntent.getService` dengan `ACTION_STOP_VOICE`), `setContentIntent` ke `MainActivity`, `setOngoing(true)`. Nonaktifkan sesi (`STATE_STOPPED`, `isActive=false`) saat mic OFF dan `release()` di `onDestroy`.
   - Efek yang diharapkan di HP pemilik: kartu naik ke media carousel teratas dan fitur kapsul/Dynamic Island OEM (mini capsule di kamera punch-hole) menyala untuk sesi media aktif. Itu perilaku OEM yang dipicu `MediaSession`; belum terverifikasi tanpa uji perangkat, jadi laporkan sebagai ekspektasi, bukan jaminan.
   - Pertahankan `foregroundServiceType="microphone"` + izin `FOREGROUND_SERVICE_MICROPHONE` di manifest (sudah ada). `onStartCommand` harus tetap berbentuk `... : Int = START_NOT_STICKY.also { ... }` karena tes statis mencocokkan polanya; jangan ubah ke blok `return`.
6. **Interaksi Fisika Sentuh Maskot & Sapaan Pintar Kontekstual (Tier 2 Smart Appliance)**:
   - **Interaksi Fisika Sentuh Kenyal (*Tactile Spring & Gaze Tracking*)**: Menyentuh dan menggeser layar pada maskot `VisualizerView` menggerakkan bola mata digital dan kemiringan kepala mengikuti jari pengguna secara real-time (`ACTION_MOVE`), disertai deformasi elastis (*squash & stretch* `touchSquashX/Y`). Saat sentuhan dilepas (`ACTION_UP`), pegas harmonik teredam memantul lembut ke bentuk semula dan memicu ekspresi gemas spontan (`SENANG`, `MENGEDIP`, `TERTAWA`, `JAHIL`, `KEREN`).
   - **Sapaan Pintar Kontekstual Waktu (*Contextual Smart Greeting*)**: Mengenali sapaan salam pengguna berdasarkan jam waktu Indonesia (WIB) tanpa membuang kuota LLM:
     - Pagi (04.00–11.00): *"Selamat pagi Pak Yanto! Semangat berkarya dengan hati hari ini. Ada yang bisa Nexo bantu?"*
     - Siang (11.00–15.00): *"Selamat siang Pak Yanto! Tetap semangat berkarya. Ada yang perlu Nexo siapkan untuk pekerjaan Anda?"*
     - Sore (15.00–18.00): *"Selamat sore Pak Yanto! Bagaimana progres karya hari ini? Nexo siap membantu."*
     - Malam (18.00–04.00): *"Selamat malam Pak Yanto! Semoga hari ini produktif dan berkah. Ada yang bisa Nexo bantu sebelum istirahat?"*
   - **Perintah Briefing Harian & Pantun Motivasi**:
     - *"Briefing hari ini"* / *"Nexo briefing"*: Langsung membacakan hari, tanggal, jam WIB, status kesiapan server Nexo & Hermes, serta menanyakan agenda kerja.
     - *"Pantun hari ini"* / *"Pantun Nexo"*: Membacakan pantun penyemangat kerja: *"Aroma kopi harum memikat, diseduh hangat di atas meja. Awali hari dengan semangat, berkarya dengan hati dalam bekerja!"*
   - **Wake Mode Dual-Path**: Perintah bangun ("bangun", "ayo bangun") saat tidur mengembalikan *"Halo, saya sudah bangun! Ada yang bisa dibantu?"*, sedangkan saat sudah bangun mengonfirmasi *"Saya sudah aktif dan siap menyimak, Pak Yanto!"*.
7. **Audio Ingestion & Hardware AEC**:
   - Periksa source/routing/efek aktual sebelum memilih DSP. `VOICE_COMMUNICATION` ditujukan untuk VoIP dan dapat memakai preprocessing vendor; bandingkan dengan `VOICE_RECOGNITION` atau `UNPROCESSED` yang didukung menggunakan audio HP nyata, bukan menganggap satu source selalu terbaik.
   - Ikat AEC ke sesi `AudioRecord` bila diperlukan dan didukung, tetapi verifikasi hasil akustiknya; instance tersedia atau `enabled=true` bukan bukti gema hilang. Jangan menumpuk noise suppression atau menambah gain untuk menutupi capture yang belum diukur.
   - Buffer Pool: Alokasi statis 3200-byte buffer (100 ms audio pada 16kHz 16-bit mono) untuk mencegah garbage collection (GC) pauses.
8. **Pre-roll Circular Buffer & VAD Sensitivitas Meja Kerja**:
   - `AudioBuffer` berkapasitas 8000 byte (250 ms) menampung audio secara kontinu sehingga konsonan pembuka kata tidak terpotong.
   - Silence window finalisasi ucapan: **`1.0 detik`** (32000 bytes) untuk memastikan pergantian giliran bicara sigap.
9. **Umpan Balik Visual Suara Lokal (Zero-Latency Local VU Meter)**:
   - Klien Android (`NexoKioskService.kt`) menghitung nilai RMS audio lokal secara langsung dari PCM input microphone (`calculateNormalizedRms`).
   - Amplitudo lokal diteruskan seketika ke `visualizerView.setAmplitude(amp)` saat status `LISTENING` atau `IDLE`.
10. **Pipeline STT & TTS**:
   - **STT**: `Faster-Whisper` (CTranslate2) menggunakan model `base`, bahasa Indonesia (`id`), kuantisasi `int8` (CPU) atau `float16` (GPU).
   - **Prompt Echo Filter Hygiene**: Saat menggunakan `initial_prompt`, daftar `prompt_echoes` HANYA boleh berisi kalimat prompt penuh panjang. Dilarang keras memasukkan sapaan umum seperti `"halo nexo"`, `"halo nexo."`, `"nexo"`, atau `"nexo."` ke dalam daftar filter echo.
   - **Resiliensi Turn-Taking**: Hasil `stt_empty` atau `no_speech` dilarang mematikan mikrofon atau memutus sesi ke status error. Klien Android harus tetap berada dalam status listening.
   - **TTS**: `Edge-TTS` menggunakan suara alami pria Indonesia `id-ID-ArdiNeural` dengan streaming per potongan kalimat.
11. **Alokasi Port & Lingkungan Host (Port 8766)**:
   - Server Nexo berjalan pada port baku **`8766`** (`http://127.0.0.1:8766`), dapat dikonfigurasi melalui `server/config.yaml` (`server.port`), environment variable `NEXO_PORT`, dan `NEXO_SERVER_URL`.
12. **Biometrik Vokal & Speaker Verification (CAM++ ONNX)**:
   - Ekstraksi 192-dim normalized embedding vokal via 80-bin Mel Filterbank + CMVN.
   - Invarian Path Biometrik: Selalu resolve path direktori voiceprints via `Path.home() / ".config/nexo-auth/voiceprints"`. Dilarang keras menggunakan fallback `/root/` saat server berjalan sebagai user service unprivileged (`hermesbot`).
13. **Aturan Kompilasi Android & Hygiene Repositori Publik**:
   - ⚠️ **DILARANG KERAS** menjalankan build/kompilasi APK (Gradle `:app:assembleDebug`, dll) di mesin server Linux host. Seluruh pembaruan kode Android hanya di-commit dan di-push ke git. Pemilik ({YH Dev}) akan melakukan `git pull` dan build langsung di Android Studio lokal.
   - **Sinkronisasi Sebelum Edit**: Selalu jalankan `git pull --ff-only origin main` terlebih dahulu sebelum mengedit file Android/dokumentasi, lalu jalankan pytest statis untuk menyelaraskan asersi layout (`test_android_mobile_layout_static.py`, `test_android_enrollment_ui_static.py`).
   - **Hygiene Repositori Publik & .gitignore**: Repositori GitHub Nexo bersifat **publik**. Dilarang keras meng-commit berkas catatan internal, handoff QA, rencana sementara, atau log verifikasi (`*HANDOFF*.md`, `*VERIFICATION*.md`, `*PLAN*.md`, `*REPAIR*.md`, `*SCRATCH*.md`) ke dalam git. Semua aturan ini diblokir di `.gitignore`.

---

## 2. Pipelined Sentence-Chunked Streaming & Penanganan Latensi

### 2.1 Konsep & Pengurangan Time to First Audio (TTFA)
1. Server mengalirkan token jawaban dari Hermes Core secara real-time via `query_stream`.
2. `SentenceChunker` mendeteksi akhir kalimat pertama.
3. Kalimat pertama langsung dilempar ke worker Edge-TTS (`synthesize_stream`) dan frame audio biner pertama langsung dikirimkan ke Android.
4. Klien Android mulai memutar audio kalimat pertama di speaker via `AudioTrack.MODE_STREAM` sementara server masih menerima kalimat berikutnya.

### 2.2 Algoritma SentenceChunker
- Pemisah kalimat utama: `.`, `!`, `?`, atau `\n\n`.
- Pengecualian: Gelar/singkatan (`Dr.`, `Prof.`, `PT.`, `dll.`), nilai angka/desimal (`Rp 50.000`, `3.14`), dan penutup tanda kutip.

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

### 2.4 Parsing dan Penghapusan Otomatis Tag Emosi (`[expr:...]`)
Tag ekspresi dipetakan ke enum `MascotExpression` lalu dihapus dari teks sebelum dikirim ke TTS agar tidak dilafalkan secara verbal.

### 2.5 Durasi PCM & Echo Guard
- Kontrak PCM16 mono 24 kHz: `len(chunk) / 48000` detik.
- Echo Guard 600 ms: Menahan aktivasi mikrofon baru hingga pemutaran selesai ditambah jeda pengaman 600 ms untuk mencegah feedback loop.

---

## 3. Fitur Smart Screen Kios 24/7: Ambient Sleep Mode, Pixel Shifting, & Earcons

### 3.1 Sinyal Audio Lokal Instan (EarconManager)
- Listening Chime (E5 -> B5, 320 ms) saat mulai menyimak.
- Thinking Chime (A5 -> D6, 180 ms) saat pengguna selesai bicara.

### 3.2 Mode Tidur Ambien & Pixel Shifting
- Transisi otomatis ke `sleeping` setelah 5 menit inaktif.
- Pixel shifting periodik ±2..±6 px untuk mencegah burn-in OLED/IPS.

### 3.3 Otomasi Studio Smart IoT & Telemetri Server (Tier 3)
Nexo menyediakan perintah suara instan dan REST API untuk kontrol smart home/studio tanpa latensi LLM:
- **Perintah Telemetri Server**: *"Cek status server"*, *"Berapa suhu server"*, *"Kondisi server"*, *"Penggunaan memori / CPU"* langsung membaca metrik Linux nyata (`/proc/loadavg`, `/proc/meminfo`, `/proc/uptime`, sensor thermal) dan merespons verbal secara instan (< 50ms) dengan ekspresi `KEREN`.
- **Perintah Mode Studio / Smart Scene**:
  - *"Mode fokus"* / *"Nexo aktifkan mode fokus"*: Mengatur suasana konsentrasi kerja, ekspresi `KEREN`, dan menyiarkan event `{ "type": "iot_control", "action": "focus_mode" }`.
  - *"Mode santai"* / *"Mode istirahat"*: Mengatur suasana hangat dan rileks, ekspresi `SENANG`, dan menyiarkan event `{ "type": "iot_control", "action": "relax_mode" }`.
  - *"Nyalakan lampu studio dan pasang mode fokus"*: Mengaktifkan perangkat lampu dan scene kerja secara bersamaan.
- **REST Endpoints & Webhook**:
  - `GET /iot/status`: Metrik server (CPU load, RAM, disk, uptime, suhu) dan status perangkat IoT.
  - `POST /iot/control`: Menerima webhook perintah kontrol dari Home Assistant / MQTT (`action`, `scene`, `device`, `state`, `speak`).
- **CLI Utility**:
  - `scripts/nexo iot status`: Memeriksa telemetri server dan status smart appliance langsung dari terminal.
  - `scripts/nexo iot focus_mode`: Mengaktifkan mode fokus studio dari terminal.

---

## 4. Panduan Verifikasi Cepat

Untuk memverifikasi seluruh komponen backend dan invariant statis Android:
```bash
cd ~/workspace/projects/Nexo
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests -q -o asyncio_default_fixture_loop_scope=function
```
Restart server setelah perubahan:
```bash
systemctl --user restart nexo-appliance.service
systemctl --user status nexo-appliance.service
curl -fsS http://127.0.0.1:8766/health
```
