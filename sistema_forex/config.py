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
# spread razoável. GOLD REMOVIDO (15/07, pedido do dono): o ouro é outlier de USD (velas de
# dezenas de dólares) que INFLAVA o lucro agregado sem edge real em R — a auditoria mostrou que
# o "+USD" do forex vinha do GOLD, não de expectância. Os params de GOLD ficam em PARAMS_SIMBOLO
# (inertes enquanto fora de PARES); reincluir é só voltar "GOLD" aqui ou no env PARES do Dokploy.
PARES = [s.strip() for s in os.environ.get(
    "PARES", "EURUSD#,GBPUSD#,USDCAD,USDJPY#,AUDUSD#,GBPJPY#").split(",") if s.strip()]
TF_OPERACAO = "M5"
# Timeframes onde o estrategista roda OPERAÇÕES DE SOMBRA INDEPENDENTES (cada TF é um
# "livro" próprio: abre/gerencia sua posição virtual e é comparado no /analitico "Por
# timeframe"). M1 REMOVIDO do FOREX (15/07, pedido do dono): a auditoria (1657 trades) provou que
# o M1 é RALO — negativo/fino em todas as estratégias pelo custo/spread (skill §0.1), e as células
# M1 estáveis no split-half eram todas NEGATIVAS. Ficam M5/M15 (onde vive o edge). O M1 SEGUE sendo
# COLETADO (TFS_COLETA) e alimenta a pirâmide fuzzy/sync — só não gera mais operação no forex.
# ⚠️ Consequência: a Variante B (fuzzy_puro, timing=M1) deixa de rodar no forex; a B3 usa
# TFS_OPERACAO_B3 (próprio) e NÃO é afetada. Ajustável por env (Dokploy).
TFS_OPERACAO = [s.strip() for s in os.environ.get("TFS_OPERACAO", "M5,M15").split(",") if s.strip()]
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
# Teto ampliado p/ 1200: com C/C_CORRE + D_LINHAS (4) + E_SENTINELA (3) o catálogo cresceu — 1200
# evita truncar a amostra dos grupos novos. Ajustável por env no Dokploy.
MAX_POS_SOMBRA = int(os.environ.get("MAX_POS_SOMBRA", "1200"))
# Idade máxima de uma decisão para virar posição (segundos, medida em UTC contra criada_utc).
# Guarda contra abrir sinal VELHO após downtime (redeploy/fim de semana): sem preço vivo da
# hora do sinal, a simulação seria desonesta (tick atual ≠ contexto da decisão).
ENTRADA_MAX_ATRASO_S = int(os.environ.get("ENTRADA_MAX_ATRASO_S", "300"))
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
    "order_block_rej_v1": "Order block + rejeição",
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

# --- 2b. Gêmea da caça-stops COM filtro de ABSORÇÃO (sweep_choch_abs_v1) ---
# Livro de sombra INDEPENDENTE da sweep_choch_v1 (mesma detecção + gates), mas só entra se a
# vela da varredura mostrar ABSORÇÃO (volume alto + corpo fraco). A/B do dono: a caça-stops é
# mais lucrativa COM ou SEM o filtro de absorção? (a sweep_choch_v1 fica intocada como controle).
SWEEP_ABS_HABILITADA = os.environ.get("SWEEP_ABS_HABILITADA", "true").lower() in ("1", "true", "sim")
# Candles anteriores usados como referência p/ medir volume/range médios da absorção.
SWEEP_ABS_JANELA = int(os.environ.get("SWEEP_ABS_JANELA", "20"))

# --- 3ª estratégia: reteste de Order Block (M15/H1) + rejeição ---
# Entra quando o preço RETESTA uma zona de OB fresca na direção do OB. S/R e regime a
# favor entram como REFORÇO (nunca veto). A rejeição na borda é confluência; só vira gate
# se EXIGIR_REJEICAO_OB=true (modo estrito).
OB_HABILITADA = os.environ.get("OB_HABILITADA", "true").lower() in ("1", "true", "sim")
EXIGIR_REJEICAO_OB = os.environ.get("EXIGIR_REJEICAO_OB", "false").lower() in ("1", "true", "sim")
# Gêmeo A/B do order block: MESMA detecção, mas exige REJEIÇÃO na borda do bloco (a vela testa e
# volta). Livro `order_block_rej_v1` comparável ao `order_block_v1` — a auditoria da C_HIBRIDA
# mostrou 28/28 perdedoras indo contra de imediato (entrada sem confirmação). Sombra decide.
OB_REJ_HABILITADA = os.environ.get("OB_REJ_HABILITADA", "true").lower() in ("1", "true", "sim")

# --- 4ª estratégia: pullback a favor da tendência (H1) + rejeição em S/R forte ---
# Tese própria: em tendência, o preço recua a um S/R FORTE na direção da tendência, REJEITA
# (candle_rejeicao) e retoma. A rejeição é o GATILHO (obrigatória aqui). OB fresco coincidente
# soma como reforço.
PULLBACK_HABILITADA = os.environ.get("PULLBACK_HABILITADA", "true").lower() in ("1", "true", "sim")

# --- 5ª estratégia: fechamento de gap (fade rumo ao fechamento anterior) ---
# Gap de sessão/notícia tende a ser preenchido. Opera a favor do fill quando a vela vira
# na direção do alvo (momentum) e o gap ainda tem ESPAÇO. S/R no alvo/rejeição = reforço.
# ⚠️ APOSENTADA (18/07, decisão do dono): a auditoria 3-vias mostrou fade FRACO — negativo nas 3
# variantes (A −0,038R · C_CORRE −0,099R · C_HIBRIDA −0,109R), e a camada fuzzy o piora. Default
# OFF; a função pura fica no código (reversível: `GAP_HABILITADA=true` religa após reauditar zerado).
GAP_HABILITADA = os.environ.get("GAP_HABILITADA", "false").lower() in ("1", "true", "sim")
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
# A/B da maré (item 4): livro PARALELO da Variante B com a maré FIEL ao PDF (Lima=76, mais seletiva).
# Roda ao lado de fuzzy_puro_v1 (maré 60/verde) como estratégia `fuzzy_puro_lima_v1` — os dados dizem
# se exigir Lima (menos sinais, mais qualidade) rende mais que verde. Desligável.
FUZZY_B2_HABILITADA = os.environ.get("FUZZY_B2_HABILITADA", "true").lower() in ("1", "true", "sim")
FUZZY_B2_MARE_MIN = float(os.environ.get("FUZZY_B2_MARE_MIN", "76"))     # maré Lima (fiel ao PDF)
FUZZY_B_CORRENTE_MIN = float(os.environ.get("FUZZY_B_CORRENTE_MIN", "55"))  # M5 (correnteza)
FUZZY_B_TIMING_MIN = float(os.environ.get("FUZZY_B_TIMING_MIN", "58"))   # M1 (timing/gatilho)
# Força do candle-gatilho: corpo >= K × desvio-padrão dos 20 closes (energia com lastro).
FUZZY_B_STD_K = float(os.environ.get("FUZZY_B_STD_K", "1.0"))
# Nº mínimo de itens do checklist de 6 para entrar (5 de 6 = alto padrão, fiel ao didático).
FUZZY_B_CHECKLIST_MIN = int(os.environ.get("FUZZY_B_CHECKLIST_MIN", "5"))

# --------------------------------------------------------------------------- #
# Variante C — Híbrida (ETAPA 6). As 9 estratégias da Variante A + 7 integrações fuzzy.
# --------------------------------------------------------------------------- #
# Grupo PARALELO (aditivo): NÃO altera nenhuma estratégia. Espelha cada decisão "entrou" da
# Variante A e aplica a camada fuzzy (veto de absorção/exaustão, maré M15, virada na zona, esforço
# do sweep, localização VWAP), marcando variante=C_HIBRIDA. O livro C é o subconjunto fuzzy-filtrado
# do A → A vs C fica direto comparável no relatório (ETAPA 7). Limiares reaproveitam os da Variante B.
HIBRIDA_HABILITADA = os.environ.get("HIBRIDA_HABILITADA", "true").lower() in ("1", "true", "sim")
# Saída da Variante C (funções puras prontas p/ o executor plugar; a sombra usa a saída genérica):
#  - score M5 do lado oposto além deste limiar → saída antecipada (integração 5);
#  - sob exaustão, aperta o stop por esta fração da distância ao preço (integração 6).
HIBRIDA_SAIDA_M5_MIN = float(os.environ.get("HIBRIDA_SAIDA_M5_MIN", "60"))
HIBRIDA_STOP_APERTO = float(os.environ.get("HIBRIDA_STOP_APERTO", "0.5"))
# CARÊNCIA (grace) antes de a saída antecipada/técnica por variante (B/C) poder fechar: a posição
# tem de viver ao menos ESTE número de velas do SEU TF. Sem isso, o fuzzy M5 no PRIMEIRO ciclo após
# a abertura fechava a ordem no mesmo minuto (−1 pip, "não deixou andar") — o M5 no instante da
# entrada é a foto da entrada, não uma MUDANÇA de contexto. Corrige o bug visto na sombra da B3
# (14/07): 100% das C fechavam com ~0 de movimento. O aperto de stop na exaustão (só APROXIMA) não
# é afetado. Alinhado à metodologia do dono ("ativou, deixa o preço andar").
HIBRIDA_SAIDA_MIN_CANDLES = float(os.environ.get("HIBRIDA_SAIDA_MIN_CANDLES", "2"))
# Minutos por TF (para converter idade da posição em nº de velas do próprio TF).
MINUTOS_TF = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}

# --------------------------------------------------------------------------- #
# FAMÍLIA "LINHAS FUZZY" (Variante D_LINHAS) — estratégias pela DINÂMICA das curvas de score por TF
# --------------------------------------------------------------------------- #
# Grupo PARALELO/aditivo (não toca A/B/C): 4 estratégias que leem o MOVIMENTO das linhas de score
# (não o nível estático). Cada uma é um livro de sombra próprio, comparável no /relatorio como um 4º
# cenário. Janela da série de scores/preço alinhada por candle.
LINHAS_JANELA = int(os.environ.get("LINHAS_JANELA", "60"))       # nº de velas da série (preço+score)
LINHAS_N_SWING = int(os.environ.get("LINHAS_N_SWING", str(SWING_N_M5)))  # fractal p/ swings de preço
# A — Divergência esforço×resultado (score vs preço) na banda da VWAP. Reversão.
DIVERGENCIA_HABILITADA = os.environ.get("DIVERGENCIA_HABILITADA", "true").lower() in ("1", "true", "sim")
# B — Pullback do leque: linha rápida (TF de operação) recua contra a lenta (TF acima) e REENGATA na
# direção da maré (M15), no valor da VWAP. Continuação.
PULLBACK_LEQUE_HABILITADA = os.environ.get("PULLBACK_LEQUE_HABILITADA", "true").lower() in ("1", "true", "sim")
LEQUE_MARE_MIN = float(os.environ.get("LEQUE_MARE_MIN", "60"))   # M15 (maré) p/ o pullback do leque
LEQUE_DIP_JANELA = int(os.environ.get("LEQUE_DIP_JANELA", "6"))  # velas p/ ter havido o recuo (dip/pop)
# C — Sync flip: linhas SAEM de divergência e ALINHAM (Sync amarelo→verde/vermelho) rompendo a VWAP.
SYNC_FLIP_HABILITADA = os.environ.get("SYNC_FLIP_HABILITADA", "true").lower() in ("1", "true", "sim")
# D — Exaustão: score preso no extremo por N velas e ROLA (clímax) na banda ±2σ. Fade de exaustão.
EXAUSTAO_HABILITADA = os.environ.get("EXAUSTAO_HABILITADA", "true").lower() in ("1", "true", "sim")
EXAUSTAO_SAT_CANDLES = int(os.environ.get("EXAUSTAO_SAT_CANDLES", "4"))  # velas saturadas antes do rollover
EXAUSTAO_SAT_ALTO = float(os.environ.get("EXAUSTAO_SAT_ALTO", "80"))     # saturação de compra (>=)
EXAUSTAO_SAT_BAIXO = float(os.environ.get("EXAUSTAO_SAT_BAIXO", "20"))   # saturação de venda (<=)

# --------------------------------------------------------------------------- #
# FAMÍLIA E_SENTINELA — FORÇA contínua (micro/macro) + LEQUE (5º cenário, inspirado no Sentinela do PDF)
# --------------------------------------------------------------------------- #
# Linha de FORÇA contínua no painel (comparável às 4 linhas de TF) + 3 estratégias próprias. A "força"
# é o esforço direcional micro(M1/M5)/macro(M15/H1) contínuo (fuzzy_score.forca_sync); o leque é a
# amplitude entre as linhas. Grupo aditivo/desligável — 5º dado estatístico p/ comparar na sombra.
SENTINELA_HABILITADA = os.environ.get("SENTINELA_HABILITADA", "true").lower() in ("1", "true", "sim")
SENT_FORCA_MIN = float(os.environ.get("SENT_FORCA_MIN", "60"))       # limiar da linha de força p/ estouro
SENT_LEQUE_ESTREITO = float(os.environ.get("SENT_LEQUE_ESTREITO", "15"))  # leque comprimido (mola)
SENT_LEQUE_LARGO = float(os.environ.get("SENT_LEQUE_LARGO", "30"))   # leque expandido (estouro)
SENT_FORCA_JANELA = int(os.environ.get("SENT_FORCA_JANELA", "40"))   # nº de velas da série de força/leque
# Acumulador da LINHA de força (p/ ela BALANÇAR como a do Sentinela, não ficar plana na média): memória
# (decay ↑ = linha mais longa/suave) e sensibilidade (escala ↓ = mais amplitude). Ajustar p/ bater visual.
SENT_FORCA_DECAY = float(os.environ.get("SENT_FORCA_DECAY", "0.85"))
SENT_FORCA_ESCALA = float(os.environ.get("SENT_FORCA_ESCALA", "40"))

# --------------------------------------------------------------------------- #
# FAMÍLIA F_BREAKOUT — rompimento da faixa de abertura de Londres (6º cenário)
# --------------------------------------------------------------------------- #
# Achado do estudo histórico (9 meses H1+M15, validado FORA DA AMOSTRA): o movimento grande do forex
# se concentra na abertura de Londres; um opening-range breakout rende +0,3–0,4R líquido de spread,
# robusto em H1 e M15, em quase todos os pares (menos USDCAD), num platô de parâmetros. NÃO prevê
# direção (o rompimento dá) e deixa correr. Dois livros: sem proteção (máx expectância) e com proteção
# +2p após +10p (curva suave). O gate da Etapa 9 decide antes de qualquer demo.
BREAKOUT_HABILITADA = os.environ.get("BREAKOUT_HABILITADA", "true").lower() in ("1", "true", "sim")
BREAKOUT_TFS = [s.strip() for s in os.environ.get("BREAKOUT_TFS", "M15,H1").split(",") if s.strip()]
BREAKOUT_EXCLUI = {s.strip() for s in os.environ.get("BREAKOUT_EXCLUI", "USDCAD").split(",") if s.strip()}
BREAKOUT_OR_HORA = int(os.environ.get("BREAKOUT_OR_HORA", "10"))     # hora servidor (07:00 UTC, Londres)
BREAKOUT_OR_MIN = int(os.environ.get("BREAKOUT_OR_MIN", "45"))       # duração da faixa de abertura (min)
BREAKOUT_FIM_HORA = int(os.environ.get("BREAKOUT_FIM_HORA", "17"))   # fecha no fim da janela (hora servidor)
BREAKOUT_OR_MIN_PIPS = float(os.environ.get("BREAKOUT_OR_MIN_PIPS", "3"))  # faixa mínima (evita degenerada)
BREAKOUT_PROT_TRIGGER_PIPS = float(os.environ.get("BREAKOUT_PROT_TRIGGER_PIPS", "10"))  # +100 pipetes
BREAKOUT_PROT_LOCK_PIPS = float(os.environ.get("BREAKOUT_PROT_LOCK_PIPS", "2"))         # trava +2 pips
# Sub-flags por estratégia. As 3 DESLIGADAS por padrão desde 18/07: a 1ª auditoria
# (N=30-43, pós-fix) deu as três NEGATIVAS com o IC 95% da exp R abaixo de zero em forca/leque
# (t=-3,5/-2,7; PF ~0,35; split-half negativo nas 2 metades) e divergencia negativa a ~93% —
# falha estrutural de ENTRADA (perseguição/fade adiantado), não variância. Reversível pelo env
# (SENT_*_HABILITADA=true religa). Ver a linha do tempo da família E_SENTINELA no CLAUDE.md.
SENT_FORCA_HABILITADA = os.environ.get("SENT_FORCA_HABILITADA", "false").lower() in ("1", "true", "sim")
SENT_DIVERG_HABILITADA = os.environ.get("SENT_DIVERG_HABILITADA", "false").lower() in ("1", "true", "sim")
SENT_LEQUE_HABILITADA = os.environ.get("SENT_LEQUE_HABILITADA", "false").lower() in ("1", "true", "sim")
# Flags da B3 — INDEPENDENTES do forex: a refutação de 18/07 foi do livro FOREX; o livro da B3
# (mercado='b3') ainda NÃO foi auditado, então as 3 seguem LIGADAS por padrão na B3 (catalogando)
# até termos amostra própria. Desligar cada uma pelo env quando a auditoria da B3 pedir.
SENT_FORCA_HABILITADA_B3 = os.environ.get("SENT_FORCA_HABILITADA_B3", "true").lower() in ("1", "true", "sim")
SENT_DIVERG_HABILITADA_B3 = os.environ.get("SENT_DIVERG_HABILITADA_B3", "true").lower() in ("1", "true", "sim")
SENT_LEQUE_HABILITADA_B3 = os.environ.get("SENT_LEQUE_HABILITADA_B3", "true").lower() in ("1", "true", "sim")

# GESTÃO DE SAÍDA POR VARIANTE (liga as saídas próprias de B/C na sombra) — ADITIVO, a Variante A
# (controle) NUNCA passa por aqui, segue no gestor genérico. Motivado pela auditoria (14/07): 100%
# das perdedoras saíram no stop cheio (-1R) e a simulação de invalidação mostrou que uma saída
# estrutural antecipada salvaria ~2/3 delas. Aqui a sombra passa a CATALOGAR a saída desenhada de
# cada variante (C: saída antecipada por M5 fuzzy + aperto de stop na exaustão; B: saída técnica na
# VWAP oposta) → o relatório A vs C mede se a saída inteligente melhora a expectância. Shadow-only.
GESTAO_POR_VARIANTE = os.environ.get("GESTAO_POR_VARIANTE", "true").lower() in ("1", "true", "sim")

# EXPERIMENTO "DEIXA CORRER" (motivado pela 1ª auditoria de dados reais da B3, 14/07): a saída
# antecipada da Variante C domina os trades mas rende migalha (+1,91/trade) enquanto quem é deixado
# correr (giveback estrutural) rende +16 a +56. Este livro C_CORRE espelha as MESMAS entradas da C
# mas NÃO passa pela saída fuzzy (cai no gestor genérico = stop + giveback) → o /relatorio mede o Δ
# de expectância C_HIBRIDA (corta cedo) × C_CORRE (deixa andar), isolando SÓ a saída. Aditivo/desligável.
EXPERIMENTO_CORRE_HABILITADO = os.environ.get("EXPERIMENTO_CORRE_HABILITADO", "true").lower() in ("1", "true", "sim")

# --------------------------------------------------------------------------- #
# Relatório sombra multi-variante (ETAPA 7)
# --------------------------------------------------------------------------- #
# Mínimo de sinais por CÉLULA (variante × estratégia × TF × par) para a expectância ser
# considerada estatisticamente utilizável (doc: 30–50). Abaixo disso, "amostra pequena".
RELATORIO_MIN_SINAIS = int(os.environ.get("RELATORIO_MIN_SINAIS", "30"))
# Rótulos amigáveis das variantes do laboratório (só exibição).
NOMES_VARIANTES = {
    "A_ORIGINAL": "A · Original (controle)",
    "B_FUZZY_PURO": "B · Fuzzy Puro",
    "C_HIBRIDA": "C · Híbrida (A + fuzzy)",
}


def nome_variante(codigo):
    """Nome amigável da variante; devolve o próprio código se não houver mapeamento."""
    return NOMES_VARIANTES.get(codigo, codigo or "—")

# --------------------------------------------------------------------------- #
# ETAPA 9 — Gate de aprovação estatística por célula (sombra → demo)
# --------------------------------------------------------------------------- #
# Uma célula (variante × estratégia × TF × par) só é APROVADA para execução em DEMO
# quando cumpre TODOS os critérios do doc-mestre (Parte 8/10) + a skill quant (§5) —
# o gate que separa EDGE de SORTE antes de arriscar qualquer ordem, mesmo em demo:
#   1. AMOSTRA:      N ≥ APROVACAO_MIN_SINAIS (winrate de 5 trades não é winrate)
#   2. EXPECTÂNCIA:  exp R > 0 (a métrica-mãe, já líquida de spread na simulação)
#   3. PROFIT FACTOR: PF ≥ APROVACAO_PF_MIN
#   4. SPLIT-HALF:   exp R POSITIVA nas DUAS metades do período (guardião anti-sorte)
APROVACAO_MIN_SINAIS = int(os.environ.get("APROVACAO_MIN_SINAIS", "50"))
APROVACAO_PF_MIN = float(os.environ.get("APROVACAO_PF_MIN", "1.3"))
APROVACAO_EXIGE_SPLIT_HALF = os.environ.get(
    "APROVACAO_EXIGE_SPLIT_HALF", "true").lower() in ("1", "true", "sim")
# Probabilidade nominal de uma célula "passar por ACASO" — testamos CENTENAS de células
# ao mesmo tempo (armadilha de múltiplos testes / data-snooping da skill §5). A nota
# `testadas × esta prob ≈ nº de falsos-positivos esperados` calibra a desconfiança.
APROVACAO_PROB_ACASO = float(os.environ.get("APROVACAO_PROB_ACASO", "0.05"))

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
# DEFAULT VAZIO (18/07): a auditoria da `confluencia_v1` (sombra pós-fix 16/07, N=47 A / 44
# C_CORRE / 139 C_HIBRIDA) deu exp R NEGATIVA nas TRÊS variantes (−0,31/−0,32/−0,14) e 0 células
# no gate da Etapa 9 → ela deixou de ser curada. `fecha_gap` já saíra (aposentada 18/07). Nada é
# elegível ao real por default até PASSAR o gate (N≥50 · exp R>0 · PF≥1,3 · split-half) — o dono
# repromove pelo env quando alguma célula aprovar.
EXEC_REAL_ESTRATEGIAS = [s.strip() for s in os.environ.get(
    "EXEC_REAL_ESTRATEGIAS", "").split(",") if s.strip()]
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
# Exportação em LOTE do raio-X por estratégia (zip com 1 HTML/trade — gráfico visual + raio-X
# textual). Teto de trades por download (o mais RECENTE primeiro) p/ não estourar CPU/memória da
# VPS gerando centenas de gráficos Plotly num request só.
RAIOX_EXPORT_MAX = int(os.environ.get("RAIOX_EXPORT_MAX", "150"))

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
