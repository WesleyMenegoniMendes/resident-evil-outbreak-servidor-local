#!/bin/bash
# Desliga o servidor do Resident Evil Outbreak (não consome hardware enquanto parado)
sudo podman container stop bioserver
echo "Servidor Outbreak desligado."
