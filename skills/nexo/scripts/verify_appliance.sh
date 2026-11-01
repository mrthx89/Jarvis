#!/usr/bin/env bash
# ==============================================================================
# Nexo Dedicated Appliance - Verification & Health Diagnostic Script
# Architect: Yanto Hariyono (YH Dev) | Ethos: Berkarya dengan hati @ 2026
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SERVER_DIR="${REPO_ROOT}/server"
AUTH_DIR="${HOME}/.config/nexo-auth"
REGISTRY_FILE="${AUTH_DIR}/devices.json"
HEALTH_URL="http://127.0.0.1:8765/health"

echo "=========================================================="
echo "  Nexo Dedicated Appliance - System Verification Checklist"
echo "=========================================================="
echo ""

PASSED_COUNT=0
FAILED_COUNT=0

check_item() {
    local desc="$1"
    local cmd="$2"
    printf "%-52s : " "$desc"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "✅ PASS"
        PASSED_COUNT=$((PASSED_COUNT + 1))
    else
        echo "❌ FAIL"
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
}

# 1. Environment & Tools
check_item "Python 3.11+ terpasang" "python3 -c 'import sys; assert sys.version_info >= (3, 11)'"
check_item "Server virtual environment tersedia" "[ -f '${SERVER_DIR}/.venv/bin/python' ]"
check_item "Hermes CLI terdeteksi" "command -v hermes"

# 2. Config & Auth Permissions
check_item "Berkas server config.yaml ada" "[ -f '${SERVER_DIR}/config.yaml' ]"
check_item "Direktori auth ~/.config/nexo-auth ada" "[ -d '${AUTH_DIR}' ]"
check_item "Izin direktori auth aman (0700)" "[ \"\$(stat -c '%a' '${AUTH_DIR}' 2>/dev/null || stat -f '%Lp' '${AUTH_DIR}')\" = '700' ]"
check_item "Registry devices.json ada" "[ -f '${REGISTRY_FILE}' ]"
check_item "Izin registry devices.json aman (0600)" "[ \"\$(stat -c '%a' '${REGISTRY_FILE}' 2>/dev/null || stat -f '%Lp' '${REGISTRY_FILE}')\" = '600' ]"

# 3. Service & Network Port
check_item "Systemd unit nexo-appliance terpasang" "systemctl --user list-unit-files | grep -q 'nexo-appliance.service' || [ -f '${HOME}/.config/systemd/user/nexo-appliance.service' ]"
check_item "Port 8765 sedang listening (Gateway aktif)" "ss -tulpn | grep -q ':8765 ' || netstat -tulpn 2>/dev/null | grep -q ':8765 '"
check_item "Health endpoint HTTP 200" "curl -fsS '${HEALTH_URL}'"

# 4. CLI & Session Dispatcher
check_item "Executable CLI scripts/nexo dapat dieksekusi" "[ -x '${REPO_ROOT}/scripts/nexo' ]"
check_item "Executable CLI scripts/nexo_session.py ada" "[ -x '${REPO_ROOT}/scripts/nexo_session.py' ]"
check_item "Nexo status CLI merespons" "${REPO_ROOT}/scripts/nexo status"

# 5. Unit Tests Check
check_item "Unit test suite server lulus 100%" "cd '${SERVER_DIR}' && PYTHONPATH=src .venv/bin/python -m pytest tests -q -o asyncio_default_fixture_loop_scope=function"

echo ""
echo "=========================================================="
echo "Hasil Verifikasi: ${PASSED_COUNT} Passed, ${FAILED_COUNT} Failed"
if [ "$FAILED_COUNT" -eq 0 ]; then
    echo "🎉 Seluruh checklist verifikasi Nexo terpenuhi dengan sempurna!"
else
    echo "⚠️  Ada beberapa item verifikasi yang gagal. Periksa log atau konfigurasi."
fi
echo "=========================================================="

exit "$FAILED_COUNT"
