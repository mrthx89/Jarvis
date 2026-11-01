#!/usr/bin/env bash
# ==============================================================================
# Nexo Dedicated Appliance - Host Automated Setup & Fresh Deployment Script
# Architect: Yanto Hariyono (YH Dev) | Ethos: Berkarya dengan hati @ 2026
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SERVER_DIR="${REPO_ROOT}/server"
CONFIG_FILE="${SERVER_DIR}/config.yaml"
EXAMPLE_CONFIG="${SERVER_DIR}/config.example.yaml"
AUTH_DIR="${HOME}/.config/nexo-auth"
REGISTRY_FILE="${AUTH_DIR}/devices.json"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_NAME="nexo-appliance.service"

echo "=========================================================="
echo "  Nexo Dedicated Appliance - Host Deployment & Setup      "
echo "=========================================================="
echo "Repo root : ${REPO_ROOT}"
echo "Server dir: ${SERVER_DIR}"
echo ""

# 1. Check system prerequisites
echo "[1/7] Memeriksa dependensi sistem..."
for cmd in python3 curl git ffmpeg; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "❌ Error: Perintah '$cmd' belum terpasang."
        echo "   Jalankan: sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg curl git build-essential"
        exit 1
    fi
done
echo "✅ Dependensi sistem dasar terverifikasi."

# Check Hermes CLI
if command -v hermes &>/dev/null; then
    HERMES_BIN="$(command -v hermes)"
    echo "✅ Hermes CLI terdeteksi di: ${HERMES_BIN}"
else
    echo "⚠️  Peringatan: Hermes CLI belum terpasang di PATH."
    echo "   Dapat dipasang via: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    HERMES_BIN="${HOME}/.local/bin/hermes"
fi

# 2. Setup Virtual Environment
echo ""
echo "[2/7] Menyiapkan Python virtual environment..."
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

# 3. Pre-download Faster-Whisper Model & Test Edge-TTS
echo ""
echo "[3/7] Mengunduh dan memvalidasi model STT / TTS..."
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
        comm = edge_tts.Communicate("Tes suara", "id-ID-ArdiNeural")
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                return True
        return False
    asyncio.run(probe())
    print("  ✅ Edge-TTS (id-ID-ArdiNeural) berhasil dihubungi.")
except Exception as e:
    print(f"  ⚠️ Peringatan Edge-TTS (koneksi jaringan luar diperlukan saat runtime): {e}")
EOF

# 4. Prepare Auth Directory & Registry
echo ""
echo "[4/7] Menyiapkan direktori autentikasi perangkat (Stage 1 Auth)..."
umask 077
mkdir -p "${AUTH_DIR}"
chmod 700 "${AUTH_DIR}"
if [ ! -f "${REGISTRY_FILE}" ]; then
    echo "{}" > "${REGISTRY_FILE}"
    chmod 600 "${REGISTRY_FILE}"
    echo "✅ Registry perangkat dibuat di ${REGISTRY_FILE}"
else
    chmod 600 "${REGISTRY_FILE}"
    echo "✅ Registry perangkat sudah ada (${REGISTRY_FILE})"
fi

# 5. Setup config.yaml
echo ""
echo "[5/7] Menyiapkan konfigurasi server..."
if [ ! -f "${CONFIG_FILE}" ]; then
    cp "${EXAMPLE_CONFIG}" "${CONFIG_FILE}"
    # Replace default placeholder with actual paths
    sed -i "s|device_registry_path: \"\"|device_registry_path: \"${REGISTRY_FILE}\"|g" "${CONFIG_FILE}"
    sed -i "s|hermes_binary: \"hermes\"|hermes_binary: \"${HERMES_BIN}\"|g" "${CONFIG_FILE}"
    echo "✅ ${CONFIG_FILE} berhasil dibuat."
else
    echo "✅ ${CONFIG_FILE} sudah ada."
fi

# 6. Configure Systemd Service
echo ""
echo "[6/7] Mengonfigurasi systemd unit (${SERVICE_NAME})..."
mkdir -p "${SYSTEMD_USER_DIR}"
SERVICE_DEST="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

cat > "${SERVICE_DEST}" << EOF
[Unit]
Description=Nexo Dedicated Appliance - Audio Gateway & Hermes Bridge
After=network.target

[Service]
Type=simple
WorkingDirectory=${SERVER_DIR}
Environment=PYTHONPATH=${SERVER_DIR}/src
Environment=PATH=${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=${HOME}
ExecStart=${SERVER_DIR}/.venv/bin/python -m nexo_server.main
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
Alias=nexo-appliance.service
EOF

echo "Memuat ulang daemon systemd user..."
systemctl --user daemon-reload
echo "✅ Unit ${SERVICE_DEST} terpasang."

# Check linger
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "${USER}" 2>/dev/null || true
fi

# 7. Setup CLI Symlink
echo ""
echo "[7/7] Membuat symlink executable 'nexo'..."
mkdir -p "${HOME}/.local/bin"
ln -sf "${REPO_ROOT}/scripts/nexo" "${HOME}/.local/bin/nexo"
chmod +x "${REPO_ROOT}/scripts/nexo" "${REPO_ROOT}/scripts/nexo_session.py"
echo "✅ CLI 'nexo' ditautkan ke ${HOME}/.local/bin/nexo"

echo ""
echo "=========================================================="
echo "🎉 Setup Host Nexo Berhasil!"
echo "=========================================================="
echo "Langkah selanjutnya:"
echo "1. Jalankan service:"
echo "   systemctl --user enable --now nexo-appliance.service"
echo "2. Periksa status dan health endpoint:"
echo "   curl -fsS http://127.0.0.1:8765/health"
echo "3. Terbitkan token untuk perangkat Android:"
echo "   PYTHONPATH=${SERVER_DIR}/src ${SERVER_DIR}/.venv/bin/python -m nexo_server.auth provision \\"
echo "     --registry ${REGISTRY_FILE} \\"
echo "     --device-id '<DEVICE_ID>' \\"
echo "     --origin 'wss://domain-anda/ws/appliance' \\"
echo "     --output ${AUTH_DIR}/device-delivery.json"
echo "4. Hubungkan terminal kerja lokal ke Nexo:"
echo "   nexo status"
echo "   nexo link --latest"
echo "=========================================================="
