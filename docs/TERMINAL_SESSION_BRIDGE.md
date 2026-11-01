# Terminal & Local Session Bridge ke Nexo Appliance

Dokumentasi ini menjelaskan mekanisme bridging dua arah antara sesi terminal lokal Hermes dengan **Nexo Dedicated Appliance** (layanan server Nexo dan perangkat fisik Android/smart screen).

---

## 1. Ikhtisar & Arsitektur

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

# Kirim pesan ke session thread Hermes tertentu
nexo send "Analisis commit terbaru repositori" -c "coding-session"
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
Secara default, Nexo menggunakan session thread Hermes `nexo` (atau fallback legacy `appliance`). Perintah ini memungkinkan Anda menautkan sesi aktif ke thread terminal Anda, sehingga percakapan di appliance dan terminal menyatu dalam satu memori konteks yang sama.
```bash
# Lihat thread Hermes yang sedang ditautkan
nexo link

# Tautkan secara otomatis ke sesi Hermes lokal yang paling baru aktif
nexo link --latest

# Tautkan secara manual ke nama thread spesifik
nexo link "proyek-nexo"
```
Tautan ini disimpan secara persisten di `/root/.config/nexo-auth/session_link.json` sehingga perintah berikutnya otomatis menggunakan session thread tersebut.

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
- `/link <nama>` : Ubah session thread aktif saat itu juga.
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
| `/session/link` | `POST` | Mengubah tautan session thread (`{"session_name": "..."}`). |
| `/session/transcript` | `GET` | Mengambil riwayat transkrip percakapan (`?limit=50`). |
| `/session/stream` | `GET` | SSE (Server-Sent Events) cognitive stream. |
| `/ws/session/stream` | `WebSocket` | Full-duplex WebSocket stream untuk event & instruksi dua arah. |

---

## 5. Ringkasan Praktis Integrasi

- **Penyatuan Konteks**: Cukup jalankan `nexo link --latest` di terminal kerja Anda, maka interaksi suara di layar appliance akan langsung memiliki konteks penuh dari pekerjaan terminal Anda.
- **Monitoring Tanpa Gangguan**: Gunakan `nexo watch` di jendela tmux/terminal terpisah untuk mengamati respon appliance secara pasif.
- **Remote Announcement**: Gunakan `nexo speak "Build selesai!"` di akhir CI/CD pipeline atau build script Anda untuk memberi notifikasi suara nyata di meja kerja.
