#!/bin/bash
# Abre o painel do Servidor RE Outbreak
# Funciona de qualquer lugar — detecta automaticamente onde está o executável
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXE="$DIR/Servidor-RE-Outbreak-Linux"

if [ ! -f "$EXE" ]; then
    zenity --error --text="Executável não encontrado.\n\nLow em Releases no GitHub ou rode:\n  python3 outbreak-server-setup.py" 2>/dev/null \
    || echo "Executável não encontrado: $EXE"
    exit 1
fi

chmod +x "$EXE"
"$EXE"
