# Panduan Instalasi Nexo Dedicated Appliance & Konfigurasi Hermes

Dokumen ini menjelaskan proses instalasi lengkap **Nexo Dedicated Appliance** pada host Linux baru serta konfigurasi integrasinya dengan **Hermes Agent**.

Nexo menghadirkan antarmuka fisik (*user interface*) bagi Hermes Agent agar asisten AI tidak hanya beroperasi di terminal atau teks, melainkan hadir dalam bentuk **wujud nyata di atas meja kerja**: layar visual maskot digital yang hidup, pengeras suara alami, serta mikrofon hands-free dengan peredam gema (*hardware Acoustic Echo Cancellation*).

---

## 1. Komponen Antarmuka Pengguna (User Interfaces)

Saat Nexo terpasang dan terkonfigurasi ke Hermes, Anda memperoleh 4 (empat) lapisan antarmuka pengguna:

| Antarmuka | Bentuk / Platform | Fungsi Utama |
|---|---|---|
| **Layar Kios Android** | Tablet / Smart Screen Android (`id.my.yhdev.nexo`) | **Wajah & Indra Fisik**: Mata digital maskot bercahaya dengan 16 ekspresi emosi Nusa Silver, animasi 60 FPS GPU, mikrofon hands-free AEC, speaker TTS, dan subtitle transkrip kognitif dengan tombol salin. |
| **Telegram Bot UI** | Chat privat bot Telegram Hermes (`nexo-device-access`) | **Remote Control & Provisioning**: Kartu visual interaktif status appliance (`/nexo_status`), penerbitan token perangkat (`/nexo_token`), dan perintah suara jarak jauh (`/nexo_say`). |
| **Terminal Console Bridge** | Terminal CLI Hermes lokal (`nexo` CLI & `hermes chat`) | **Konsol Interaktif & Watcher**: Konsol interaktif 2 arah (`nexo bridge`), visualizer gelombang audio & transkrip real-time (`nexo watch`), serta mirroring jawaban otomatis dari sesi `hermes chat -c nexo`. |
| **Hermes Agent Skill** | Skill internal Hermes (`~/.hermes/skills/nexo`) | **Otonomi Agen**: Hermes Agent di host tersebut otomatis memahami cara mengecek, mengontrol, dan mendiagnosis Nexo secara mandiri. |

---

## 2. Prasyarat Sistem

- **Sistem Operasi**: Linux x86_64 atau aarch64 (Ubuntu 22.04+, Debian 12+, Arch, Fedora).
- **Python**: Versi 3.11 atau lebih baru.
- **Paket Dasar**: `ffmpeg`, `curl`, `git`, `build-essential`.
- **Hermes Agent CLI**: Sudah terpasang dan terkonfigurasi dengan provider model (LLM).
- **Perangkat Klien**: Monitor pintar / tablet Android (Min Android 8.0 / API 26).

---

## 3. Instalasi Otomatis (Direkomendasikan)

Repositori menyediakan skrip setup otomatis yang menangani seluruh persiapan: pembuatan virtual environment, dependensi Python, pre-cache model suara, konfigurasi server, binding sesi Hermes, systemd service, skill, hingga plugin Telegram.

Jalankan perintah berikut di direktori repositori Nexo:

```bash
git clone https://github.com/mrthx89/Nexo.git
cd Nexo
chmod +x scripts/setup_host.sh
./scripts/setup_host.sh
```

Skrip akan menjalankan 10 langkah verifikasi dan konfigurasi secara otomatis. Setelah selesai, server akan langsung aktif pada port `8765`.

---

## 4. Instalasi Manual Langkah demi Langkah

Jika Anda ingin mengonfigurasi host secara manual atau menyesuaikan konfigurasi khusus, ikuti langkah-langkah di bawah ini:

### Langkah A: Siapkan Dependensi Sistem & Hermes CLI

Pasang paket yang diperlukan:
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg curl git build-essential
```

Pastikan Hermes Agent CLI telah terpasang:
```bash
command -v hermes || curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup
hermes model
```

### Langkah B: Inisialisasi Sesi Kanonis Hermes (`nexo`)

Server Nexo mengunci percakapan ke satu ID percakapan kanonis bernama `nexo`. Buat sesi ini di Hermes CLI sebelum server dijalankan:

```bash
hermes chat -c nexo -q "Inisialisasi sesi Nexo Dedicated Appliance." -Q
```

Perintah di atas akan mencatat sesi bernama `nexo` di database SQLite `$HERMES_HOME/state.db`.

### Langkah C: Setup Python Virtual Environment

```bash
cd /data/home/Nexo  # Atau path instalasi Anda
python3 -m venv server/.venv
source server/.venv/bin/activate
pip install --upgrade pip
pip install -r server/requirements.txt
```

### Langkah D: Pre-download & Cache Model STT/TTS

Unduh model Faster-Whisper `base` agar inferensi pertama tidak mengalami jeda:
```bash
python3 - << 'EOF'
from faster_whisper import WhisperModel
model = WhisperModel("base", device="cpu", compute_type="int8")
print("Faster-Whisper model cached!")
EOF
```

Uji koneksi sintesis suara Edge-TTS (Indonesia - Ardi):
```bash
python3 - << 'EOF'
import asyncio, edge_tts
async def test():
    comm = edge_tts.Communicate("Koneksi suara siap.", "id-ID-ArdiNeural")
    data = b"".join([chunk["data"] async for chunk in comm.stream() if chunk["type"] == "audio"])
    print(f"Edge-TTS OK ({len(data)} bytes).")
asyncio.run(test())
EOF
```

### Langkah E: Siapkan Direktori Autentikasi & Binding Sesi

```bash
umask 077
mkdir -p ~/.config/nexo-auth
chmod 700 ~/.config/nexo-auth
echo "{}" > ~/.config/nexo-auth/devices.json
chmod 600 ~/.config/nexo-auth/devices.json

mkdir -p ~/.config/nexo
chmod 700 ~/.config/nexo
```

### Langkah F: Konfigurasi `server/config.yaml`

Buat file `server/config.yaml` dari template:

```yaml
server:
  host: "127.0.0.1"
  port: 8765
  ws_path: "/ws/appliance"
  ping_interval: 15
  ping_timeout: 30
  device_registry_path: "/root/.config/nexo-auth/devices.json"
  max_sessions: 4
  hello_timeout_seconds: 5.0
  max_json_bytes: 4096

stt:
  provider: "faster-whisper"
  model: "base"
  language: "id"

tts:
  provider: "edge-tts"
  voice: "id-ID-ArdiNeural"

hermes:
  bridge_mode: "cli"
  hermes_binary: "/root/.local/bin/hermes"
  timeout_seconds: 60
  session_name: "nexo"
  hermes_home: "/root/.hermes"
  session_binding_path: "/root/.config/nexo/session_binding.json"
```
*(Sesuaikan path `/root` dengan `$HOME` Anda bila berjalan sebagai user non-root).*

### Langkah G: Konfigurasi Systemd User Service

Buat file unit di `~/.config/systemd/user/nexo-appliance.service`:

```ini
[Unit]
Description=Nexo Dedicated Appliance - Audio Gateway & Hermes Bridge
After=network.target

[Service]
Type=simple
WorkingDirectory=/data/home/Nexo/server
Environment=PYTHONPATH=/data/home/Nexo/server/src
Environment=HERMES_HOME=/root/.hermes
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/root
ExecStart=/data/home/Nexo/server/.venv/bin/python -m nexo_server.main
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
Alias=nexo-appliance.service
```

Aktifkan service dan hidupkan user lingering (agar service tetap hidup setelah logout SSH):
```bash
systemctl --user daemon-reload
loginctl enable-linger $USER
systemctl --user enable --now nexo-appliance.service
```

Periksa kesehatan service:
```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8765/session/status
```
Status harus menampilkan `session_locked: true` dan `hermes_session_id`.

---

## 5. Konfigurasi Ekstensi & Integrasi Hermes

### A. Memasang Skill Nexo ke Hermes Agent
Agar instance Hermes dapat memahami perintah dan troubleshooting Nexo:
```bash
mkdir -p ~/.hermes/skills/iot-appliance/nexo
cp -r skills/nexo/* ~/.hermes/skills/iot-appliance/nexo/
```

### B. Memasang Plugin Telegram Nexo (Remote Bot UI)
Jika Hermes Gateway terhubung ke Telegram Bot:
1. Salin plugin ke direktori plugins Hermes:
   ```bash
   mkdir -p ~/.hermes/plugins/nexo-device-access
   cp server/hermes-plugin/nexo-device-access/plugin.yaml ~/.hermes/plugins/nexo-device-access/
   cp server/hermes-plugin/nexo-device-access/__init__.py ~/.hermes/plugins/nexo-device-access/
   ```
2. Buat berkas `settings.json` di `~/.hermes/plugins/nexo-device-access/settings.json` (chmod 0600):
   ```json
   {
     "owner_user_id": "<USER_ID_TELEGRAM_ANDA>",
     "owner_chat_id": "<CHAT_ID_TELEGRAM_ANDA>",
     "registry_path": "/root/.config/nexo-auth/devices.json",
     "delivery_dir": "/root/.config/nexo-auth",
     "origin": "wss://nexo.domain-anda.id/ws/appliance",
     "backend_python": "/data/home/Nexo/server/.venv/bin/python",
     "server_src": "/data/home/Nexo/server/src",
     "telegram_env_file": "/root/.hermes/.env"
   }
   ```
3. Aktifkan plugin:
   ```bash
   hermes plugins enable nexo-device-access
   ```
4. Di bot Telegram, perintah berikut akan langsung aktif:
   - `/nexo_status` — Cek status server, koneksi tablet, dan ekspresi maskot.
   - `/nexo_say <pesan>` — Suarakan teks langsung ke speaker monitor/tablet.
   - `/nexo_token <device_id>` — Buat atau kirim ulang token akses tablet.
   - `/nexo_help` — Bantuan perintah bot.

### C. Mendaftarkan CLI Dispatcher ke PATH
```bash
mkdir -p ~/.local/bin
ln -sf /data/home/Nexo/scripts/nexo ~/.local/bin/nexo
chmod +x /data/home/Nexo/scripts/nexo /data/home/Nexo/scripts/nexo_session.py
```

---

## 6. Menghubungkan Layar Android (Physical Kiosk UI)

Layar Android bertindak sebagai wajah utama Nexo:

1. **Pasang APK**: Pasang file APK `id.my.yhdev.nexo` pada tablet / smart screen Android.
2. **Lihat Device ID**: Buka aplikasi. Tekan lama (*long-press*) pada teks status koneksi / mikrofon di bagian atas layar.
3. **Catat Device ID**: Di bottom sheet pengaturan yang muncul, catat `Device ID` Anda (contoh: `kiosk-tablet-01`).
4. **Terbitkan Token**:
   - **Lewat Telegram**: Kirim `/nexo_token <Device_ID>` ke bot Telegram Anda.
   - **Lewat Terminal**:
     ```bash
     PYTHONPATH=server/src server/.venv/bin/python -m nexo_server.auth provision \
       --registry ~/.config/nexo-auth/devices.json \
       --device-id "<DEVICE_ID>" \
       --origin "wss://nexo.domain-anda.id/ws/appliance" \
       --output ~/.config/nexo-auth/device-delivery.json
     ```
     Salin token dari file JSON tersebut, lalu hapus filenya.
5. **Masukkan Token ke Tablet**: Masukkan token ke input bertopeng pada bottom sheet tablet, pastikan WSS Origin sesuai, lalu klik **Simpan**.
6. Tablet akan mengenkripsi token ke Android Keystore (AES-GCM) dan otomatis tersambung.
7. **Wajah Maskot Aktif**: Mata digital maskot akan menyala, ekspresi responsif siap digunakan, dan mikrofon siap menerima perintah suara.

---

## 7. Ekspos Publik dengan Cloudflare Tunnel (WSS)

Agar perangkat Android dapat tersambung secara aman dari luar LAN:

1. Buat tunnel: `cloudflared tunnel create nexo-tunnel`
2. Atur ingress di `/etc/cloudflared/config.yml`:
   ```yaml
   tunnel: <TUNNEL_UUID>
   credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

   ingress:
     - hostname: nexo.domain-anda.id
       service: http://127.0.0.1:8765
       originRequest:
         noTLSVerify: false
     - service: http_status:404
   ```
3. Arahkan DNS: `cloudflared tunnel route dns nexo-tunnel nexo.domain-anda.id`
4. Mulai tunnel: `sudo systemctl restart cloudflared`
5. Endpoint publik Anda adalah: `wss://nexo.domain-anda.id/ws/appliance`

---

## 8. Verifikasi Keseluruhan Sistem

Jalankan skrip diagnostik terpadu:
```bash
./skills/nexo/scripts/verify_appliance.sh
```

Skrip akan menguji 13 poin kesehatan sistem: ketersediaan Python, virtualenv, CLI Hermes, izin file autentikasi (0700/0600), port 8765, health endpoint HTTP 200, status CLI, dan seluruh test suite server.
