"""Configuração do módulo B3 (ETAPA 8) — feed de WIN/WDO via MT5 da Genial.

ADITIVO e ISOLADO do forex: este módulo NÃO altera `config.py` nem o caminho do XM.
O forex segue no terminal XM (`config.MT5_HOST`); o B3 usa um SEGUNDO terminal (Genial)
num container próprio (`mt5_b3`), lido por um coletor próprio (`coletor_b3`) por uma
ponte própria e DATA-ONLY (`mt5_bridge_b3` — sem funções de ordem).

Regra do dono: conta Genial é REAL, usada SÓ como fonte de cotações (nenhuma ordem é
enviada — o sistema fica em sombra). Por isso a ponte do B3 nem expõe abrir/fechar.

Segredos (login/senha da Genial, senha do VNC) vêm do Environment do Dokploy — nunca no git.
"""

import os

# --------------------------------------------------------------------------- #
# Conexão ao 2º terminal (Genial) — container `mt5_b3`, ponte RPyC na porta 8001
# --------------------------------------------------------------------------- #
MT5_B3_HOST = os.environ.get("MT5_B3_HOST", "mt5_b3")
MT5_B3_PORT = int(os.environ.get("MT5_B3_PORT", "8001"))
# Credenciais do terminal Genial — o login é feito UMA vez pela tela VNC do container
# mt5_b3 (persistido no volume). Mantidas aqui só para referência/health.
MT5_B3_LOGIN = os.environ.get("MT5_B3_LOGIN", "")
MT5_B3_SERVER = os.environ.get("MT5_B3_SERVER", "")     # ex.: "Genial-..." (definir no Dokploy)
MT5_B3_PASSWORD = os.environ.get("MT5_B3_PASSWORD", "")  # segredo — só no Environment

# --------------------------------------------------------------------------- #
# Símbolos monitorados na B3 (sombra/catálogo)
# --------------------------------------------------------------------------- #
# WIN = mini índice Bovespa; WDO = mini dólar. São FUTUROS com contrato mensal (WIN é par;
# WDO é mensal), então além do contrato específico (ex.: WINV25) o feed costuma expor um
# símbolo CONTÍNUO (rolagem automática) — o que queremos para histórico/gráfico. O nome
# exato varia por corretora/feed; confirme na Market Watch da Genial (VNC do mt5_b3) e
# ajuste PARES_B3 no Dokploy se necessário. O resolver tenta o par + os ALIASES abaixo.
PARES_B3 = [s.strip() for s in os.environ.get("PARES_B3", "WIN$N,WDO$N").split(",") if s.strip()]

# Nomes alternativos por par lógico (o resolver tenta cada um, na ordem, até achar no broker).
ALIASES_B3 = {
    "WIN$N": ["WIN$", "WIN$D", "WINFUT", "WIN"],
    "WDO$N": ["WDO$", "WDO$D", "WDOFUT", "WDO"],
}

# Timeframes coletados para o B3. Sem W1 por ora (D1 basta p/ contexto; foco é intradiário).
TFS_COLETA_B3 = [s.strip() for s in os.environ.get(
    "TFS_COLETA_B3", "M1,M5,M15,H1,D1").split(",") if s.strip()]

# --------------------------------------------------------------------------- #
# Backfill / loop
# --------------------------------------------------------------------------- #
BACKFILL_MESES_B3 = int(os.environ.get("BACKFILL_MESES_B3", "6"))
BACKFILL_MIN_BARRAS_B3 = int(os.environ.get("BACKFILL_MIN_BARRAS_B3", "300"))
# Teto do M1 (6 meses de M1 seriam ~50k+ velas; não precisamos disso p/ o catálogo).
BACKFILL_M1_BARRAS_B3 = int(os.environ.get("BACKFILL_M1_BARRAS_B3", "3000"))
BACKFILL_TENTATIVAS_B3 = int(os.environ.get("BACKFILL_TENTATIVAS_B3", "8"))
BACKFILL_ESPERA_S_B3 = int(os.environ.get("BACKFILL_ESPERA_S_B3", "3"))

# Intervalo (segundos) entre verificações de candle novo no loop do coletor B3.
COLETOR_B3_POLL_S = int(os.environ.get("COLETOR_B3_POLL_S", "5"))

# Habilita o módulo B3 por completo (desligável sem remover os serviços).
B3_HABILITADO = os.environ.get("B3_HABILITADO", "true").lower() in ("1", "true", "sim")


def pares_ativos() -> list:
    """Pares B3 a processar (motor/painel) — vazio se o módulo B3 estiver desligado.
    Isola o forex: quem chama itera `config.PARES + config_b3.pares_ativos()`."""
    return list(PARES_B3) if B3_HABILITADO else []


def candidatos_simbolo(par: str, aliases: dict = None) -> list:
    """Nomes a tentar no broker para um par lógico: o próprio + aliases, sem duplicar.

    Função PURA (testável sem MT5): a resolução real (symbol_info/select) fica na ponte.
    """
    aliases = ALIASES_B3 if aliases is None else aliases
    seq = [par] + list(aliases.get(par, []))
    out = []
    for nome in seq:
        if nome and nome not in out:
            out.append(nome)
    return out
