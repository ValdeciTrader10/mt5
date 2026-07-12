# Imagem do MetaTrader 5 (base gmag11) com duas correções: o servidor da ponte
# RPyC e o ABI do numpy do Python do Wine.
#
# A base 2.3 roda o MT5 sob Wine 10 e expõe VNC (3000) + API Python (8001). Porém:
#
#   1) A versão do mt5linux que ela instala (1.0.3) quebra o start do servidor
#      8001 (issue #28 — "Unknown switch -w"). O cont-init 50-fix-mt5linux.sh fixa
#      o mt5linux em 0.1.9 (que aceita "-w"), rodando ANTES do start.sh da imagem.
#
#   2) O start.sh instala "MetaTrader5==5.0.36" no Python do Wine sem fixar o numpy;
#      o pip puxa numpy 2.x e o ._core (compilado contra numpy 1.x) quebra com
#      "numpy.core.multiarray failed to import". Corrigimos isso substituindo o
#      autostart do openbox pelo wrapper forex-start.sh, que roda o start.sh e
#      depois força numpy<2 no Python do Wine (ver forex-start.sh).
FROM gmag11/metatrader5_vnc:2.3

# Custom-init do LSIO: scripts em /custom-cont-init.d rodam na inicialização,
# antes dos serviços (incluindo o desktop/openbox que dispara o start.sh).
COPY fix-mt5linux.sh /custom-cont-init.d/50-fix-mt5linux.sh
COPY set-autostart.sh /custom-cont-init.d/60-forex-autostart.sh

# Wrapper de autostart que roda o start.sh e corrige o numpy do Wine.
COPY forex-start.sh /custom/forex-start.sh

RUN chmod +x /custom-cont-init.d/50-fix-mt5linux.sh \
             /custom-cont-init.d/60-forex-autostart.sh \
             /custom/forex-start.sh
