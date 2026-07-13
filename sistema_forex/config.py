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
PARES = ["EURUSD#", "GBPUSD#", "USDCAD"]
TF_OPERACAO = "M5"
# W1 (semanal) incluído: é onde estão os S/R mais fortes (junto de D1/H1).
TFS_COLETA = ["M5", "M15", "H1", "D1", "W1"]
BACKFILL_MESES = 6
# Piso de barras por TF no backfill: garante histórico suficiente para os TFs altos
# (6 meses de W1 são só ~26 velas; precisamos de mais para ATR/S/R no semanal).
BACKFILL_MIN_BARRAS = int(os.environ.get("BACKFILL_MIN_BARRAS", "300"))

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
# Máximo de níveis por tipo (suporte/resistência) por par — anti-proliferação (só os melhores).
SR_MAX_POR_TIPO = int(os.environ.get("SR_MAX_POR_TIPO", "6"))

FVG_MIN_ATR = 0.3

GAP_MIN_PIPS = 5
GAP_MAX_PIPS = 20

ADX_TENDENCIA = 25
ADX_LATERAL = 20

SESSAO_UTC = (7, 20)
SPREAD_MAX_PIPS = 2.0

LOTE = 0.01
SL_SERVIDOR_ATR_MULT = 3.0
SL_MIN_PIPS = 12
SL_MAX_PIPS = 40
BE_TRIGGER_R = 1.0
DD_DIARIO_MAX_PCT = 5.0
MAX_POS_POR_PAR = 1
MAX_POS_TOTAL = 2
# Guarda de correlação: exposição líquida MÁXIMA por moeda (em nº de posições).
# EURUSD e GBPUSD comprados = ambos short USD → USD líquido -2. Com limite 1, a 2ª
# entrada correlacionada é bloqueada. USDCAD comprado + EURUSD comprado se cancelam no
# USD (net 0) → permitido. Evita "risco dobrado escondido" pela mesma moeda.
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
SWING_N = {"M5": SWING_N_M5, "M15": SWING_N_M15, "H1": SWING_N_H1,
           "D1": SWING_N_H1, "W1": SWING_N_H1}

# --------------------------------------------------------------------------- #
# Estrategista / decisão (Fase 4 — modo sombra)
# --------------------------------------------------------------------------- #
# Intervalo (segundos) entre verificações de candle M5 novo para decidir.
DECISAO_POLL_S = int(os.environ.get("DECISAO_POLL_S", "5"))
# Proximidade (em ATR) do preço a um nível para contar como confluência.
NIVEL_PROX_ATR = float(os.environ.get("NIVEL_PROX_ATR", "0.5"))
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

# --------------------------------------------------------------------------- #
# Executor + gestor de saída (Fase 5)
# --------------------------------------------------------------------------- #
# TRAVA DE SEGURANÇA. false = simulação sobre preço AO VIVO (nenhuma ordem é
# enviada). true = envia e gerencia ordens de verdade na conta demo (exige
# "Algo Trading" habilitado no terminal). Mude só quando decidir operar.
EXECUCAO_ATIVA = os.environ.get("EXECUCAO_ATIVA", "false").lower() in ("1", "true", "sim")
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

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")  # DEBUG desde a v1 (regra do projeto)
