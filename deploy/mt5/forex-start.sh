#!/bin/bash
# ---------------------------------------------------------------------------
# Wrapper de autostart do container MT5 (gmag11/metatrader5_vnc).
#
# O openbox chama este script (via /config/.config/openbox/autostart, fixado
# pelo cont-init 60-forex-autostart.sh) NO LUGAR de /Metatrader/start.sh. Ele:
#
#   1. Roda o start.sh original da imagem (instala Wine Python + MT5 + mt5linux
#      e sobe a ponte RPyC na porta 8001).
#   2. Corrige o ABI do numpy no Python do Wine.
#
# Por que o passo 2 é necessário:
#   O start.sh instala "MetaTrader5==5.0.36" no Python do Wine SEM fixar o numpy.
#   O pip então puxa numpy 2.x, mas o módulo compilado do MetaTrader5 (._core) foi
#   construído contra numpy 1.x. No 1º import isso quebra com:
#       ImportError: numpy.core.multiarray failed to import
#   (o coletor/web veem isso via RPyC como remote traceback e ficam reiniciando).
#
#   A correção é forçar numpy<2 no Python do Wine. Como o processo do servidor da
#   ponte pode já ter carregado o numpy 2.x na memória, reiniciamos a ponte depois
#   de trocar o numpy no disco, para o servidor recarregar o numpy 1.x.
# ---------------------------------------------------------------------------
set -u

MT5_PORT="${MT5_PORT:-8001}"
log() { echo "[forex-start] $*"; }

# 1) Bring-up padrão da imagem (instala tudo e sobe a ponte em background).
log "Executando o start.sh da imagem..."
/Metatrader/start.sh

# 2) Verifica o numpy do Python do Wine.
NUMPY_VER="$(wine python -c 'import numpy,sys; sys.stdout.write(numpy.__version__)' 2>/dev/null)"
log "numpy no Python do Wine: ${NUMPY_VER:-<ausente/erro>}"

case "${NUMPY_VER}" in
  1.*)
    log "numpy já é 1.x — nada a corrigir."
    ;;
  *)
    # numpy 2.x, ausente, ou import quebrado: força a versão compatível.
    log "Fixando numpy<2 no Python do Wine (ABI do MetaTrader5 5.0.36)..."
    wine python -m pip install --no-cache-dir --force-reinstall "numpy<2" 2>&1 \
      | sed 's/^/[forex-start] pip: /'

    # 3) Reinicia a ponte para o servidor recarregar o numpy correto.
    log "Reiniciando a ponte mt5linux para recarregar o numpy..."
    pkill -f "mt5linux" 2>/dev/null || true
    sleep 2
    python3 -m mt5linux --host 0.0.0.0 -p "${MT5_PORT}" -w wine python.exe &
    log "Ponte reiniciada na porta ${MT5_PORT}."
    ;;
esac

log "Concluído."
