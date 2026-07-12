# Imagem do MetaTrader 5 (base gmag11) com a correção do servidor da ponte RPyC.
#
# A base 2.3 roda o MT5 sob Wine 10 e expõe VNC (3000) + API Python (8001). Porém
# a versão do mt5linux que ela instala (1.0.3) quebra o start do servidor 8001
# (issue #28 — "Unknown switch -w"). O script de custom-init abaixo fixa o mt5linux
# em 0.1.9 (que aceita "-w"), rodando ANTES do start.sh da imagem.
FROM gmag11/metatrader5_vnc:2.3

# Custom-init do LSIO: scripts em /custom-cont-init.d rodam na inicialização,
# antes dos serviços (incluindo o start.sh que sobe o MT5 e a ponte).
COPY fix-mt5linux.sh /custom-cont-init.d/50-fix-mt5linux.sh
RUN chmod +x /custom-cont-init.d/50-fix-mt5linux.sh
