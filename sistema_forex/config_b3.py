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

# --------------------------------------------------------------------------- #
# Calibração de escala (ETAPA 8b) — derivar do banco, NÃO chutar (lição GOLD)
# --------------------------------------------------------------------------- #
# WIN/WDO têm escala MUITO diferente do forex (o índice move centenas de pontos por vela; o
# mini dólar move dezenas). A lição do OURO foi clara: um SL menor que a própria vela
# insta-estopa 100% dos trades (-1R). Antes de ligar o executor de sombra da B3, precisamos
# calibrar `tamanho_pip`/`sl_min`/`sl_max`/`spread_max` A PARTIR dos candles JÁ coletados —
# é o que `calibracao_b3.py` faz (funções puras + leitura do banco + dossiê para o dono).
# TF base da calibração (o SL do executor usa o ATR desse TF; M5 é a espinha dorsal do sistema).
CALIB_TF_BASE = os.environ.get("CALIB_TF_BASE", "M5")
# Quantos candles ler por TF na calibração (janela de volatilidade recente, sem varrer tudo).
CALIB_JANELA = int(os.environ.get("CALIB_JANELA", "1500"))
# Multiplicador de ATR do SL do executor (espelha config.SL_SERVIDOR_ATR_MULT) — a calibração
# dimensiona o piso/teto do SL para que o ATR×este_mult MANDE, sem clampar dentro do ruído.
CALIB_SL_MULT = float(os.environ.get("CALIB_SL_MULT", "3.0"))

# --------------------------------------------------------------------------- #
# Valor-por-ponto (BRL) — FATO DE CONTRATO, base do P&L em reais da sombra da B3
# --------------------------------------------------------------------------- #
# O P&L da B3 é em BRL e a ponte B3 é data-only (sem calc_lucro). Cada ponto de preço vale um
# valor fixo em reais por contrato: WIN (mini índice) = R$ 0,20/ponto (tick 5 pts = R$ 1,00);
# WDO (mini dólar) = R$ 10,00/ponto (tick 0,5 pt = R$ 5,00). Estes são os defaults conhecidos
# da B3; a calibração CONFIRMA via symbol_info (trade_tick_value/trade_tick_size) quando a
# ponte está acessível. lucro_brl = pontos_a_favor × valor_ponto × contratos.
VALOR_PONTO_B3 = {"WIN$N": 0.20, "WDO$N": 10.0}


def valor_ponto(par: str, default: float = None) -> float:
    """Valor em BRL de 1 ponto de preço do símbolo B3 (fato de contrato)."""
    return VALOR_PONTO_B3.get(par, default)


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
