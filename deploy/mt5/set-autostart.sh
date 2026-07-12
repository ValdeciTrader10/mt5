#!/usr/bin/with-contenv bash
# ---------------------------------------------------------------------------
# Faz o openbox iniciar o NOSSO wrapper (forex-start.sh) em vez do start.sh
# original da imagem, para aplicar a correção do numpy no Python do Wine.
#
# A baseimage kasmvnc só copia /defaults/autostart -> /config/.config/openbox/
# autostart SE o arquivo ainda não existir. Como o /config é um volume, em
# deploys já existentes esse arquivo JÁ aponta para /Metatrader/start.sh e uma
# troca do /defaults/autostart não teria efeito. Por isso sobrescrevemos aqui,
# no cont-init (roda antes do desktop/openbox subir), a cada boot.
# ---------------------------------------------------------------------------
set -u

AUTOSTART=/config/.config/openbox/autostart

mkdir -p "$(dirname "${AUTOSTART}")"
echo '/custom/forex-start.sh' > "${AUTOSTART}"
chown -R abc:abc /config/.config/openbox 2>/dev/null || true

echo "[set-autostart] openbox autostart apontando para /custom/forex-start.sh"
exit 0
