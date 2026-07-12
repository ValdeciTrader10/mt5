#!/usr/bin/with-contenv bash
# ---------------------------------------------------------------------------
# Correção do bug da imagem gmag11/metatrader5_vnc (issue #28) + alinhamento RPyC.
#
# Problema 1 (servidor 8001 não sobe): a imagem instala mt5linux 1.0.3 (fev/2026),
#   que removeu o switch "-w" usado pelo start.sh dela → "Unknown switch -w".
#   Correção: fixar o mt5linux do lado Linux na 0.1.9 (última versão que aceita -w).
#
# Problema 2 (ponte conecta e cai — "invalid message type"): o RPyC do Python do
#   Wine é de uma versão diferente do RPyC do nosso cliente (6.0.2) → protocolos
#   incompatíveis. Correção: forçar o RPyC do Wine para 6.0.2, igual ao cliente.
#
# Este script roda ANTES do start.sh da imagem (custom-init do LSIO).
# ---------------------------------------------------------------------------
set -u

ABC_HOME="$(getent passwd abc | cut -d: -f6)"
[ -z "${ABC_HOME}" ] && ABC_HOME=/config
WINE_PY='C:\Program Files (x86)\Python39-32\python.exe'

echo "[fix-mt5linux] (1/2) Fixando mt5linux==0.1.9 no Linux (corrige gmag11 issue #28)..."
HOME="${ABC_HOME}" s6-setuidgid abc python3 -m pip install \
    --break-system-packages --no-cache-dir --no-deps --user --force-reinstall \
    "mt5linux==0.1.9" 2>&1 | sed 's/^/[fix-mt5linux] /'

echo "[fix-mt5linux] (2/2) Alinhando o RPyC do Wine em 6.0.2 (igual ao cliente)..."
# O Python do Wine já está instalado no volume /config/.wine (persistido). Se ainda
# não estiver (primeiríssimo boot), esta etapa falha silenciosamente e roda no próximo.
HOME="${ABC_HOME}" WINEPREFIX="${ABC_HOME}/.wine" WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree=d;mshtml=d" s6-setuidgid abc \
    wine "${WINE_PY}" -m pip install --no-cache-dir --upgrade "rpyc==6.0.2" \
    2>&1 | sed 's/^/[fix-mt5linux] /' \
    || echo "[fix-mt5linux] AVISO: não alinhei o RPyC do Wine agora (tentará no próximo boot)."

echo "[fix-mt5linux] Concluído."
exit 0
