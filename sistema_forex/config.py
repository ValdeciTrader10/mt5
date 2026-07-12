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
TFS_COLETA = ["M5", "M15", "H1", "D1"]
BACKFILL_MESES = 6

SWING_N_M5 = 3
SWING_N_M15 = 5
SWING_N_H1 = 5

SR_CLUSTER_ATR = 0.5
SR_ROMPIMENTO_ATR = 0.3
SR_FORCA_MIN = 3

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
MT5_LOGIN = os.environ.get("MT5_LOGIN", "")            # ex: 84110577
MT5_SERVER = os.environ.get("MT5_SERVER", "")          # ex: XMGlobal-MT5 4
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
# Hash bcrypt da senha (gerar com scripts/gerar_hash.py). NUNCA a senha em texto.
PAINEL_SENHA_HASH = os.environ.get("PAINEL_SENHA_HASH", "")
# Chave para assinar o cookie de sessão. Trocar em produção (.env).
SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
SESSAO_HORAS = int(os.environ.get("SESSAO_HORAS", "12"))

# --------------------------------------------------------------------------- #
# Telegram (usado a partir das fases seguintes)
# --------------------------------------------------------------------------- #
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT", "")

# --------------------------------------------------------------------------- #
# Coletor
# --------------------------------------------------------------------------- #
# Intervalo (segundos) entre verificações de candle M5 fechado no loop.
COLETOR_POLL_S = int(os.environ.get("COLETOR_POLL_S", "15"))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")  # DEBUG desde a v1 (regra do projeto)
