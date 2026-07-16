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

# VWAP da B3 ancorada na ABERTURA DO PREGÃO (item 5) — a VWAP intradiária deve zerar quando o
# pregão abre (~09:00), não à meia-noite como no forex 24h. Valor em HORAS no relógio do servidor
# Genial. ⚠️ Assume que esse relógio mostra a hora local do pregão (WIN/WDO); se o fuso do servidor
# for outro, ajuste aqui (validar com os candles: a VWAP deve reiniciar junto com o 1º candle do dia).
VWAP_B3_ANCORA_HORA = int(os.environ.get("VWAP_B3_ANCORA_HORA", "9"))

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
    """Valor em BRL de 1 ponto de preço do símbolo B3 (fato de contrato).

    Casa por PREFIXO (WIN*/WDO*): `PARES_B3` é env-overridável e um símbolo com outro sufixo
    (WIN$, WINV26…) devolvia None → todo trade gravava lucro NULL e o P&L da B3 sumia em
    silêncio. O fato de contrato vale para a família toda, não para um sufixo específico."""
    v = VALOR_PONTO_B3.get(par)
    if v is not None:
        return v
    p = (par or "").upper()
    for prefixo, valor in (("WIN", 0.20), ("WDO", 10.0)):
        if p.startswith(prefixo):
            return valor
    return default


# --------------------------------------------------------------------------- #
# SOMBRA da B3 (ETAPA 8b, bloqueio (b)) — estrategista + executor de sombra
# --------------------------------------------------------------------------- #
# Liga o laboratório de sombra da B3: o `decisao_b3` roda as MESMAS estratégias (funções puras,
# agnósticas de mercado) sobre WIN/WDO e grava as decisões `mercado='b3'`; o `executor_b3`
# simula as operações ao vivo com o tick da ponte data-only da Genial e P&L em BRL. NADA é
# real (a ponte da B3 não tem função de ordem — impossível por construção). ADITIVO e ISOLADO:
# o forex não é tocado; o executor do forex ignora as decisões/trades `mercado='b3'`.
B3_SOMBRA_HABILITADA = os.environ.get(
    "B3_SOMBRA_HABILITADA", "true").lower() in ("1", "true", "sim")

# TFs de OPERAÇÃO da sombra da B3 (cada TF é um livro independente, como no forex). Default
# igual ao forex (M1/M5/M15) — M1 é observação (o custo come o alvo), M5/M15 são os candidatos.
TFS_OPERACAO_B3 = [s.strip() for s in os.environ.get(
    "TFS_OPERACAO_B3", "M1,M5,M15").split(",") if s.strip()]

# Janela de negociação da B3 — HORA DO SERVIDOR da Genial (o filtro usa a hora do candle). A B3
# só forma candles durante o pregão, então o default é PERMISSIVO (00–24 = sem corte de sessão):
# deixamos o próprio pregão ser o filtro e evitamos descartar tudo por um descasamento de fuso do
# feed. O corte FINO (minutos) da abertura fica em JANELA_ABERTURA_B3 (abaixo) — este par de horas
# é só a rede grossa legada; mantido permissivo para não conflitar com a janela de minutos.
SESSAO_B3 = (int(os.environ.get("SESSAO_B3_INICIO", "0")), int(os.environ.get("SESSAO_B3_FIM", "24")))

# --------------------------------------------------------------------------- #
# Janela de negociação FINA da B3 (pedido do dono) — precisão de MINUTOS (só B3)
# --------------------------------------------------------------------------- #
# Diferente do forex (24/5), a B3 tem pregão com horário fino e o volume CAI ao fim da tarde. O
# dono pediu, SÓ para a B3:
#   - ABRIR posições apenas entre 09:15 e 16:00 (encerra o gatilho mais cedo pela queda de volume);
#   - FECHAR à força qualquer posição do dia às 17:30 — a corretora zera as posições no fim do
#     pregão, independentemente do resultado; reproduzimos isso e CATALOGAMOS o motivo do
#     fechamento (aparece no /analitico "por motivo de saída").
# Tudo em MINUTOS-DO-DIA no relógio do servidor da Genial — o MESMO relógio do candle (decisao_b3)
# e do executor_b3 (ver VWAP_B3_ANCORA_HORA: assume que o relógio do servidor mostra a hora local
# do pregão; validar com os candles). Ajustável por env (formato "HH:MM").


def _hhmm_para_min(txt: str, default_min: int) -> int:
    """'HH:MM' (ou 'HH') → minutos-do-dia (0..1440); cai no default se malformado. Função PURA."""
    try:
        partes = str(txt).strip().split(":")
        h = int(partes[0])
        m = int(partes[1]) if len(partes) > 1 else 0
        if 0 <= h <= 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, IndexError, AttributeError):
        pass
    return default_min


# Janela em que a sombra da B3 pode ABRIR novas posições (gatilho). Fora dela, nenhuma entrada.
JANELA_ABERTURA_B3 = (
    _hhmm_para_min(os.environ.get("B3_ABERTURA_INICIO", "09:15"), 9 * 60 + 15),
    _hhmm_para_min(os.environ.get("B3_ABERTURA_FIM", "16:00"), 16 * 60),
)

# Horário do FECHAMENTO FORÇADO do pregão (a corretora zera as posições no fim do dia).
B3_FECHAMENTO_FORCADO_MIN = _hhmm_para_min(os.environ.get("B3_FECHAMENTO_FORCADO", "17:30"), 17 * 60 + 30)

# Motivo de saída CATALOGÁVEL do fechamento forçado (o dono quer o log do porquê p/ auditar).
MOTIVO_FECHAMENTO_PREGAO = "fechamento do pregao (17:30)"


def minuto_do_dia(epoch_servidor: int) -> int:
    """Minuto-do-dia (0..1439) de um epoch JÁ no fuso do servidor. Função PURA."""
    return (int(epoch_servidor) % 86400) // 60


def dentro_janela_abertura(minuto_dia: int, janela=None) -> bool:
    """True se `minuto_dia` está na janela de ABERTURA da B3 (09:15–16:00). Função PURA."""
    ini, fim = JANELA_ABERTURA_B3 if janela is None else janela
    return ini <= minuto_dia < fim


def hora_de_fechar_pregao(minuto_dia: int, corte_min: int = None) -> bool:
    """True se já passou do fechamento forçado do pregão (17:30). Função PURA."""
    corte = B3_FECHAMENTO_FORCADO_MIN if corte_min is None else corte_min
    return minuto_dia >= corte

# Filtro de spread da sombra da B3 (na régua interna pontos/10 do snapshot). Permissivo por ora
# (catalogar o máximo; a régua de spread da B3 ainda será reconciliada — nota da calibração).
SPREAD_MAX_B3 = float(os.environ.get("SPREAD_MAX_B3", "5.0"))

# Nº de contratos da posição de sombra (P&L = pontos_a_favor × valor_ponto × contratos).
CONTRATOS_B3 = int(os.environ.get("CONTRATOS_B3", "1"))

# Teto amplo de posições virtuais simultâneas da B3 (segurança — não trunca o catálogo).
MAX_POS_SOMBRA_B3 = int(os.environ.get("MAX_POS_SOMBRA_B3", "200"))

# Polls (segundos) do estrategista e do executor de sombra da B3.
DECISAO_B3_POLL_S = int(os.environ.get("DECISAO_B3_POLL_S", "5"))
GESTOR_B3_POLL_S = int(os.environ.get("GESTOR_B3_POLL_S", "1"))

# A cada quantos segundos a escala (tick/piso/teto de SL) é RE-DERIVADA dos candles por par
# (lição GOLD: nunca chutar a escala; deriva de `calibracao_b3` sobre os candles já coletados).
CALIB_REFRESH_S = int(os.environ.get("CALIB_REFRESH_S", "3600"))

# Overrides POR SÍMBOLO de escala da B3 (mesmo papel do config.PARAMS_SIMBOLO no forex). VAZIO
# por padrão de propósito: a escala é DERIVADA ao vivo da calibração (candles) — só fixar aqui
# quando o dono confirmar os números do dossiê `/b3`. Chaves: tamanho_pip, sl_min_pips,
# sl_max_pips, spread_max_pips.
PARAMS_SIMBOLO_B3 = {}


def param_simbolo_b3(par: str, chave: str, default=None):
    """Override de escala do símbolo B3 (PARAMS_SIMBOLO_B3) ou o `default` (calibração ao vivo)."""
    return PARAMS_SIMBOLO_B3.get(par, {}).get(chave, default)


def lucro_brl(direcao: str, entrada: float, saida: float, par: str,
              contratos: int = 1, valor_ponto_pt: float = None) -> float:
    """P&L em BRL da sombra da B3 (função PURA): pontos_a_favor × valor-por-ponto × contratos.

    A ponte da B3 é data-only (sem order_calc_profit), então o P&L é calculado do FATO de
    contrato (`VALOR_PONTO_B3`: WIN R$0,20/pt, WDO R$10/pt). None se não houver valor-por-ponto.
    """
    vp = valor_ponto_pt if valor_ponto_pt is not None else valor_ponto(par)
    if vp is None:
        return None
    pontos = (saida - entrada) if direcao == "compra" else (entrada - saida)
    return pontos * vp * contratos


def sombra_pares() -> list:
    """Pares B3 a operar na sombra — vazio se o módulo B3 ou a sombra estiverem desligados."""
    return list(PARES_B3) if (B3_HABILITADO and B3_SOMBRA_HABILITADA) else []


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
