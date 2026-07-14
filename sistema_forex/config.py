"""Parâmetros centralizados do sistema (doc §9) + configuração de infraestrutura.

Regra do projeto: TODOS os parâmetros ficam aqui. Nada de número mágico espalhado.
Segredos (senhas, tokens, credenciais) vêm de variáveis de ambiente — nunca hardcoded.
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
# O banco fica num diretório de dados (volume Docker). Em dev, cai para ./dados.
DADOS_DIR = Path(os.environ.get("DADOS_DIR", BASE_DIR.parent / "dados"))
DB_PATH = Path(os.environ.get("DB_PATH", DADOS_DIR / "mercado.db"))

# --------------------------------------------------------------------------- #
# Parâmetros de mercado (doc §9 — valores iniciais)
# --------------------------------------------------------------------------- #
# Pares monitorados (sombra). Ajustável por env (Dokploy) sem mexer no código. Majors de
# spread razoável + OURO (GOLD) para catalogar — o ouro paga mais, mas é mais arriscado
# (spread/volatilidade altos): tem parâmetros próprios em PARAMS_SIMBOLO.
PARES = [s.strip() for s in os.environ.get(
    "PARES", "EURUSD#,GBPUSD#,USDCAD,USDJPY#,AUDUSD#,GBPJPY#,GOLD").split(",") if s.strip()]
TF_OPERACAO = "M5"
# Timeframes onde o estrategista roda OPERAÇÕES DE SOMBRA INDEPENDENTES (cada TF é um
# "livro" próprio: abre/gerencia sua posição virtual e é comparado no /analitico "Por
# timeframe"). M5 é a base; M1 e M15 entram para comparar qual TF tem melhor expectância.
# ATENÇÃO (skill §0.1): no M1 o spread come o alvo — é observação de sombra para comparar,
# NUNCA candidato a real sem auditar. Ajustável por env (ex.: TFS_OPERACAO="M5,M15").
TFS_OPERACAO = [s.strip() for s in os.environ.get("TFS_OPERACAO", "M1,M5,M15").split(",") if s.strip()]
# W1 (semanal) incluído: é onde estão os S/R mais fortes (junto de D1/H1). M1 coletado
# para as operações de sombra do M1 (não gera S/R — é só a vela de operação/ATR).
TFS_COLETA = ["M1", "M5", "M15", "H1", "D1", "W1"]
BACKFILL_MESES = 6
# Piso de barras por TF no backfill: garante histórico suficiente para os TFs altos
# (6 meses de W1 são só ~26 velas; precisamos de mais para ATR/S/R no semanal).
BACKFILL_MIN_BARRAS = int(os.environ.get("BACKFILL_MIN_BARRAS", "300"))
# Teto de barras por TF intradiário fino: 6 meses de M1 são ~180k velas (backfill enorme
# e banco inchado) e não precisamos disso — só ATR(14) + janela de sweep. Cap por env.
BACKFILL_MAX_BARRAS = {"M1": int(os.environ.get("BACKFILL_M1_BARRAS", "3000"))}

SWING_N_M1 = 3
SWING_N_M5 = 3
SWING_N_M15 = 5
SWING_N_H1 = 5

SR_CLUSTER_ATR = 0.5
SR_ROMPIMENTO_ATR = 0.3
SR_FORCA_MIN = 3
# S/R MAIS FORTES são de TFs MAIORES (pedido do dono): H1, Diário e Semanal.
# M5/M15 = ruído; não geram S/R. O W1/D1/H1 são as zonas de maior chance de boa entrada.
SR_TFS = [s.strip() for s in os.environ.get("SR_TFS", "H1,D1,W1").split(",") if s.strip()]
# Peso por TF de origem: Semanal > Diário > H1 (força cresce com o TF).
SR_TF_PESO = {"M15": 0.5, "H1": 1.0, "D1": 2.0, "W1": 3.0}
# Banda de "toque" ao nível = fração do ATR (para medir toques/rejeições).
SR_TOQUE_ATR = float(os.environ.get("SR_TOQUE_ATR", "0.25"))
# Zona não tocada há mais que isto (candles) perde força (doc §4.2).
SR_RECENCIA_CANDLES = int(os.environ.get("SR_RECENCIA_CANDLES", "500"))
# Força mínima (qualidade) para o nível ser persistido/usado — corta S/R fraco.
SR_QUALIDADE_MIN = float(os.environ.get("SR_QUALIDADE_MIN", "2.0"))
# CONFLUÊNCIA de S/R (pedido do dono): quando níveis do MESMO tipo (topos/fundos), de TFs
# diferentes, caem dentro desta fração do ATR (do TF de regime), formam uma ZONA — cada
# vizinho soma `SR_CONFLUENCIA_BONUS`×força. Zonas de confluência (topos/fundos alinhados)
# viram os S/R mais FORTES, que o preço mais respeita → as estratégias (que pontuam pela
# força do nível + rejeição) priorizam essas regiões. Desligável com bônus 0.
SR_CONFLUENCIA_ATR = float(os.environ.get("SR_CONFLUENCIA_ATR", "0.5"))
SR_CONFLUENCIA_BONUS = float(os.environ.get("SR_CONFLUENCIA_BONUS", "0.5"))
# Máximo de níveis por tipo (suporte/resistência) por par — anti-proliferação (só os melhores).
SR_MAX_POR_TIPO = int(os.environ.get("SR_MAX_POR_TIPO", "6"))

FVG_MIN_ATR = 0.3

# Order Block (doc/skill §4): zona da última vela contrária antes de um impulso com
# displacement (FVG). Detectar só em M15/H1 (M5 = ruído). `OB_MIN_ATR` = tamanho mínimo
# do imbalance (fração do ATR) que prova o displacement — reaproveita a régua do FVG.
OB_TFS = [s.strip() for s in os.environ.get("OB_TFS", "M15,H1").split(",") if s.strip()]
OB_MIN_ATR = float(os.environ.get("OB_MIN_ATR", "0.3"))

GAP_MIN_PIPS = 5
GAP_MAX_PIPS = 20

ADX_TENDENCIA = 25
ADX_LATERAL = 20

# Janela de negociação — HORA DO SERVIDOR (MetaTrader), pois o filtro usa a hora do candle
# (=servidor). Só abre trades com `inicio <= hora < fim`. Alargada p/ 04:00–21:00 (pedido do
# dono, 13/07) para catalogar mais operações e mais horários. Env-configurável no Dokploy.
# (Nome SESSAO_UTC mantido por compatibilidade; o VALOR é hora de servidor.)
SESSAO_UTC = (int(os.environ.get("SESSAO_INICIO", "4")), int(os.environ.get("SESSAO_FIM", "21")))
SPREAD_MAX_PIPS = 2.0

LOTE = 0.01
SL_SERVIDOR_ATR_MULT = 3.0
SL_MIN_PIPS = 12
SL_MAX_PIPS = 40
BE_TRIGGER_R = 1.0
DD_DIARIO_MAX_PCT = 5.0
# --- Modo MONITORAMENTO / CATALOGAÇÃO (sombra) vs. travas de risco (real) ---
# Objetivo da sombra: catalogar TUDO. Cada (par, tf, ESTRATÉGIA) roda sua própria posição
# virtual ao vivo, em paralelo, gerida tick a tick — assim comparamos estratégia × TF ×
# regime com amostra robusta e decidimos o que fica/sai/calibra. Por isso, em modo sombra
# (EXECUCAO_ATIVA=false) NÃO se aplica trava de correlação nem cap por livro; só um teto de
# segurança amplo (MAX_POS_SOMBRA) para não crescer sem limite. As travas abaixo valem só
# no modo REAL (proteção de conta), onde risco correlacionado importa de verdade.
MAX_POS_SOMBRA = int(os.environ.get("MAX_POS_SOMBRA", "200"))
# Caps do modo REAL, aplicados POR LIVRO DE TIMEFRAME: no máximo MAX_POS_POR_PAR posições
# por (par, tf) e MAX_POS_TOTAL simultâneas dentro do mesmo TF.
MAX_POS_POR_PAR = int(os.environ.get("MAX_POS_POR_PAR", "1"))
MAX_POS_TOTAL = int(os.environ.get("MAX_POS_TOTAL", "2"))
# Guarda de correlação (só REAL, e desligada por padrão a pedido do dono): exposição líquida
# MÁXIMA por moeda (em nº de posições). EURUSD e GBPUSD comprados = ambos short USD → USD
# líquido -2. Com o guard ligado e limite 1, a 2ª entrada correlacionada é bloqueada. Deixe
# GUARDA_CORRELACAO=true (e ajuste MAX_EXPOSICAO_MOEDA) para religar antes de operar real.
GUARDA_CORRELACAO = os.environ.get("GUARDA_CORRELACAO", "false").lower() in ("1", "true", "sim")
MAX_EXPOSICAO_MOEDA = int(os.environ.get("MAX_EXPOSICAO_MOEDA", "1"))
TEMPO_MAX_POSICAO_H = 8
SCORE_MIN_CONFLUENCIAS = 2

# Auditoria de custo (lição MASMC): edge fino morre com spread alto.
SPREAD_ALERTA_PIPS = 3.2

# --------------------------------------------------------------------------- #
# Conexão MT5 (via ponte mt5linux — o terminal roda no container "mt5" sob Wine)
# --------------------------------------------------------------------------- #
MT5_HOST = os.environ.get("MT5_HOST", "mt5")
MT5_PORT = int(os.environ.get("MT5_PORT", "8001"))
# Credenciais do terminal — o login é feito uma vez pela tela VNC (:3000).
# Mantidas aqui apenas para referência/health; o terminal já fica logado.
MT5_LOGIN = os.environ.get("MT5_LOGIN", "")            # ex: 336082748 (demo)
MT5_SERVER = os.environ.get("MT5_SERVER", "")          # ex: XMGlobal-MT5 9 (demo)
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")      # segredo — só no .env

# Timeout (segundos) para chamadas à ponte antes de considerar falha.
MT5_TIMEOUT_S = int(os.environ.get("MT5_TIMEOUT_S", "60"))

# --------------------------------------------------------------------------- #
# Sufixo de símbolo por par
# --------------------------------------------------------------------------- #
# XM Global usa "#" na maioria (EURUSD#), mas USDCAD vem sem sufixo.
# O coletor confirma o nome real via symbols_get na inicialização; este mapa é
# apenas o palpite inicial para o backfill.
SUFIXO_PADRAO = "#"

# Nomes-base alternativos por par lógico (o broker pode chamar o ouro de GOLD ou XAUUSD).
# O resolver tenta cada base com e sem o sufixo "#".
ALIASES_SIMBOLO = {
    "GOLD": ["XAUUSD"],
}

# --------------------------------------------------------------------------- #
# Parâmetros POR SÍMBOLO (escalas diferentes: forex vs. OURO)
# --------------------------------------------------------------------------- #
# O ouro se move DÓLARES por vela; 1 pip ≈ 0.01 e o spread é ~20–50 pontos. Sem override,
# o SL global (12–40 pips = só ~$0.40 no ouro) insta-estoparia todo trade e o filtro de
# spread (2.0) barraria quase tudo. Aqui cada símbolo sobrescreve o que precisa; o resto
# cai no default global. (spread_max_pips está na régua interna pontos/10 do snapshot.)
PARAMS_SIMBOLO = {
    # Ouro: pip≈0.01. ATENÇÃO — uma vela de ouro (M1/M5) move $10–$40 = 1000–4000 pips, então
    # um SL de $8 (800 pips) fica DENTRO do ruído e insta-estopa todo trade. O SL do ouro tem
    # de deixar o ATR×3 mandar: piso $8 (800), teto $60 (6000) só como guarda-corpo largo.
    # spread razoável do ouro ~20–50 pontos → 2.0–5.0 na régua interna; cap 6.0.
    "GOLD": {"spread_max_pips": 6.0, "sl_min_pips": 800, "sl_max_pips": 6000},
    # GBPJPY: cruzado VOLÁTIL e de spread mais largo (~25–40 pontos → 2.5–4.0 na régua
    # interna) — com o cap global 2.0 quase não entraria. pip=0.01 (JPY); dá um pouco mais
    # de folga no SL (ATR×3 do M5 nesse cruzado passa dos 40 pips às vezes).
    "GBPJPY#": {"spread_max_pips": 4.5, "sl_max_pips": 60},
}


def param_simbolo(par, chave, default):
    """Valor de um parâmetro para o par (override em PARAMS_SIMBOLO) ou o default global."""
    return PARAMS_SIMBOLO.get(par, {}).get(chave, default)

# --------------------------------------------------------------------------- #
# Painel web (autenticação)
# --------------------------------------------------------------------------- #
PAINEL_USUARIO = os.environ.get("PAINEL_USUARIO", "admin")
# Duas formas de definir a senha (o hash tem prioridade):
#  - PAINEL_SENHA_HASH: hash bcrypt (mais seguro; gerar com scripts/gerar_hash.py).
#  - PAINEL_SENHA: senha em texto — prático para definir direto no painel do Dokploy.
PAINEL_SENHA_HASH = os.environ.get("PAINEL_SENHA_HASH", "")
PAINEL_SENHA = os.environ.get("PAINEL_SENHA", "")
# Chave para assinar o cookie de sessão. Trocar em produção (.env).
SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
SESSAO_HORAS = int(os.environ.get("SESSAO_HORAS", "12"))
# Intervalo (segundos) do auto-refresh do painel — fetch leve do /api/status (sem
# recarregar o gráfico). Baixo para o status/regime/níveis atualizarem quase ao vivo.
PAINEL_REFRESH_S = int(os.environ.get("PAINEL_REFRESH_S", "5"))

# --------------------------------------------------------------------------- #
# Telegram (usado a partir das fases seguintes)
# --------------------------------------------------------------------------- #
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT", "")

# --------------------------------------------------------------------------- #
# Coletor
# --------------------------------------------------------------------------- #
# Intervalo (segundos) entre verificações de candle M5 fechado no loop.
# Baixo de propósito: detecta o candle fechado quase na hora (o candle em si é 5 min).
COLETOR_POLL_S = int(os.environ.get("COLETOR_POLL_S", "5"))

# Backfill: o MT5 baixa o histórico de forma ASSÍNCRONA na 1ª chamada logo após
# selecionar o símbolo (por isso, sem retry, só chegam ~9 candles). Repetimos a
# requisição enquanto a contagem cresce, dando tempo para o download completar.
BACKFILL_TENTATIVAS = int(os.environ.get("BACKFILL_TENTATIVAS", "8"))
BACKFILL_ESPERA_S = int(os.environ.get("BACKFILL_ESPERA_S", "3"))

# --------------------------------------------------------------------------- #
# Motor de análise (Fase 2)
# --------------------------------------------------------------------------- #
# Intervalo (segundos) entre recálculos do motor (níveis/estrutura/regime).
ANALISE_POLL_S = int(os.environ.get("ANALISE_POLL_S", "15"))
# Períodos dos indicadores (padrão de mercado).
ATR_PERIODO = int(os.environ.get("ATR_PERIODO", "14"))
ADX_PERIODO = int(os.environ.get("ADX_PERIODO", "14"))
# Quantos candles ler por TF para a análise (janela deslizante — memória recente).
ANALISE_JANELA = int(os.environ.get("ANALISE_JANELA", "1500"))
# TF de referência para o regime (ADX) e para gaps de sessão.
TF_REGIME = os.environ.get("TF_REGIME", "H1")
# Nº de swings por TF (fractal) — quantos candles de cada lado confirmam o pivô.
SWING_N = {"M1": SWING_N_M1, "M5": SWING_N_M5, "M15": SWING_N_M15, "H1": SWING_N_H1,
           "D1": SWING_N_H1, "W1": SWING_N_H1}

# --------------------------------------------------------------------------- #
# VWAP diária + bandas (ETAPA 3) — zona de valor que Wyckoff/fluxo usam
# --------------------------------------------------------------------------- #
# VWAP acumulada do dia de SERVIDOR (reset 00:00 servidor), calculada no TF `VWAP_TF`.
# Grava níveis vwap/vwap_sup1/vwap_inf1/vwap_sup2/vwap_inf2 (bandas ±k·σ ponderado por volume).
VWAP_HABILITADO = os.environ.get("VWAP_HABILITADO", "true").lower() in ("1", "true", "sim")
VWAP_TF = os.environ.get("VWAP_TF", "M5")
VWAP_K1 = float(os.environ.get("VWAP_K1", "1.0"))
VWAP_K2 = float(os.environ.get("VWAP_K2", "2.0"))

# --------------------------------------------------------------------------- #
# Fuzzy score (Fuzzy Wyckoff) + Sync Line + EV score (ETAPA 3)
# --------------------------------------------------------------------------- #
# O motor pontua cada candle FECHADO (cache por par/tf/candle em `fuzzy_scores`). Score 0–100
# (50 neutro), estado por cor e flags absorção/exaustão/transição. Desligável por env.
FUZZY_HABILITADO = os.environ.get("FUZZY_HABILITADO", "true").lower() in ("1", "true", "sim")
# TFs pontuados (finos + estruturais). Cada TF é uma linha de score no painel (ETAPA 4).
FUZZY_TFS = [s.strip() for s in os.environ.get("FUZZY_TFS", "M1,M5,M15,H1").split(",") if s.strip()]
# Janela deslizante (candles recentes) recalculada por ciclo — não varre o histórico todo.
FUZZY_JANELA = int(os.environ.get("FUZZY_JANELA", "120"))
# Nº de velas ANTERIORES usadas como referência (range/volume médios) na normalização.
FUZZY_REF_JANELA = int(os.environ.get("FUZZY_REF_JANELA", "20"))
# Sync Line: quais TFs formam o alinhamento micro (fino) e macro (estrutural).
SYNC_MICRO_TFS = [s.strip() for s in os.environ.get("SYNC_MICRO_TFS", "M1,M5").split(",") if s.strip()]
SYNC_MACRO_TFS = [s.strip() for s in os.environ.get("SYNC_MACRO_TFS", "M15,H1").split(",") if s.strip()]
# EV score no sinal (não bloqueia na v1 — só carimba a decisão no dados_json).
EV_HABILITADO = os.environ.get("EV_HABILITADO", "true").lower() in ("1", "true", "sim")

# --------------------------------------------------------------------------- #
# Estrategista / decisão (Fase 4 — modo sombra)
# --------------------------------------------------------------------------- #
# Intervalo (segundos) entre verificações de candle M5 novo para decidir.
DECISAO_POLL_S = int(os.environ.get("DECISAO_POLL_S", "5"))
# Proximidade (em ATR) do preço a um nível para contar como confluência.
NIVEL_PROX_ATR = float(os.environ.get("NIVEL_PROX_ATR", "0.5"))

# Nomes AMIGÁVEIS das estratégias (só para exibição no painel/análise). O código interno
# — ex. "sweep_choch_v1" — permanece estável no banco para não perder o histórico.
NOMES_ESTRATEGIAS = {
    "confluencia_v1": "Confluência (tendência / S-R)",
    "sweep_choch_v1": "Caça-stops + reversão",
    "order_block_v1": "Order block (reteste)",
    "pullback_tendencia_v1": "Pullback na tendência",
    "fecha_gap_v1": "Fechamento de gap",
    "pullback_rompimento_v1": "Pullback ao rompimento",
    "rompimento_extremos_v1": "Rompimento máx/mín do dia",
    "pullback_medias_v1": "Pullback a médias (EMA)",
    "pivot_confluencia_v1": "Pivot + confluência S/R",
    "fuzzy_puro_v1": "Fuzzy Puro (Variante B)",
}


def nome_estrategia(codigo):
    """Nome amigável da estratégia; devolve o próprio código se não houver mapeamento."""
    return NOMES_ESTRATEGIAS.get(codigo, codigo or "—")
# Rejeição no nível: pavio contrário ≥ esta fração do range (doc §6.2). Por padrão a
# rejeição é apenas uma CONFLUÊNCIA (soma no score); não obriga a entrada — para não
# engessar. Ligue EXIGIR_REJEICAO_SR para torná-la gate no fade lateral (modo estrito).
REJEICAO_PAVIO_MIN = float(os.environ.get("REJEICAO_PAVIO_MIN", "0.5"))
EXIGIR_REJEICAO_SR = os.environ.get("EXIGIR_REJEICAO_SR", "false").lower() in ("1", "true", "sim")

# --- 2ª estratégia: liquidity sweep + CHoCH no M5 (doc/skill §4) ---
# Roda em paralelo à confluencia_v1 (cada uma grava sua decisão; o executor deduplica no
# nível de posição). Desligável por env se a sombra mostrar que não presta.
SWEEP_HABILITADA = os.environ.get("SWEEP_HABILITADA", "true").lower() in ("1", "true", "sim")
# Janela de candles M5 lida para procurar o padrão (≈ N velas de memória recente).
SWEEP_JANELA = int(os.environ.get("SWEEP_JANELA", "60"))
# Tamanho do fractal (candles de cada lado) para os swings do M5 no padrão.
SWEEP_N_SWING = int(os.environ.get("SWEEP_N_SWING", str(SWING_N_M5)))
# Penetração mínima do pavio ALÉM do nível varrido (fração do ATR) — evita "sweep" de tick.
SWEEP_MIN_ATR = float(os.environ.get("SWEEP_MIN_ATR", "0.1"))
# O sweep tem de ter ocorrido há no máximo estes candles do CHoCH (follow-through no tempo).
SWEEP_RECENTE = int(os.environ.get("SWEEP_RECENTE", "6"))

# --- 3ª estratégia: reteste de Order Block (M15/H1) + rejeição ---
# Entra quando o preço RETESTA uma zona de OB fresca na direção do OB. S/R e regime a
# favor entram como REFORÇO (nunca veto). A rejeição na borda é confluência; só vira gate
# se EXIGIR_REJEICAO_OB=true (modo estrito).
OB_HABILITADA = os.environ.get("OB_HABILITADA", "true").lower() in ("1", "true", "sim")
EXIGIR_REJEICAO_OB = os.environ.get("EXIGIR_REJEICAO_OB", "false").lower() in ("1", "true", "sim")

# --- 4ª estratégia: pullback a favor da tendência (H1) + rejeição em S/R forte ---
# Tese própria: em tendência, o preço recua a um S/R FORTE na direção da tendência, REJEITA
# (candle_rejeicao) e retoma. A rejeição é o GATILHO (obrigatória aqui). OB fresco coincidente
# soma como reforço.
PULLBACK_HABILITADA = os.environ.get("PULLBACK_HABILITADA", "true").lower() in ("1", "true", "sim")

# --- 5ª estratégia: fechamento de gap (fade rumo ao fechamento anterior) ---
# Gap de sessão/notícia tende a ser preenchido. Opera a favor do fill quando a vela vira
# na direção do alvo (momentum) e o gap ainda tem ESPAÇO. S/R no alvo/rejeição = reforço.
GAP_HABILITADA = os.environ.get("GAP_HABILITADA", "true").lower() in ("1", "true", "sim")
# Espaço mínimo (fração do ATR) do preço até o alvo do gap para valer a pena entrar.
FECHA_GAP_MIN_ATR = float(os.environ.get("FECHA_GAP_MIN_ATR", "0.5"))

# --- 6ª estratégia: pullback ao rompimento (reteste com inversão de polaridade) ---
# Um nível S/R rompido (BOS) é retestado já invertido (resistência→suporte, suporte→resistência)
# e rejeita. Direção pelo BOS; a rejeição no reteste é o gatilho. Regime/força = reforço.
ROMPIMENTO_HABILITADA = os.environ.get("ROMPIMENTO_HABILITADA", "true").lower() in ("1", "true", "sim")

# --- 7ª estratégia: rompimento da máx/mín do dia anterior (PDH/PDL) + reteste ---
# Preço rompe a máxima/mínima do dia anterior (liquidez clássica) e retesta o nível rompido,
# rejeitando. A rejeição no reteste é o gatilho; regime a favor = reforço.
EXTREMOS_HABILITADA = os.environ.get("EXTREMOS_HABILITADA", "true").lower() in ("1", "true", "sim")

# --- 8ª estratégia: pullback a médias (EMA9/EMA20 do TF acima) em tendência ---
# A favor da tendência, o preço recua e toca a EMA9/EMA20 do TF SUPERIOR e retoma. FVG/OB
# coincidente DOBRA o score; rejeição/regime = reforço. Variante A (grupo de controle).
MEDIAS_HABILITADA = os.environ.get("MEDIAS_HABILITADA", "true").lower() in ("1", "true", "sim")
# TF de operação → TF ACIMA lido para as médias de contexto (tendência do timeframe maior).
TF_ACIMA = {"M1": "M5", "M5": "M15", "M15": "H1", "H1": "D1"}
# Quantos closes do TF acima ler para as médias (>= 200 p/ a SMA200 fechar).
MEDIAS_JANELA = int(os.environ.get("MEDIAS_JANELA", "260"))

# --- 9ª estratégia: toque em pivot (PP/R1/S1) confluente com S/R/OB + rejeição ---
# Fade de um pivot clássico que coincide (< PIVOT_SR_ATR×ATR) com uma zona de S/R ou OB, com
# rejeição no candle. Lateral é o terreno natural (fade); regime a favor = reforço.
PIVOT_HABILITADA = os.environ.get("PIVOT_HABILITADA", "true").lower() in ("1", "true", "sim")
# Distância máxima (fração do ATR) entre o pivot e a zona de S/R/OB para contar como confluência.
PIVOT_SR_ATR = float(os.environ.get("PIVOT_SR_ATR", "0.5"))

# --------------------------------------------------------------------------- #
# Variante B — Fuzzy Puro (ETAPA 5). Reprodução fiel do operacional Fuzzy Wyckoff.
# --------------------------------------------------------------------------- #
# Roda em SOMBRA marcada variante=B_FUZZY_PURO (estratégia própria fuzzy_puro_v1) — grupo
# PARALELO às 9 estratégias da Variante A (nada é alterado; tudo é aditivo). Avalia UMA vez
# por par, no TF de TIMING (M1), usando a pirâmide MTF: M15=maré, M5=correnteza, M1=timing.
FUZZY_B_HABILITADA = os.environ.get("FUZZY_B_HABILITADA", "true").lower() in ("1", "true", "sim")
# TF do gatilho (timing) — a Variante B só grava no livro deste TF (evita duplicar por TF).
FUZZY_B_TIMING_TF = os.environ.get("FUZZY_B_TIMING_TF", "M1")
# TFs da pirâmide MTF lidos do fuzzy (maré=M15, correnteza=M5, timing=M1).
FUZZY_B_PIRAMIDE = [s.strip() for s in os.environ.get("FUZZY_B_PIRAMIDE", "M1,M5,M15").split(",") if s.strip()]
# Limiares da pirâmide MTF (score fuzzy 0–100; o lado vendedor é o espelho: score <= 100-limiar).
FUZZY_B_MARE_MIN = float(os.environ.get("FUZZY_B_MARE_MIN", "60"))       # M15 (maré/viés macro)
FUZZY_B_CORRENTE_MIN = float(os.environ.get("FUZZY_B_CORRENTE_MIN", "55"))  # M5 (correnteza)
FUZZY_B_TIMING_MIN = float(os.environ.get("FUZZY_B_TIMING_MIN", "58"))   # M1 (timing/gatilho)
# Força do candle-gatilho: corpo >= K × desvio-padrão dos 20 closes (energia com lastro).
FUZZY_B_STD_K = float(os.environ.get("FUZZY_B_STD_K", "1.0"))
# Nº mínimo de itens do checklist de 6 para entrar (5 de 6 = alto padrão, fiel ao didático).
FUZZY_B_CHECKLIST_MIN = int(os.environ.get("FUZZY_B_CHECKLIST_MIN", "5"))

# --------------------------------------------------------------------------- #
# Executor + gestor de saída (Fase 5)
# --------------------------------------------------------------------------- #
# TRAVA DE SEGURANÇA. false = simulação sobre preço AO VIVO (nenhuma ordem é
# enviada). true = envia e gerencia TODAS as ordens de verdade na conta demo (exige
# "Algo Trading" habilitado no terminal). Mude só quando decidir operar tudo real.
EXECUCAO_ATIVA = os.environ.get("EXECUCAO_ATIVA", "false").lower() in ("1", "true", "sim")

# --- Modo PARALELO CURADO (validação em DEMO) -------------------------------- #
# A SOMBRA segue catalogando TODAS as combinações (virtual) E, em paralelo, um livro
# REAL (demo) roda só para as combinações JÁ POSITIVAS — para comparar o que a sombra
# ASSUME (preço-sinal) com o fill REAL: spread no fill, DERRAPAGEM (fill vs assumido) e
# DELAY (decisão→execução). Cada sinal curado abre um GÊMEO real ao lado do virtual.
# Ligar SÓ em conta DEMO. Ignorado se EXECUCAO_ATIVA=true (aí é tudo real).
EXECUCAO_REAL_CURADA = os.environ.get("EXECUCAO_REAL_CURADA", "false").lower() in ("1", "true", "sim")
# Estratégias e TFs elegíveis ao livro real curado (as positivas na sombra; pula M1).
EXEC_REAL_ESTRATEGIAS = [s.strip() for s in os.environ.get(
    "EXEC_REAL_ESTRATEGIAS", "confluencia_v1,fecha_gap_v1").split(",") if s.strip()]
EXEC_REAL_TFS = [s.strip() for s in os.environ.get("EXEC_REAL_TFS", "M5,M15").split(",") if s.strip()]
# Teto de posições REAIS simultâneas no demo (protege a margem da conta de validação).
MAX_POS_REAL = int(os.environ.get("MAX_POS_REAL", "12"))


def combo_real(estrategia, tf):
    """(estratégia, tf) é elegível ao livro REAL curado (demo)?"""
    return estrategia in EXEC_REAL_ESTRATEGIAS and tf in EXEC_REAL_TFS
# Intervalo (segundos) do gestor de saída — poll de tick por posição aberta.
GESTOR_POLL_S = int(os.environ.get("GESTOR_POLL_S", "1"))
# Saída por reversão: depois de atingir BE_TRIGGER_R, fecha se ceder este tanto de R
# do pico favorável (a "força contrária" no preço).
GIVEBACK_R = float(os.environ.get("GIVEBACK_R", "0.7"))

# --- Saída por força contrária de ESTRUTURA (SMC), "com direito a desenvolver" ---
# Pedido do dono: a força contrária não pode ser instantânea (fechava com centavos de
# lucro em qualquer BOS de ruído), e o preço precisa ter ESPAÇO para desenvolver.
# Só protege lucro já feito: exige r >= este valor antes de sair por estrutura contrária.
SAIDA_ESTRUTURA_MIN_R = float(os.environ.get("SAIDA_ESTRUTURA_MIN_R", "1.0"))
# TFs de estrutura que valem para SAIR. M5 é ruído (a estrutura de trade é M15/H1),
# por isso fica de fora por padrão — evita sair a cada BOS de 1 candle no M5.
SAIDA_ESTRUTURA_TFS = [s.strip() for s in
                       os.environ.get("SAIDA_ESTRUTURA_TFS", "M15,H1").split(",") if s.strip()]
# Espaço mínimo (em R) até o próximo nível contrário para AINDA segurar a posição quando
# o sinal contrário é FRACO (BOS). Com espaço >= isto e BOS → mantém (deixa o preço andar);
# CHOCH (reversão confirmada) sai assim que há o lucro mínimo, independentemente do espaço.
SAIDA_ESPACO_SEGURAR_R = float(os.environ.get("SAIDA_ESPACO_SEGURAR_R", "1.0"))
# Identificador das ordens do robô (magic number) e base de saldo p/ P&L simulado.
MAGIC = int(os.environ.get("MAGIC", "500250"))
SALDO_SIMULADO = float(os.environ.get("SALDO_SIMULADO", "1000"))

# Raio-X do trade (/trade/{id}): quantos candles do PRÓPRIO TF do trade desenhar ANTES da
# entrada (contexto que levou à decisão) e DEPOIS do fechamento (o que o preço fez em seguida
# — revela stop no ruído, alvo curto, entrada adiantada). Como os candles ficam no banco, o
# "futuro" se preenche sozinho com o tempo; auditar dias depois mostra o desfecho completo.
GRAFICO_TRADE_BARRAS_ANTES = int(os.environ.get("GRAFICO_TRADE_BARRAS_ANTES", "60"))
GRAFICO_TRADE_BARRAS_DEPOIS = int(os.environ.get("GRAFICO_TRADE_BARRAS_DEPOIS", "40"))

# Auditoria IA — "raio-x textual" das perdedoras: candles em pips relativos à entrada que a IA
# lê para analisar o price action (stop real, confirmação do padrão, entrada adiantada). Janela
# menor que a do gráfico visual porque a IA lê candle a candle; nº de perdedoras com raio-x
# embutido no dossiê é limitado (o resto sai sob demanda em /api/raiox/{id}).
AUDITORIA_RAIOX_ANTES = int(os.environ.get("AUDITORIA_RAIOX_ANTES", "30"))
AUDITORIA_RAIOX_DEPOIS = int(os.environ.get("AUDITORIA_RAIOX_DEPOIS", "30"))
AUDITORIA_RAIOX_TRADES = int(os.environ.get("AUDITORIA_RAIOX_TRADES", "6"))
# Quantas perdedoras entram na simulação de "saída por invalidação" (CHoCH oposto): mede se
# cortar o perdedor num padrão de reversão forte reduziria o prejuízo (vs esperar o stop).
AUDITORIA_INVALIDACAO_TRADES = int(os.environ.get("AUDITORIA_INVALIDACAO_TRADES", "60"))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")  # DEBUG desde a v1 (regra do projeto)
