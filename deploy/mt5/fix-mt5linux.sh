#!/usr/bin/with-contenv bash
# ---------------------------------------------------------------------------
# Correção do bug da imagem gmag11/metatrader5_vnc (issue #28).
#
# A imagem instala o mt5linux mais recente (1.0.3, fev/2026), que REMOVEU o
# switch "-w" usado pelo start.sh dela para iniciar o servidor da ponte. Isso
# gera "Error: Unknown switch -w" e o servidor RPyC (porta 8001) NÃO sobe.
#
# Aqui fixamos o mt5linux do lado Linux na 0.1.9 (única versão anterior à
# reescrita de fev/2026 e a que o start.sh espera, com "-w"). O start.sh roda
# depois deste script e, vendo o mt5linux já satisfeito, mantém a 0.1.9.
#
# O RPyC do Python do Wine (5.2.3) NÃO é alterado — o cliente é que fixa a MESMA
# versão (rpyc==5.2.3) para o protocolo bater. Ver sistema_forex/requirements.txt.
# ---------------------------------------------------------------------------
set -u

ABC_HOME="$(getent passwd abc | cut -d: -f6)"
[ -z "${ABC_HOME}" ] && ABC_HOME=/config

echo "[fix-mt5linux] Fixando mt5linux==0.1.9 no Linux (corrige gmag11 issue #28)..."
HOME="${ABC_HOME}" s6-setuidgid abc python3 -m pip install \
    --break-system-packages --no-cache-dir --no-deps --user --force-reinstall \
    "mt5linux==0.1.9" 2>&1 | sed 's/^/[fix-mt5linux] /'

echo "[fix-mt5linux] Concluído."
exit 0
