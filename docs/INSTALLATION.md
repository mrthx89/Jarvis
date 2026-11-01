# Instalasi dan penggunaan Nexo Dedicated Appliance

Panduan ini menjelaskan gateway Linux dan klien Android native untuk Nexo (sebelumnya Jarvis). Nexo mengintegrasikan Hermes Agent melalui CLI dan session bridge; bukan plugin resmi Nous Research, bukan pengganti runtime Hermes, dan tidak menjalankan model di Android. Android minimum 8.0/API 26, target/compile SDK 34; build memerlukan full JDK 17. Server memerlukan Python 3.11+.

## 1. Siapkan Hermes pada akun layanan

Gunakan akun Linux khusus dengan hak akses minimum. Hermes dapat menjalankan tools; token perangkat hanya mengizinkan akses gateway, bukan membatasi kewenangan agen. Jangan membuka akses perangkat tidak tepercaya ke agen administratif.

Instalasi resmi CLI Linux/macOS/WSL2:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

Tinjau installer sebelum menjalankannya bila kebijakan sistem memerlukan. Panduan upstream: [installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation) dan [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands). Konfigurasi awal sebagai pengguna yang nanti menjalankan gateway:

```bash
hermes setup
hermes model
hermes doctor
hermes --version
hermes chat --help
```

Simpan kredensial provider melalui setup Hermes, bukan Git atau konfigurasi Android. Pastikan provider/model dapat menjawab. Uji berikut melakukan panggilan model, dapat menggunakan kuota, dan membuat/melanjutkan sesi:

```bash
hermes chat -c appliance --create-if-missing -q "Jawab singkat: koneksi Nexo siap." -Q
```

### Kontrak kompatibilitas

Bridge Nexo menjalankan command berikut secara langsung, tanpa shell:

```text
hermes chat -c appliance --create-if-missing -q <transkripsi> -Q
```

Runtime harus mendukung `chat`, `-c`, `--create-if-missing`, `-q`, dan `-Q`. CLI pada lingkungan pengembangan diperiksa sebagai `v0.22.5+2971.ga938f00`; ini catatan pemeriksaan command, bukan jaminan seluruh versi Hermes. Tidak ada minimum versi upstream yang telah dibuktikan melalui matriks pengujian. Setelah upgrade, periksa help/flag, banner/footer output, timeout, sesi, kemudian uji percakapan nyata. Bridge membersihkan metadata CLI yang dikenal sebelum TTS.

Akun, PATH, HOME/HERMES_HOME dan profil service menentukan konfigurasi yang dipakai. Uji dari akun dan direktori kerja yang sama dengan service. Semua appliance saat ini menggunakan nama sesi `appliance`; ini bukan isolasi per perangkat atau sesi Telegram. Jarvis tidak mengirim override model/provider lewat CLI. Detail: [APPLIANCE_SESSION](APPLIANCE_SESSION.md).

## 2. Pasang gateway

```bash
git clone https://github.com/mrthx89/Jarvis.git
cd Jarvis
python3 -m venv server/.venv
source server/.venv/bin/activate
pip install -r server/requirements.txt
cp server/config.example.yaml server/config.yaml
```

Sesuaikan `server/config.yaml` atau gunakan file eksternal melalui `NEXO_CONFIG_PATH` (atau `JARVIS_CONFIG_PATH`). Field utama:

```yaml
server:
  host: "127.0.0.1"
  port: 8765
  device_registry_path: "/home/nexo/.config/nexo-auth/devices.json"
stt:
  provider: "faster-whisper"
  model: "base"
  language: "id"
tts:
  provider: "edge-tts"
  voice: "id-ID-ArdiNeural"
hermes:
  bridge_mode: "cli"
  hermes_binary: "/home/nexo/.local/bin/hermes"
```

Ini contoh path, bukan path yang wajib dipakai. Gunakan hasil `command -v hermes` dari akun layanan untuk binary. Pilihan `hermes_binary` dan `bridge_mode: cli` adalah jalur integrasi yang digunakan; field konfigurasi lain jangan diasumsikan aktif tanpa menelusuri implementasi. Model STT dapat memerlukan unduhan pada pemakaian awal. Edge TTS dan provider model memerlukan jaringan. Pastikan kapasitas disk mencukupi.

Gateway default hanya bind loopback. Ekspos `/ws/appliance` melalui reverse proxy atau tunnel dengan TLS/WSS dan dukungan WebSocket Upgrade. Batasi port origin dari jaringan publik; jangan menonaktifkan validasi WSS Android. Endpoint health tidak memerlukan token dan bukan pemeriksaan otorisasi.

## 3. Siapkan klien dan kredensial

1. Buka `android/` di Android Studio. Sediakan SDK 34/full JDK 17 serta `android/app/google-services.json` milik proyek Firebase dengan package `id.my.yhdev.nexo` (atau alias `id.my.yhdev.jarvis`).
2. Konfigurasikan Remote Config `server_websocket_url` ke `wss://domain-anda/ws/appliance`; ikuti [REMOTE_CONFIG_SETUP](REMOTE_CONFIG_SETUP.md). Kredensial tidak boleh disimpan di Firebase.
3. Build di komputer pengembang sesuai [BUILDING](BUILDING.md). Jangan membuild di gateway bila storage dibatasi. Paket `internal` memakai debug signing; tidak dapat memperbarui instalasi yang memakai signing berbeda. Jangan uninstall sembarangan karena token/data lokal akan hilang.
4. Buka aplikasi dan tekan lama status mikrofon. Catat ID perangkat dan verifikasi origin WSS yang ditampilkan.
5. Provision token per perangkat menggunakan bot Telegram (`/nexo-token` atau `/jarvis-token`) atau CLI offline dalam [DEVICE_AUTH](DEVICE_AUTH.md). Registry berisi hash, file pengiriman privat berisi token sekali terbit. Masukkan token pada field bertopeng, konfirmasi origin, lalu simpan. Jangan kirim token ke chat publik, commit, atau log.
6. Koordinasikan aktivasi gateway dan klien yang mendukung autentikasi. Registry kosong/invalid menolak semua koneksi; APK lama tanpa auth ditolak.

## 4. Jalankan sebagai service

Uji foreground terlebih dahulu dari `server/`:

```bash
cd server
PYTHONPATH=src .venv/bin/python -m nexo_server.main
```

Di terminal lain, `curl -fsS http://127.0.0.1:8765/health` memeriksa HTTP saja. Lanjutkan pemeriksaan koneksi tanpa token/invalid token, handshake valid, kemudian audio end-to-end.

Untuk operasi berkelanjutan, salin dan sesuaikan [template systemd](../server/deploy/nexo-appliance.service), bukan menggunakan path pengembang mentah. Ganti `WorkingDirectory`, `ExecStart`, `PYTHONPATH`, `PATH` dan `HOME` agar konsisten dengan akun layanan. Gunakan `Environment=NEXO_CONFIG_PATH=/path/private/config.yaml` bila konfigurasi eksternal. Profil Hermes khusus harus memiliki home/profil dan kredensial yang benar; jangan mengubah profil lain tanpa sengaja.

```bash
mkdir -p ~/.config/systemd/user
# Salin template yang SUDAH disesuaikan ke:
# ~/.config/systemd/user/nexo-appliance.service
systemctl --user daemon-reload
systemctl --user enable --now nexo-appliance.service
systemctl --user status nexo-appliance.service
journalctl --user -u nexo-appliance.service -n 50 --no-pager
```

Aktivasi/restart memutus sesi suara aktif. Lakukan ketika perangkat tidak sedang berbicara. Atur user lingering melalui kebijakan administrator bila service harus bertahan setelah logout. Lindungi log; jangan membagikan payload/kredensial provider.

## 5. Pemakaian sehari-hari

- Instalasi baru dimulai OFF. Tekan **Mulai mikrofon**, tunggu status menyimak/kesiapan, lalu bicara dengan jeda di akhir kalimat.
- Selama memproses dan memutar suara, alur half-duplex menahan pengiriman mikrofon. Barge-in belum dijanjikan.
- Tekan **Berhenti** untuk menghentikan audio/koneksi. Maskot menangis dan panel percakapan disembunyikan; tidak ada perintah suara yang dapat membangunkan mikrofon OFF.
- Saat ON, ucapan senang hilang setelah 15 detik. Ini teks reaksi, bukan status koneksi.
- Ketuk ikon clipboard di kanan `NEXO // COGNITIVE STREAM` untuk menyalin jawaban final lengkap. Jawaban tersimpan dalam memori Activity dan hilang setelah Activity dibuat ulang.
- Ucapkan “Nexo tersenyum” (atau “Jarvis tersenyum”), “coba tertawa”, “ekspresi jahil”, atau “kembali normal”. Perintah singkat mengubah ekspresi sementara; tidak mengubah hak akses maupun konfigurasi model. Lihat [MASCOT_EXPRESSIONS](MASCOT_EXPRESSIONS.md).
- Gunakan utilitas sesi terminal `./scripts/nexo` untuk interaksi kognitif langsung dari terminal (`./scripts/nexo status`, `./scripts/nexo send "Halo"`, `./scripts/nexo watch`).
- Ketuk status untuk detail gangguan; tekan lama untuk pengaturan token. Saat origin berpindah ke host/port lain, verifikasi dan provision ulang, jangan meneruskan token sembarangan.

## 6. Diagnostik dan upgrade

| Gejala | Pemeriksaan |
| --- | --- |
| Health normal, perangkat ditolak | Registry, ID, hash/token, origin, hello, dan izin file; health bukan bukti autentikasi. |
| Hermes tidak ditemukan | Binary dan PATH akun systemd, bukan PATH shell interaktif. |
| Model/provider salah | Periksa profil/home/sesi milik akun service; Telegram tidak otomatis berbagi sesi appliance. |
| Tidak ada transkripsi | Izin mikrofon, readiness protokol, hardware, jarak/volume, dan STT diuji terpisah. |
| Wajah tidak berubah | Backend baru harus aktif dan APK harus berisi perubahan terbaru; cek perintah yang didukung dan hasil STT. |
| Token tidak dapat dibaca setelah reinstall | Provision ulang setelah memastikan identitas/signing; cabut ID lama bila tidak dipakai. |

Sebelum upgrade, baca [VERSIONING](VERSIONING.md) dan [CHANGELOG](../CHANGELOG.md), cadangkan konfigurasi/registry privat, catat commit dan versi Hermes, lalu pilih release/tag yang jelas. Source pull tidak memperbarui APK maupun proses server secara otomatis. Build/restart harus terkoordinasi; rollback memakai signing kompatibel dan cadangan data, bukan menghapus registry.
