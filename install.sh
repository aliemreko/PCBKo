#!/bin/bash
set -e

cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}

function usage() {
    cat <<EOF
Kullanım:
  ./install.sh              - Gereken Python paketlerini yükler
  ./install.sh --user       - Kullanıcı dizinine yükler
  ./install.sh --venv       - Proje içi sanal ortam oluşturup yükler
  PYTHON=python3.11 ./install.sh - Belirli Python yorumlayıcısı kullanır
EOF
}

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    usage
    exit 0
fi

if ! command -v "$PYTHON" > /dev/null 2>&1; then
    echo "Hata: $PYTHON bulunamadı. Lütfen Python 3 yükleyin."
    exit 1
fi

INSTALL_ARGS=()
if [ "$1" = "--user" ]; then
    INSTALL_ARGS+=("--user")
elif [ "$1" = "--venv" ]; then
    echo "Sanal ortam oluşturuluyor: .venv"
    "$PYTHON" -m venv .venv
    source .venv/bin/activate
fi

echo "pip güncelleniyor..."
"$PYTHON" -m pip install --upgrade pip wheel setuptools

echo "Gereken paketler yükleniyor..."
"$PYTHON" -m pip install "${INSTALL_ARGS[@]}" -r requirements.txt

echo "Kurulum tamamlandı. GUI'yi başlatmak için ./start.sh kullanabilirsiniz."
