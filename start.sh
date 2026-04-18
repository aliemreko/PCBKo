#!/bin/bash
cd "$(dirname "$0")"

if [ "$1" = "install" ] || [ "$1" = "--install" ]; then
    ./install.sh "${@:2}"
    exit 0
fi

# KiCad 3D model path — needed for 3D viewer
export KICAD6_3DMODEL_DIR="/usr/share/kicad/3dmodels"
export KICAD7_3DMODEL_DIR="/usr/share/kicad/3dmodels"
export KICAD8_3DMODEL_DIR="/usr/share/kicad/3dmodels"

python3 run_gui.py
