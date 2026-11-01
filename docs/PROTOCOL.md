# WebSocket Communication Protocol Specification
## Project: Nexo Dedicated Appliance (formerly Jarvis)
**Protocol Version**: 1.0.0  
**Transport**: WebSocket (RFC 6455)  
**Encoding**: JSON (Control/Telemetry) + Binary (Raw PCM Audio)  

---

### 1. Model Komunikasi

Satu koneksi WebSocket full-duplex melayani dua jenis data:
1. **Text Frames (JSON)**: Digunakan untuk negosiasi handshake, perubahan status sistem, telemetri amplitudo/visual, dan transkripsi teks.
2. **Binary Frames (Raw PCM16 little-endian)**: Digunakan untuk streaming audio pengguna dari Android ke Server, serta streaming audio balasan dari Server ke Android.

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
dikonfigurasi). `device_id` harus sama dengan header terautentikasi, versi wajib `1.0`,
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

#### 3.1 Jalur utama: PCM kontinu dan deteksi akhir ucapan di server

Setelah `server_hello`, Android mengaktifkan `AudioRecord` dan mengirim PCM16 little-endian, 16 kHz mono (3200 byte = 100 ms). Server mendeteksi awal suara, menyimpan pre-roll terbatas, dan menutup ucapan setelah hening atau durasi maksimum. Klien tidak wajib mengirim `voice_start`/`voice_end`.

Percakapan saat ini **half-duplex**, bukan barge-in: masukan ditahan selama THINKING dan SPEAKING, dan baru dilanjutkan setelah buffer speaker selesai. Tujuannya mencegah jawaban speaker direkam sebagai pertanyaan berikutnya. Ini belum wake-word; pembicaraan di sekitar perangkat dapat memicu pengiriman ucapan.

#### 3.2 Jalur kompatibilitas: kendali eksplisit
Jika klien memiliki VAD lokal, pesan berikut tetap didukung:

```json
{
  "type": "voice_start",
  "timestamp": 1793500800120
}
```
Segera setelah pesan ini, klien mulai mengirimkan **Binary Frames** berisi potongan PCM 16-bit 16kHz mono (panjang 3200 bytes per chunk ~ 100ms).

#### 3.2 Deteksi Suara Pengguna Selesai (Client -> Server)
Ketika hening terdeteksi melebihi ambang batas (misal 800ms):
```json
{
  "type": "voice_end",
  "timestamp": 1793500803450
}
```

---

### 4. Siklus Status Sistem & Telemetri Visual (Server -> Client)

#### 4.1 Perubahan Status (State Change)
Mengubah mode ekspresi wajah digital Nexo:
```json
{
  "type": "state_change",
  "state": "LISTENING" | "THINKING" | "SPEAKING" | "IDLE" | "ERROR",
  "message": "Opsional deskripsi status"
}
```

#### 4.2 Telemetri Amplitudo & Frekuensi (Server -> Client)
Dikirim secara berkala (setiap 30-50ms) saat Nexo sedang berbicara untuk menggerakkan visualizer secara akurat:
```json
{
  "type": "visual_telemetry",
  "amplitude": 0.78,
  "frequency_bands": [0.12, 0.45, 0.88, 0.65, 0.30]
}
```

#### 4.3 Transkripsi Teks (Subtitles) (Server -> Client)
Opsional: Menampilkan teks ucapan atau jawaban di layar:
```json
{
  "type": "transcription",
  "role": "user" | "assistant",
  "text": "Selamat pagi, bagaimana cuaca hari ini?",
  "is_final": true
}
```

---

### 5. Pengiriman Audio Balasan (TTS Audio Stream)

#### 5.1 Permulaan Sintesis Suara
```json
{
  "type": "tts_start",
  "format": "audio/pcm",
  "sample_rate": 24000,
  "channels": 1
}
```
Diikuti oleh **Binary Frames** audio dari server ke klien. Klien langsung memutar potongan audio ini secara berkesinambungan ke speaker.

#### 5.2 Pengakhiran Sintesis Suara
```json
{
  "type": "tts_end"
}
```
Setelah menerima `tts_end` dan buffer pemutaran habis, klien beralih kembali ke status `IDLE`.

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
- Jika dalam 30 detik tidak ada balasan Pong, klien menganggap koneksi terputus dan memulai siklus rekoneksi otomatis.
