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

### 3.3 Pendaftaran sidik suara melalui sentuhan

Kontrol ini memakai otoritas koneksi WebSocket yang sudah terautentikasi dan selesai
hello. Ucapan seperti “daftarkan suara saya” atau “hapus sidik suara” hanya membuka
UI (`action=enroll` atau `manage`); ucapan tersebut **tidak** mendaftarkan,
menimpa, menghapus, atau mengonfirmasi profil. Mulai rekaman dan konfirmasi hapus
harus berasal dari sentuhan eksplisit di UI.

| Client control | Server `voiceprint_dialog.action` | Makna |
| --- | --- | --- |
| `voiceprint_query` | `query` | Status profil dan kesiapan model saat ini |
| `voiceprint_enroll_start` + `request_id` | `recording` atau `error` | Mulai capture khusus setelah diterima server |
| `voiceprint_enroll_finish` + ID yang sama | `enrolled` atau `error` | Ekstraksi model dan penyimpanan hasil, bukan tanda berhasil optimistis |
| `voiceprint_enroll_cancel` + ID yang sama | `cancelled` | Hapus capture dan batalkan commit yang belum terjadi |
| `voiceprint_unenroll` + `request_id` | `unenroll` | Hasil hapus setelah konfirmasi sentuh |

`request_id` adalah string non-kosong sepanjang 1–128 karakter. Query boleh tanpa
ID. Gunakan ID baru untuk setiap capture: ID capture yang telah diterima tidak
boleh dipakai ulang selama koneksi itu. Maksimal 1024 capture per koneksi, setelah
itu sambungkan ulang. Kontrol dengan ID usang tidak mengubah capture yang baru.
ID tidak valid adalah pelanggaran protokol.

Contoh acknowledgment (field baru mempunyai default agar pesan lama tetap dapat dibaca):

```json
{
  "type": "voiceprint_dialog",
  "action": "recording",
  "request_id": "capture-unique-id",
  "success": null,
  "available": true,
  "is_enrolled": false,
  "metadata": null,
  "message": "Bicara selama 2 hingga 6 detik."
}
```

- `available` berarti sesi ONNX sungguhan siap, bukan sekadar modul dikonfigurasi.
  Model hilang/gagal dimuat menolak enrollment; tidak ada embedding tiruan produksi.
- `success=null` untuk query, intent UI, dan acknowledgment `recording`;
  hasil enrollment/hapus memakai `true` atau `false`. Cancel memakai `false`.
  `is_enrolled` dan `metadata` selalu dibaca dari profil saat respons dikirim,
  termasuk ketika gagal hapus (jangan menganggap profil sudah kosong).
- Klien menahan PCM percakapan selama start pending, lalu hanya mengirim sampel
  enrollment setelah `recording` dengan ID yang cocok. Selama capture maupun
  ekstraksi, PCM **tidak** masuk VAD percakapan, pre-roll, STT, Hermes, atau TTS.
  `voice_start`/`voice_end` tidak mengakhiri capture enrollment.
- PCM16 16 kHz mono: minimal 64000 byte (2 detik), maksimal 192000 byte
  (6 detik). Frame tetap maksimal 64000 byte dan harus berjumlah byte genap.
  Kelebihan PCM dibuang, tidak memicu commit otomatis. Finish eksplisit wajib.
- Deadline 15 detik sejak start mencakup rekaman dan ekstraksi. Cancel, timeout,
  atau disconnect membersihkan buffer. Ekstraksi yang terlambat tidak dapat
  menyimpan profil atau memengaruhi turn berikutnya; start baru ditolak selama
  worker ekstraksi sebelumnya belum berhenti. Profil lama tetap aktif sampai
  embedding valid berhasil disimpan dengan replace, tanpa penghapusan awal.
- Start/hapus ditolak saat sesi sibuk, sedang merekam percakapan, berbicara,
  atau masih dalam echo guard. Enrollment tidak menghasilkan balasan TTS.
  Klien menghentikan capture dan menahan PCM enrollment sesudah cancel/finish;
  hanya kembali ke percakapan setelah hasil/ack yang sesuai.

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
