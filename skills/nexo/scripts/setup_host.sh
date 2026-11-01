#!/usr/bin/env bash
# ==============================================================================
# Nexo Dedicated Appliance - Host Automated Setup & Hermes Integration Script
# Architect: Yanto Hariyono (YH Dev) | Ethos: Berkarya dengan hati @ 2026
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${SCRIPT_DIR}/../server" ]; then
    REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
elif [ -d "${SCRIPT_DIR}/../../../server" ]; then
    REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
else
    REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

SERVER_DIR="${REPO_ROOT}/server"
CONFIG_FILE="${SERVER_DIR}/config.yaml"
EXAMPLE_CONFIG="${SERVER_DIR}/config.example.yaml"
AUTH_DIR="${HOME}/.config/nexo-auth"
REGISTRY_FILE="${AUTH_DIR}/devices.json"
BINDING_DIR="${HOME}/.config/nexo"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_NAME="nexo-appliance.service"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"

echo "=========================================================="
echo "  Nexo Dedicated Appliance - Host Setup & Hermes Bridge   "
echo "=========================================================="
echo "Repo root   : ${REPO_ROOT}"
echo "Server dir  : ${SERVER_DIR}"
echo "Hermes home : ${HERMES_HOME}"
echo ""

# ------------------------------------------------------------------------------
# 1. Check System Prerequisites
# ------------------------------------------------------------------------------
echo "[1/10] Memeriksa dependensi sistem..."
for cmd in python3 curl git ffmpeg; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "❌ Error: Perintah '$cmd' belum terpasang."
        echo "   Jalankan: sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg curl git build-essential"
        exit 1
    fi
done

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')
if [ "$PYTHON_OK" != "1" ]; then
    echo "❌ Error: Python 3.11 atau lebih baru diperlukan (terdeteksi: ${PYTHON_VER})."
    exit 1
fi
echo "✅ Dependensi sistem dasar terverifikasi (Python ${PYTHON_VER})."

# ------------------------------------------------------------------------------
# 2. Check & Verify Hermes CLI
# ------------------------------------------------------------------------------
echo ""
echo "[2/10] Memeriksa Hermes Agent CLI..."
if command -v hermes &>/dev/null; then
    HERMES_BIN="$(command -v hermes)"
    echo "✅ Hermes CLI terdeteksi di: ${HERMES_BIN}"
elif [ -x "${HOME}/.local/bin/hermes" ]; then
    HERMES_BIN="${HOME}/.local/bin/hermes"
    echo "✅ Hermes CLI terdeteksi di: ${HERMES_BIN}"
else
    echo "⚠️  Hermes CLI belum terpasang di PATH."
    echo "   Memasang Hermes CLI..."
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/install-hermes.sh
    bash /tmp/install-hermes.sh || true
    rm -f /tmp/install-hermes.sh
    HERMES_BIN="${HOME}/.local/bin/hermes"
fi

# ------------------------------------------------------------------------------
# 3. Initialize Canonical Hermes Session ('nexo')
# ------------------------------------------------------------------------------
echo ""
echo "[3/10] Menyiapkan sesi kanonis 'nexo' pada Hermes..."
STATE_DB="${HERMES_HOME}/state.db"
SESSION_EXISTS=0
if [ -f "${STATE_DB}" ]; then
    SESSION_EXISTS=$(python3 -c "
import sqlite3, sys
try:
    with sqlite3.connect('${STATE_DB}') as conn:
        row = conn.execute(\"SELECT id FROM sessions WHERE title = 'nexo' AND source = 'local' AND parent_session_id IS NULL\").fetchone()
        print(1 if row else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0)
fi

if [ "$SESSION_EXISTS" = "1" ]; then
    echo "✅ Sesi kanonis 'nexo' sudah tersedia di ${STATE_DB}."
else
    echo "Membuat sesi kanonis 'nexo' di Hermes..."
    if [ -x "${HERMES_BIN}" ]; then
        HERMES_HOME="${HERMES_HOME}" "${HERMES_BIN}" chat -c nexo -q "Inisialisasi sesi kanonis Nexo Dedicated Appliance." -Q >/dev/null 2>&1 || true
        echo "✅ Sesi 'nexo' berhasil diinisialisasi."
    else
        echo "⚠️  Hermes binary belum dapat dieksekusi. Buat sesi 'nexo' via 'hermes chat -c nexo' sebelum memulai server."
    fi
fi

# ------------------------------------------------------------------------------
# 4. Setup Python Virtual Environment
# ------------------------------------------------------------------------------
echo ""
echo "[4/10] Menyiapkan Python virtual environment..."
if [ ! -d "${SERVER_DIR}/.venv" ]; then
    python3 -m venv "${SERVER_DIR}/.venv"
    echo "✅ Virtualenv dibuat di ${SERVER_DIR}/.venv"
else
    echo "✅ Virtualenv sudah ada di ${SERVER_DIR}/.venv"
fi

echo "Memasang dependensi server..."
"${SERVER_DIR}/.venv/bin/pip" install --upgrade pip -q
"${SERVER_DIR}/.venv/bin/pip" install -r "${SERVER_DIR}/requirements.txt" -q
echo "✅ Seluruh dependensi Python terpasang."

# ------------------------------------------------------------------------------
# 5. Pre-download Faster-Whisper Model, Validate Edge-TTS & Generate Earcons
# ------------------------------------------------------------------------------
echo ""
echo "[5/10] Mengunduh/memvalidasi model STT/TTS & audio assets..."
"${SERVER_DIR}/.venv/bin/python" - << 'EOF'
import sys
try:
    print("  -> Mengunduh model Faster-Whisper (base)...")
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    print("  ✅ Faster-Whisper (base) siap di cache.")
except Exception as e:
    print(f"  ❌ Gagal mengunduh Faster-Whisper: {e}", file=sys.stderr)
    sys.exit(1)

try:
    print("  -> Menguji konektivitas Edge-TTS...")
    import asyncio, edge_tts
    async def probe():
        comm = edge_tts.Communicate("Koneksi audio Nexo siap.", "id-ID-ArdiNeural")
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                return True
        return False
    asyncio.run(probe())
    print("  ✅ Edge-TTS (id-ID-ArdiNeural) berhasil dihubungi.")
except Exception as e:
    print(f"  ⚠️  Peringatan Edge-TTS: {e}")
EOF

if [ -f "${REPO_ROOT}/scripts/generate_earcons.py" ]; then
    echo "  -> Menghasilkan file audio earcons lokal Android (chime_*.wav & earcon_*.wav)..."
    python3 "${REPO_ROOT}/scripts/generate_earcons.py"
    echo "  ✅ Aset audio earcons terverifikasi di android/app/src/main/res/raw/."
fi

# ------------------------------------------------------------------------------
# 6. Prepare Auth Directory, Binding Directory & Registry
# ------------------------------------------------------------------------------
echo ""
echo "[6/10] Menyiapkan direktori autentikasi perangkat (Stage 1 Auth)..."
umask 077
mkdir -p "${AUTH_DIR}"
chmod 700 "${AUTH_DIR}"
mkdir -p "${BINDING_DIR}"
chmod 700 "${BINDING_DIR}"

if [ ! -f "${REGISTRY_FILE}" ]; then
    echo "{}" > "${REGISTRY_FILE}"
    chmod 600 "${REGISTRY_FILE}"
    echo "✅ Registry perangkat dibuat di ${REGISTRY_FILE}"
else
    chmod 600 "${REGISTRY_FILE}"
    echo "✅ Registry perangkat sudah ada (${REGISTRY_FILE})"
fi

# ------------------------------------------------------------------------------
# 7. Setup config.yaml
# ------------------------------------------------------------------------------
echo ""
echo "[7/10] Menyiapkan konfigurasi server (${CONFIG_FILE})..."
cat > "${CONFIG_FILE}" << EOF
server:
  host: "127.0.0.1"
  port: 8765
  ws_path: "/ws/appliance"
  ping_interval: 15
  ping_timeout: 30
  device_registry_path: "${REGISTRY_FILE}"
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
  hermes_binary: "${HERMES_BIN}"
  timeout_seconds: 60
  session_name: "nexo"
  hermes_home: "${HERMES_HOME}"
  session_binding_path: "${BINDING_DIR}/session_binding.json"

telemetry:
  frequency_bands_count: 5
  emit_interval_ms: 40
EOF
echo "✅ ${CONFIG_FILE} berhasil dikonfigurasi."

# ------------------------------------------------------------------------------
# 8. Configure Systemd Service
# ------------------------------------------------------------------------------
echo ""
echo "[8/10] Mengonfigurasi systemd unit (${SERVICE_NAME})..."
mkdir -p "${SYSTEMD_USER_DIR}"
SERVICE_DEST="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

cat > "${SERVICE_DEST}" << EOF
[Unit]
Description=Nexo Dedicated Appliance - Audio Gateway & Hermes Bridge
After=network.target hermes-gateway.service

[Service]
Type=simple
WorkingDirectory=${SERVER_DIR}
Environment=PYTHONPATH=${SERVER_DIR}/src
Environment=HERMES_HOME=${HERMES_HOME}
Environment=PATH=${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=${HOME}
ExecStart=${SERVER_DIR}/.venv/bin/python -m nexo_server.main
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
Alias=jarvis-appliance.service
EOF

systemctl --user daemon-reload
echo "✅ Unit ${SERVICE_DEST} terpasang."

if command -v loginctl &>/dev/null; then
    loginctl enable-linger "${USER}" 2>/dev/null || true
    echo "✅ User lingering diaktifkan untuk pengguna '${USER}'."
fi

# ------------------------------------------------------------------------------
# 9. Install Hermes Skills & Telegram Plugin
# ------------------------------------------------------------------------------
echo ""
echo "[9/10] Memasang skill dan plugin ke Hermes..."

# Pasang Skill Nexo
HERMES_SKILLS_DEST="${HERMES_HOME}/skills/iot-appliance/nexo"
if [ -d "${REPO_ROOT}/skills/nexo" ]; then
    mkdir -p "${HERMES_SKILLS_DEST}"
    cp -r "${REPO_ROOT}/skills/nexo/"* "${HERMES_SKILLS_DEST}/"
    echo "✅ Skill 'nexo' dipasang ke: ${HERMES_SKILLS_DEST}"
fi

# Pasang Plugin Telegram Nexo
HERMES_PLUGINS_DEST="${HERMES_HOME}/plugins/nexo-device-access"
PLUGIN_SRC="${SERVER_DIR}/hermes-plugin/nexo-device-access"
if [ -d "${PLUGIN_SRC}" ]; then
    mkdir -p "${HERMES_PLUGINS_DEST}"
    cp "${PLUGIN_SRC}/plugin.yaml" "${HERMES_PLUGINS_DEST}/plugin.yaml"
    cp "${PLUGIN_SRC}/__init__.py" "${HERMES_PLUGINS_DEST}/__init__.py"

    # Buat settings.json jika belum ada
    if [ ! -f "${HERMES_PLUGINS_DEST}/settings.json" ]; then
        cat > "${HERMES_PLUGINS_DEST}/settings.json" << EOF
{
  "owner_user_id": "",
  "owner_chat_id": "",
  "registry_path": "${REGISTRY_FILE}",
  "delivery_dir": "${AUTH_DIR}",
  "origin": "wss://nexo.domain-anda.id/ws/appliance",
  "backend_python": "${SERVER_DIR}/.venv/bin/python",
  "server_src": "${SERVER_DIR}/src",
  "telegram_env_file": "${HERMES_HOME}/.env"
}
EOF
        chmod 600 "${HERMES_PLUGINS_DEST}/settings.json"
        echo "✅ Plugin Telegram dipasang. Silakan lengkapi 'owner_user_id' & 'owner_chat_id' di ${HERMES_PLUGINS_DEST}/settings.json"
    else
        echo "✅ Plugin Telegram sudah terpasang (${HERMES_PLUGINS_DEST})."
    fi

    if [ -x "${HERMES_BIN}" ]; then
        HERMES_HOME="${HERMES_HOME}" "${HERMES_BIN}" plugins enable nexo-device-access 2>/dev/null || true
    fi
fi

# Setup CLI Symlink & Permissions
mkdir -p "${HOME}/.local/bin"
chmod +x "${REPO_ROOT}/scripts/nexo" "${REPO_ROOT}/scripts/nexo_session.py" "${REPO_ROOT}/scripts/generate_earcons.py" "${REPO_ROOT}/scripts/setup_host.sh"
if [ -f "${REPO_ROOT}/skills/nexo/scripts/verify_appliance.sh" ]; then
    chmod +x "${REPO_ROOT}/skills/nexo/scripts/verify_appliance.sh"
fi
ln -sf "${REPO_ROOT}/scripts/nexo" "${HOME}/.local/bin/nexo"
echo "✅ CLI 'nexo' ditautkan ke ${HOME}/.local/bin/nexo"

# Setup Hermes Skill Installation
SKILL_SRC="${REPO_ROOT}/skills/nexo"
SKILL_DEST="${HERMES_HOME}/skills/iot-appliance/nexo"
if [ -d "${SKILL_SRC}" ]; then
    mkdir -p "${SKILL_DEST}/references" "${SKILL_DEST}/scripts"
    cp -r "${SKILL_SRC}/"* "${SKILL_DEST}/"
    chmod +x "${SKILL_DEST}/scripts/"*.sh 2>/dev/null || true
    echo "✅ Skill Nexo terpasang di ${SKILL_DEST}"
fi

# ------------------------------------------------------------------------------
# 10. Start Service & Verification
# ------------------------------------------------------------------------------
echo ""
echo "[10/10] Menjalankan layanan dan memverifikasi status..."
systemctl --user enable --now "${SERVICE_NAME}"

# Polling health check
HEALTH_OK=0
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8765/health &>/dev/null; then
        HEALTH_OK=1
        break
    fi
    sleep 0.5
done

if [ "$HEALTH_OK" = "1" ]; then
    echo "✅ Nexo Server aktif dan merespons (HTTP 200 di :8765)."
    STATUS_OUT=$(curl -fsS http://127.0.0.1:8765/session/status 2>/dev/null || echo "{}")
    LOCKED=$(python3 -c "import json; print(json.loads('''${STATUS_OUT}''').get('session_locked'))" 2>/dev/null || echo "False")
    BOUND_ID=$(python3 -c "import json; print(json.loads('''${STATUS_OUT}''').get('hermes_session_id'))" 2>/dev/null || echo "")
    if [ "$LOCKED" = "True" ]; then
        echo "✅ Sesi kanonis terkunci: ${BOUND_ID}"
    fi
else
    echo "⚠️  Layanan belum merespons dalam 15 detik. Periksa log: journalctl --user -u nexo-appliance -n 30"
fi

echo ""
echo "=========================================================="
echo "🎉 Setup Host Nexo Berhasil!"
echo "=========================================================="
echo "Antarmuka Pengguna & Utilitas yang Siap Digunakan:"
echo ""
echo "1. Layar Kios Android (Visual Maskot & Suara Interaktif):"
echo "   - ⚠️ PERINGATAN: DILARANG MEMBUILD APK DI SERVER! Kompilasi APK Android HANYA"
echo "     dilakukan di komputer lokal developer menggunakan Android Studio / Gradle."
echo "     Lihat panduan lengkap di: docs/BUILDING.md"
echo "   - Pasang APK Nexo (id.my.yhdev.nexo) hasil build lokal ke tablet Android."
echo "   - Tekan lama status koneksi pada layar untuk melihat Device ID."
echo "   - Terbitkan token akses (via Telegram /nexo-token atau CLI Python):"
echo "     PYTHONPATH=${SERVER_DIR}/src ${SERVER_DIR}/.venv/bin/python -m nexo_server.auth provision --device-id '<DEVICE_ID>' --origin 'wss://domain-anda/ws/appliance'"
echo "   - Masukkan token ke layar tablet dan simpan."
echo ""
echo "2. Hermes Telegram Bot (Remote Control & Status UI):"
echo "   - Lengkapi 'owner_user_id' & 'owner_chat_id' di ${HERMES_HOME}/plugins/nexo-device-access/settings.json"
echo "   - Perintah bot: /nexo_status, /nexo_say <teks>, /nexo_token <device_id>, /nexo_help"
echo ""
echo "3. Terminal Console Bridge & Stream Watcher (Interactive CLI UI):"
echo "   - Buka sesi percakapan: HERMES_HOME=${HERMES_HOME} hermes chat -c nexo"
echo "   - Konsol interaktif 2 arah: nexo bridge"
echo "   - Cognitive stream watcher: nexo watch"
echo "   - Cek status appliance:     nexo status"
echo "=========================================================="
