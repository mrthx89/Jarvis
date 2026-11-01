# WebSocket Communication Protocol Specification
## Project: Nexo Dedicated Appliance (formerly Nexo)
**Protocol Version**: 1.1.0  
**Transport**: WebSocket (RFC 6455)  
**Encoding**: JSON (Control/Telemetry/Cognitive) + Binary (Raw PCM Audio / MP3 Stream)  

---

### 1. Model Komunikasi

Satu koneksi WebSocket full-duplex melayani dua jenis data:
1. **Text Frames (JSON)**: Digunakan untuk negosiasi handshake, perubahan status sistem (`state_change`), ekspresi maskot, telemetri amplitudo/visual, stream kognitif inkremental (`cognitive`), dan transkripsi teks (`transcription`).
2. **Binary Frames**:
   - **Inbound (Client -> Server)**: Raw PCM16 little-endian 16 kHz mono untuk audio rekaman suara pengguna.
   - **Outbound (Server -> Client)**: Streaming audio balasan TTS (Edge-TTS MP3 / PCM frames) yang disintesis secara berkesinambungan per potongan kalimat (*sentence-chunked streaming*).

---

### 2. Handshake & Inisialisasi

Stage 1 menggunakan token unik per perangkat yang dibuat pemilik (bukan pairing otomatis).
Sebelum WebSocket diterima, request upgrade ke `/ws/appliance` wajib membawa:

```text
Authorization: Bearer ***
X-Device-ID: <ID-yang-diprovisikan>
```

Token tidak boleh dikirim melalui URL/query, Remote Config, APK literal, atau log. Server
membaca registry JSON eksternal mode 0600 berisi SHA-256 saja; konfigurasi kosong,
registry hilang/rusak, ID/token salah ditolak sebelum `accept` (HTTP 403).
Android menyimpan token terenkripsi Android Keystore dan mengikatnya ke origin WSS
(host+port) serta ID; perubahan origin memerlukan persetujuan/setup pemilik baru.
HTTP 401/403 atau close 1008 menghentikan reconnect otomatis dan menampilkan setup.

Setelah upgrade berhasil, hello wajib menjadi frame pertama dalam 5 detik (dapat
dikonfigurasi). `device_id` harus sama dengan header terautentikasi, versi wajib `1.0` atau `1.1`,
dan `audio_spec` wajib lengkap persis PCM16 little-endian 16 kHz mono seperti di bawah.
Tidak ada PCM/VAD/STT/Hermes sebelum hello valid. JSON non-object, format salah,
duplikat hello, atau PCM sebelum hello ditutup dengan kode 1008.
Batas default: 4096 byte per JSON dan 4 sesi simultan per proses; kapasitas penuh
mengembalikan 503 (atau penolakan ASGI generik tanpa extension). Batas frame transport
64 KB dan antrean 4 frame diterapkan oleh entrypoint server; konfigurasi launch eksternal
harus mempertahankannya. Lihat [DEVICE_AUTH.md](DEVICE_AUTH.md) untuk migrasi/revoke.

Klien kemudian mengirim handshake:

#### 2.1 Client Hello (Client -> Server)
```json
{
  "type": "client_hello",
  "protocol_version": "1.0",
  "device_id": "nexo-tablet-01",
  "audio_spec": {
    "format": "audio/pcm",
    "sample_rate": 16000,
    "channels": 1,
    "bit_depth": 16
  }
}
```

#### 2.2 Server Hello (Server -> Client)
```json
{
  "type": "server_hello",
  "session_id": "sess_20261101_8a9f02",
  "agent_name": "Nexo",
  "status": "IDLE"
}
```

---

### 3. Kendali Aliran Audio (Streaming Control)

#### 3.1 Jalur Utama: PCM Kontinu, Circular Pre-roll Buffer, & Histeresis VAD
Setelah `server_hello`, Android mengaktifkan `AudioRecord` dan mengirim PCM16 little-endian, 16 kHz mono (3200 byte = 100 ms).
Pada server, audio inbound diproses dengan arsitektur ketahanan:
1. **Circular Pre-roll Ring Buffer**: Server memelihara ring buffer sirkular sebesar ~500 ms (5 frame @ 100 ms). Saat suara pengguna melewati ambang batas onset VAD (-35 dBFS), seluruh buffer pre-roll ikut dimasukkan ke dalam sesi transkripsi. Ini mencegah terpotongnya konsonan awal kata pertama (*initial phoneme clipping*).
2. **VAD Histeresis Berjenjang**:
   - **Onset Threshold**: -35 dBFS untuk mendeteksi awal ucapan dari kondisi hening.
   - **Continuation Threshold**: -43 dBFS untuk mempertahankan ucapan bersuara lirih atau suku kata akhir tanpa terputus prematur.
   - **Silence Window**: 1.5 detik keheningan berkelanjutan sebelum menandai akhir ucapan (*speech finalized*).
   - **Maksimum Rekaman**: Dibatasi 30 detik untuk mencegah buffer overflow.

Percakapan beroperasi dalam model **half-duplex terkoordinasi**, bukan barge-in:
- Masukan audio mikrofon ditahan (*muted/gated*) selama fase `THINKING` dan `SPEAKING`.
- Perlindungan diperkuat dengan perhitungan durasi frame MP3 secara akurat di server (`_playback_until`) dan penambahan jeda pelindung gema (*echo guard*) 600 ms setelah seluruh audio selesai diputar oleh speaker sebelum status kembali ke `IDLE`.
- Klien Android secara independen memantau pengosongan buffer `AudioTrack` (*drain*) sebelum membuka kembali gerbang mikrofon.

#### 3.2 Jalur Kompatibilitas: Kendali Eksplisit VAD
Jika klien mengaktifkan VAD lokal:
```json
{
  "type": "voice_start",
  "timestamp": 1793500800120
}
```
Segera setelah pesan ini, klien mulai mengirimkan **Binary Frames** berisi potongan PCM 16-bit 16kHz mono (panjang 3200 bytes per chunk ~ 100ms).

Ketika hening terdeteksi melebihi ambang batas (misal 800ms):
```json
{
  "type": "voice_end",
  "timestamp": 1793500803450
}
```

---

### 4. Siklus Status Sistem, Telemetri Visual & Stream Kognitif

#### 4.1 Perubahan Status (State Change) & Ekspresi Maskot (Server -> Client)
Mengubah mode status sistem dan ekspresi wajah digital maskot Nexo:
```json
{
  "type": "state_change",
  "state": "LISTENING" | "THINKING" | "SPEAKING" | "IDLE" | "ERROR" | "SLEEPING",
  "message": "Opsional deskripsi status",
  "expression": "netral" | "senang" | "sangat_senang" | "tertawa" | "bingung" | "mengantuk" | "mengedip" | "terkejut" | "cinta" | "jahil" | "keren" | "malu" | "marah" | "menangis" | "sedih" | "takut" | "sleeping"
}
```

Daftar status sistem (`state`):
- `LISTENING`: Mikrofon aktif menyimak ucapan pengguna (disertai earcon pembuka).
- `THINKING`: Server sedang mentranskripsi suara (STT) dan memproses penalaran kognitif (Hermes Agent).
- `SPEAKING`: Server sedang menyintesis dan memutar audio jawaban (TTS).
- `IDLE`: Sistem siaga dan siap menerima interaksi berikutnya.
- `ERROR`: Terjadi kesalahan pada pipeline atau koneksi.
- `SLEEPING`: Mode tidur hemat visual setelah periode inaktivitas panjang (*ambient sleep mode*).

#### 4.2 Telemetri Amplitudo & Frekuensi (Server -> Client)
Dikirim secara berkala (setiap 30-50ms) saat Nexo sedang berbicara untuk menggerakkan visualizer secara dinamis:
```json
{
  "type": "visual_telemetry",
  "amplitude": 0.78,
  "frequency_bands": [0.12, 0.45, 0.88, 0.65, 0.30]
}
```

#### 4.3 Transkripsi Teks (Subtitles & History) (Server -> Client)
Menampilkan transkripsi final ucapan pengguna atau jawaban final asisten di layar dan broadcast subscriber:
```json
{
  "type": "transcription",
  "role": "user" | "assistant",
  "text": "Selamat pagi, bagaimana cuaca hari ini?",
  "is_final": true,
  "expression": "senang",
  "source": "appliance_voice" | "nexo_hermes"
}
```

##### Parsing & Stripping Tag Emosi (`[expr:...]`)
Hermes Agent dapat menyisipkan markup emosi seperti `[expr:senang]`, `[expr:berpikir]`, `[expr:kaget]`, dsb.
- Server secara otomatis mem-parsing ekspresi emosi pertama yang valid dan memperbarui ekspresi maskot pada `state_change` dan `cognitive`.
- Seluruh tag `[expr:...]` otomatis dihapus (*stripped*) dari teks transkripsi dan teks yang dikirim ke sintesis suara TTS, sehingga mesin TTS tidak akan pernah melafalkan tag markup tersebut.

#### 4.4 Stream Kognitif Real-Time (`cognitive`) (Server -> Client & Subscribers)
Frame baru untuk sentence-chunked streaming dan pengamatan kognitif real-time:
```json
{
  "type": "cognitive",
  "stage": "streaming" | "thought",
  "sentence": "Halo, saya siap membantu pekerjaan Anda hari ini.",
  "content": "Halo, saya siap membantu pekerjaan Anda hari ini.",
  "index": 0,
  "expression": "senang"
}
```
- `stage`: `"streaming"` untuk kalimat yang sedang dialirkan ke TTS, atau `"thought"` untuk penalaran internal.
- `sentence` / `content`: Teks potongan kalimat bersih (tanpa tag emosi).
- `index`: Urutan indeks kalimat dalam respons (0-indexed).
- `expression`: Ekspresi maskot yang terasosiasi dengan kalimat tersebut.

---

### 5. Pengiriman Audio Balasan (Sentence-Chunked Streaming TTS)

Alih-alih menahan seluruh respons hingga penalaran selesai (*blocking*), Nexo menerapkan **Sentence-Chunked Streaming**:
1. Token teks dari Hermes Agent dialirkan secara real-time ke `SentenceChunker`.
2. Segera setelah satu kalimat lengkap terbentuk (dibatasi oleh tanda baca `.`, `!`, `?`, atau jeda baris):
   - Server mengirim `TTSStart` jika belum dimulai.
   - Server memancarkan event `cognitive` (stage `streaming`).
   - Server langsung menyalurkan kalimat tersebut ke pipeline `synthesize_stream` Edge-TTS.
3. Potongan biner audio hasil sintesis segera dikirimkan ke Android melalui WebSocket biner bersamaan dengan telemetri visual `visual_telemetry`.
4. Durasi setiap potongan dihitung presisi via MPEG frame header (`parse_audio_duration`) untuk memperpanjang nilai `_playback_until`.

#### 5.1 Permulaan Sintesis Suara
```json
{
  "type": "tts_start",
  "format": "audio/mp3",
  "sample_rate": 24000,
  "channels": 1
}
```
Diikuti oleh **Binary Frames** potongan audio MP3 dari server ke klien. Klien langsung memutar potongan audio ini secara berkesinambungan melalui ring buffer pemutar audio.

#### 5.2 Pengakhiran Sintesis Suara
```json
{
  "type": "tts_end"
}
```
Setelah menerima `tts_end` dan buffer pemutaran pada peranti keras speaker benar-benar kosong, klien kembali ke status `IDLE`. Server menunggu hingga `_playback_until + ECHO_GUARD_SECONDS (0.6 detik)` tercapai sebelum membuka kembali gerbang mikrofon.

---

### 6. Heartbeat & Deteksi Putus Jaringan

- Klien mengirim frame Ping setiap 15 detik:
```json
{
  "type": "ping",
  "timestamp": 1793500815000
}
```
- Server membalas dengan Pong:
```json
{
  "type": "pong",
  "timestamp": 1793500815015
}
```
- Jika dalam 30 detik tidak ada balasan Pong, klien menganggap koneksi terputus dan memulai siklus rekoneksi otomatis (exponential backoff: 1s, 2s, 4s, 8s, maks 30s).

---

### 7. Sinyal Audio Klien (Earcons) & Perlindungan Layar Kios

1. **Audio Earcons Lokal**:
   - Saat status berpindah ke `LISTENING`: Klien memainkan nada pembuka `earcon_listening.wav` (dua nada berpadu E5 -> B5, frekuensi 659.25 Hz & 987.77 Hz).
   - Saat status berpindah ke `THINKING`: Klien memainkan nada fokus `earcon_thinking.wav` (nada A5 -> D6, frekuensi 880 Hz & 1174.66 Hz).
   - Selama earcon diputar, `HalfDuplexGate` menahan pembacaan mikrofon untuk mencegah echo feedback.
2. **Ambient Sleep Mode & Pixel Shifting**:
   - Jika tidak ada interaksi suara atau aktivitas selama durasi ambang batas tertentu, aplikasi beralih ke ekspresi `sleeping` (`mascot_sleeping.xml`).
   - `VisualizerView` menerapkan pergeseran piksel halus (*pixel shifting*) periodik pada bidang kanvas untuk melindungi layar AMOLED/OLED tablet dari risiko *screen burn-in* dalam pengoperasian kios 24/7.
