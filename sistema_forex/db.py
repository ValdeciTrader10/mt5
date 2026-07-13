"""Camada de banco (SQLite — mercado.db).

Schema completo do doc §2, já incluindo as tabelas das Fases 2-5 para não precisar
migrar depois. WAL ligado porque vários serviços (coletor, web, futuros motor/executor)
leem/escrevem o mesmo arquivo simultaneamente.
"""

import logging
import sqlite3
from contextlib import contextmanager

from . import config

log = logging.getLogger("db")

# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #
SCHEMA = """
-- Candles por par e timeframe
CREATE TABLE IF NOT EXISTS candles (
    par         TEXT NOT NULL,
    tf          TEXT NOT NULL,
    time_utc    INTEGER NOT NULL,      -- epoch (tick.time do servidor, UTC+3)
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    tick_volume INTEGER,
    spread      INTEGER,
    PRIMARY KEY (par, tf, time_utc)
);
CREATE INDEX IF NOT EXISTS idx_candles_par_tf_time ON candles (par, tf, time_utc);

-- Níveis calculados pelo motor (a "memória") — Fase 2
CREATE TABLE IF NOT EXISTS niveis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    par          TEXT NOT NULL,
    tipo         TEXT NOT NULL,        -- suporte, resistencia, pivot_pp, order_block_bull, fvg_bull...
    preco        REAL NOT NULL,
    preco2       REAL,                 -- topo/fundo de zonas (OB, FVG)
    tf_origem    TEXT,
    criado_em    INTEGER,
    ultimo_toque INTEGER,
    n_toques     INTEGER DEFAULT 0,
    ativo        INTEGER DEFAULT 1,    -- 0 = consumido (mitigado/preenchido/rompido)
    forca        REAL DEFAULT 1,
    meta_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_niveis_par_ativo ON niveis (par, ativo);

-- Estrutura SMC por par — Fase 2
CREATE TABLE IF NOT EXISTS estrutura (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    par      TEXT NOT NULL,
    tf       TEXT NOT NULL,
    time_utc INTEGER NOT NULL,
    evento   TEXT NOT NULL,            -- HH, HL, LH, LL, BOS, CHOCH
    preco    REAL,
    direcao  TEXT
);
CREATE INDEX IF NOT EXISTS idx_estrutura_par_tf_time ON estrutura (par, tf, time_utc);

-- Regime detectado (histórico para auditoria) — Fase 4
CREATE TABLE IF NOT EXISTS regime_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    par          TEXT NOT NULL,
    time_utc     INTEGER NOT NULL,
    regime       TEXT NOT NULL,
    adx          REAL,
    atr          REAL,
    detalhes_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_regime_par_time ON regime_log (par, time_utc);

-- Toda decisão do sistema (entrou ou não, e por quê) — Fase 4 (modo sombra)
CREATE TABLE IF NOT EXISTS decisoes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    par        TEXT NOT NULL,
    time_utc   INTEGER NOT NULL,
    estrategia TEXT,
    direcao    TEXT,
    resultado  TEXT,                   -- entrou / nao_entrou
    motivo     TEXT,
    dados_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisoes_par_time ON decisoes (par, time_utc);

-- Trades executados — Fase 5
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket        INTEGER,
    par           TEXT NOT NULL,
    estrategia    TEXT,
    direcao       TEXT,
    lote          REAL,
    preco_entrada REAL,
    sl_servidor   REAL,
    preco_saida   REAL,
    pips          REAL,
    lucro_usd     REAL,
    motivo_saida  TEXT,
    abertura_utc  INTEGER,
    fechamento_utc INTEGER,
    simulado      INTEGER DEFAULT 0,   -- 1 = posição do modo simulação (Fase 5 sem EXECUCAO_ATIVA)
    risco_inicial REAL,                -- |entrada - sl_inicial| em preço; base fixa do R
    mae_r         REAL,                -- Maximum Adverse Excursion: pior R contra durante a vida (≤ 0)
    mfe_r         REAL,                -- Maximum Favorable Excursion: melhor R a favor durante a vida (≥ 0)
    regime_entrada TEXT                -- regime de mercado no momento da abertura (p/ análise por regime)
);
CREATE INDEX IF NOT EXISTS idx_trades_par ON trades (par);
CREATE INDEX IF NOT EXISTS idx_trades_abertos ON trades (fechamento_utc);
"""


def _migrar(conn) -> None:
    """Migrações idempotentes para bancos criados antes de colunas novas."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "simulado" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN simulado INTEGER DEFAULT 0")
    if "risco_inicial" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN risco_inicial REAL")
    if "mae_r" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN mae_r REAL")
    if "mfe_r" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN mfe_r REAL")
    if "regime_entrada" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN regime_entrada TEXT")


def conectar(db_path=None) -> sqlite3.Connection:
    """Abre uma conexão configurada (WAL, row factory, timeout para concorrência)."""
    caminho = str(db_path or config.DB_PATH)
    conn = sqlite3.connect(caminho, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def sessao(db_path=None):
    """Context manager que commita no fim e sempre fecha a conexão."""
    conn = conectar(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None) -> None:
    """Cria o diretório de dados e o schema (idempotente)."""
    caminho = db_path or config.DB_PATH
    config.DADOS_DIR.mkdir(parents=True, exist_ok=True)
    with sessao(caminho) as conn:
        conn.executescript(SCHEMA)
        _migrar(conn)
    log.info("Banco inicializado em %s", caminho)


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    init_db()
    print(f"Banco pronto em {config.DB_PATH}")
