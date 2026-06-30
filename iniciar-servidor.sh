#!/bin/bash
# Liga o servidor do Resident Evil Outbreak (cria o container se não existir, senão só inicia)
set -e

if sudo podman container exists bioserver; then
	sudo podman container start bioserver
else
	HIP=25.44.159.240
	sudo podman container create --name bioserver \
		-p $HIP:53:53/udp -p $HIP:53:53/tcp \
		-p $HIP:80:80 -p $HIP:443:443 \
		-p $HIP:8200:8200 -p $HIP:8300:8300 -p $HIP:8590:8590 -p $HIP:8690:8690 \
		bioserver-docker:latest
	sudo podman container start bioserver
fi

echo "Servidor Outbreak ligado. IP Hamachi: 25.44.159.240"
