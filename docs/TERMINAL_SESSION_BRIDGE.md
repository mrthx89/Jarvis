# Terminal & Local Session Bridge ke Nexo Appliance

Dokumentasi ini menjelaskan mekanisme bridging dua arah antara sesi terminal lokal Hermes dengan **Nexo Dedicated Appliance** (layanan server Nexo dan perangkat fisik Android/smart screen).

---

## 1. Sesi Nexo Terkunci dan Pembacaan Otomatis

Server sekarang mengunci appliance ke satu ID percakapan Hermes bernama `nexo`.
Nama saja tidak digunakan untuk memilih ulang percakapan setiap turn. Saat pertama
menyala, server mencari tepat satu sesi lokal bernama `nexo`, lalu menyimpan ID
kanonis di `~/.config/nexo/session_binding.json` (mode 0600). Binding berbeda,
sesi hilang, atau nama ambigu membuat startup gagal tertutup, bukan berpindah ke
sesi lain. Buat sesi `nexo` di Hermes sebelum deployment baru.

Masuk ke sesi yang sama di terminal dengan `hermes chat -c nexo`; untuk memastikan
identitas saat ada nama duplikat, gunakan `hermes chat -c <hermes_session_id>` dari
`GET /session/status`. Jawaban final baru yang tersimpan dalam sesi tersebut
dibaca server secara read-only dan diteruskan ke subtitle serta TTS appliance.
Tidak ada pemanggilan model kedua dan tidak perlu `--speak` pada terminal Hermes
biasa. Pesan tool, commentary, reasoning, sesi lain, dan riwayat sebelum startup
tidak dibacakan. Jawaban dari voice/server-send dideduplikasi karena jalur itu
sudah menguasai outputnya sendiri.

Appliance harus terhubung dan voice ON. Saat sibuk, jawaban terminal menunggu
maksimal 120 detik dalam antrean terbatas (20 jawaban); saat Stop/disconnect,
antrean dibuang dan tidak diputar ulang saat Start. Server mengirim audio tetapi
tidak memiliki konfirmasi bahwa speaker fisik benar-benar terdengar.

Pengaturan `hermes.session_id`, `hermes.hermes_home`, dan
`hermes.session_binding_path` tersedia di konfigurasi aplikasi server. Gunakan
home Hermes yang sama untuk terminal dan service; tidak perlu mengubah Hermes
core ataupun profil lain. Binding persisten tidak dapat diganti melalui API.
Ini isolasi routing dan output, bukan sandbox hak akses tool Hermes.

### Ikhtisar & Arsitektur

Mekanisme bridging ini menghubungkan tiga komponen utama:
1. **Sesi Terminal Lokal**: Operator atau agen pengembang yang menjalankan sesi terminal di VPS/server lokal.
2. **Nexo Gateway & SessionHub**: Layanan server (`nexo_server`) yang mengelola koneksi WebSocket appliance, cognitive event stream, serta orkestrasi pesan ke Hermes.
3. **Appliance Fisik**: Layar pintar / tablet Android yang menampilkan ekspresi maskot, memancarkan suara audio (TTS), serta menerima ucapan mikrofon pengguna (STT).

```text
┌─────────────────────────────────┐        WebSocket / SSE       ┌──────────────────────────────┐
│  Terminal Lokal Hermes (CLI)    │ ◄──────────────────────────► │  Nexo Server (FastAPI / Hub) │
│  - nexo session send <pesan>    │       HTTP / JSON Stream     │  - SessionHub                │
│  - nexo session watch           │                              │  - Hermes Agent Bridge       │
│  - nexo session bridge          │                              └──────────────┬───────────────┘
└─────────────────────────────────┘                                             │
                                                                   WebSocket    │ Audio PCM
                                                                   Full-Duplex  │ Telemetri JSON
                                                                                ▼
                                                                 ┌──────────────────────────────┐
                                                                 │  Nexo Appliance (Android)    │
                                                                 │  - Layar Maskot & Visualizer │
                                                                 │  - Speaker & Mikrofon        │
                                                                 └──────────────────────────────┘
```

Jika server Nexo sedang offline atau belum dijalankan, utilitas CLI memiliki **Standalone Fallback** cerdas: perintah teks langsung diarahkan ke thread Hermes CLI yang bersangkutan (`hermes chat -c <thread>`).

---

## 2. Pemasangan & Persiapan CLI

### Masuk terminal ke sesi appliance

Gunakan home Hermes yang sama dengan layanan, lalu buka sesi bernama `nexo`:

```bash
HERMES_HOME=/data/home/.hermes hermes chat -c nexo
```

Nama terminal seperti `nexo-app` adalah sesi berbeda dan tidak ikut dibacakan.
Jika nama `nexo` menjadi ambigu, ambil `hermes_session_id` dari
`http://127.0.0.1:8765/session/status`, lalu gunakan `hermes chat -c <ID>`.
Jangan memakai `--create-if-missing` untuk memulihkan binding yang hilang:
server sengaja menolak berpindah ke percakapan pengganti.

Source observer baru memerlukan restart `nexo-appliance.service` agar dimuat.
Verifikasi `/session/status` memuat `session_locked: true` dan ID yang sama
setelah restart. Permintaan `/session/link` ke nama lain harus menghasilkan
HTTP 409; pengujian ini tidak memanggil model. Jangan menguji dengan nested
query ke percakapan yang sedang memproses turn.

Pengujian integrasi tanpa perangkat:

```bash
PYTHONPATH=server/src server/.venv/bin/python -m pytest server/tests/test_session_delivery_integration.py server/tests/test_session_store.py server/tests/test_answer_mirror.py server/tests/test_session_isolation.py -q
```

Tes ini memakai database sementara, socket perekam dan provider deterministik.
Ia membuktikan routing, isolasi, subtitle dan deduplikasi di server, bukan suara
speaker atau clipboard fisik Android. Jangan menyisipkan jawaban palsu ke
`state.db` produksi untuk memicu mirror.

Utilitas CLI tersedia melalui executable wrapper `nexo` (atau langsung `scripts/nexo_session.py`).

### Tambahkan ke PATH (Opsional)
```bash
ln -sf /data/home/Nexo/scripts/nexo /root/.local/bin/nexo
```

Pastikan executable memiliki izin eksekusi:
```bash
chmod +x /data/home/Nexo/scripts/nexo /data/home/Nexo/scripts/nexo_session.py
```

---

## 3. Perintah CLI Utama (`nexo session`)

Perintah dapat dipanggil dengan format `nexo session <subcommand>` atau langsung `nexo <subcommand>`.

### A. Memeriksa Status (`nexo status`)
Menampilkan status ketersediaan server Nexo, status koneksi appliance fisik, device ID, state maskot, dan session thread Hermes yang sedang ditautkan.
```bash
nexo status
```

### B. Mengirim Pesan / Instruksi (`nexo send`)
Mengirim instruksi teks ke sesi Nexo. Respons penalaran dari Hermes akan dicetak di terminal.
```bash
# Kirim pesan teks standar
nexo send "Halo Nexo, bagaimana cuaca hari ini?"

# Kirim pesan teks sekaligus suarakan jawaban di speaker appliance
nexo send "Ingatkan saya untuk rapat jam 4 sore" -s

# Target hanya nexo; sesi lain ditolak server
nexo send "Analisis commit terbaru repositori" -c nexo -s
```

### C. Menyimak Cognitive Stream & Transkrip Real-Time (`nexo watch`)
Menyambung ke stream event kognitif Nexo secara langsung melalui WebSocket. Anda dapat menyimak apa yang diucapkan pengguna pada appliance, proses penalaran Hermes (*thinking*), serta jawaban asisten secara live.
```bash
# Menyimak transkrip dan perubahan state
nexo watch

# Menyimak dengan visualisasi level audio / telemetri suara
nexo watch --telemetry
```

### D. Membaca Riwayat Transkrip Sesi (`nexo transcript`)
Mengambil transkrip percakapan sesi (baik dari memori server maupun langsung diekspor dari database sesi Hermes).
```bash
# Membaca 10 percakapan terakhir
nexo transcript -n 10

# Membaca transkrip dari thread tertentu
nexo transcript -c "Implementasi Terminal & Lo"

# Output dalam format raw JSON (cocok untuk piping ke jq atau script)
nexo transcript --json
```

### E. Mengaitkan Session Thread Hermes (`nexo link`)
Server mengunci thread ke `nexo`. Perintah link hanya idempoten untuk nama tersebut; perpindahan ke thread lain dan `--latest` yang menunjuk sesi lain ditolak.
```bash
# Lihat thread Hermes yang sedang ditautkan
nexo link

# Konfirmasi thread yang sudah terkunci
nexo link nexo
```
Preferensi CLI lama tidak dapat mengganti binding kanonis server di `~/.config/nexo/session_binding.json`.

### F. Memicu Suara / Audio Langsung di Appliance (`nexo speak`)
Mengarahkan teks langsung ke mesin TTS untuk disuarakan pada speaker appliance, sekaligus menggerakkan animasi maskot.
```bash
# Suarakan teks dengan ekspresi senang
nexo speak "Selamat pagi, sistem siap digunakan!" -e senang

# Suarakan teks dengan ekspresi tertawa
nexo speak "Hahaha, lelucon itu sangat lucu." -e tertawa
```

### G. Mode Bridge Terminal Interaktif Dua Arah (`nexo bridge`)
Membuka sesi konsol interaktif real-time di mana Anda dapat mengetik pesan sambil terus menyimak apa yang terjadi di appliance secara simultan.
```bash
# Buka mode bridge
nexo bridge

# Buka mode bridge dengan audio respons appliance selalu aktif
nexo bridge -s
```

Dalam mode bridge:
- Ketik teks biasa untuk mengirim pesan ke Nexo.
- `/speak` : Toggle aktivasi respons suara di speaker appliance.
- `/link nexo` : Konfirmasi thread; nama lain ditolak.
- `/status` : Cek status appliance dan server.
- `/quit` atau `exit` : Keluar dari mode bridge.

---

## 4. REST & WebSocket API Endpoints

Server Nexo menyediakan endpoint terpadu untuk integrasi programmatic:

| Endpoint | Method | Deskripsi |
|---|---|---|
| `/session/status` | `GET` | Status server, appliance terhubung, dan session thread. |
| `/session/send` | `POST` | Injeksi pesan teks (`{"message": "...", "speak": bool, "session_name": "..."}`). |
| `/session/speak` | `POST` | Memicu TTS di speaker appliance (`{"text": "...", "expression": "..."}`). |
| `/session/link` | `POST` | Konfirmasi `nexo`; sesi lain HTTP 409. |
| `/session/transcript` | `GET` | Mengambil riwayat transkrip percakapan (`?limit=50`). |
| `/session/stream` | `GET` | SSE (Server-Sent Events) cognitive stream. |
| `/ws/session/stream` | `WebSocket` | Full-duplex WebSocket stream untuk event & instruksi dua arah. |

---

## 5. Ringkasan Praktis Integrasi

- **Penyatuan Konteks**: Masuk ke sesi Hermes `nexo` yang ID-nya ditampilkan server. Server tidak mengikuti sesi terminal lain yang paling baru aktif.
- **Monitoring Tanpa Gangguan**: Gunakan `nexo watch` di jendela tmux/terminal terpisah untuk mengamati respon appliance secara pasif.
- **Remote Announcement**: Gunakan `nexo speak "Build selesai!"` di akhir CI/CD pipeline atau build script Anda untuk memberi notifikasi suara nyata di meja kerja.
