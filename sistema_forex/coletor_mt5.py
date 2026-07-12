"""Fase 1 — Coletor.

Coleta candles do MT5 (via ponte) e persiste em SQLite — a memória histórica que
o tempo real não tem.

- Backfill inicial: 6 meses de M5/M15/H1/D1 por par (D1 necessário para pivots).
- Loop: a cada candle M5 FECHADO, insere candles novos de todos os TFs (INSERT OR IGNORE).
- Nunca usa candle em formação para análise (detecta fechamento comparando timestamps).
- Grava o spread junto (auditoria de custo — edge morre com spread ≥ 3,2p).
- Log DEBUG desde a v1: cada iteração registra o que foi coletado.

Aceite: contagem de candles bate com o MT5, sem buracos.
"""

import logging
import signal
import time
from datetime import datetime, timedelta

from . import config, db, mt5_bridge

log = logging.getLogger("coletor")

# Quantos candles buscar por TF a cada verificação incremental (folga p/ não perder nada).
_JANELA_INCREMENTAL = 10

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover - sinal do SO
    global _parar
    log.info("Sinal %s recebido — encerrando após a iteração atual.", signum)
    _parar = True


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def gravar_candles(conn, par: str, tf: str, candles: list) -> int:
    """Insere candles com INSERT OR IGNORE. Retorna quantos foram efetivamente novos."""
    if not candles:
        return 0
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO candles
            (par, tf, time_utc, open, high, low, close, tick_volume, spread)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                par, tf, c["time"], c["open"], c["high"], c["low"],
                c["close"], c["tick_volume"], c["spread"],
            )
            for c in candles
        ],
    )
    return cur.rowcount if cur.rowcount is not None else 0


def contar(conn, par: str, tf: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM candles WHERE par=? AND tf=?", (par, tf)
    ).fetchone()
    return row["n"] if row else 0


# --------------------------------------------------------------------------- #
# Backfill
# --------------------------------------------------------------------------- #
def backfill(conn, simbolos: dict) -> None:
    """Baixa 6 meses de histórico por par/tf. Idempotente (INSERT OR IGNORE)."""
    inicio = datetime.utcnow() - timedelta(days=config.BACKFILL_MESES * 31)
    fim = datetime.utcnow() + timedelta(days=1)  # folga para pegar o candle mais recente
    for par, simbolo in simbolos.items():
        for tf in config.TFS_COLETA:
            candles = mt5_bridge.copy_rates_range(simbolo, tf, inicio, fim)
            novos = gravar_candles(conn, par, tf, candles)
            conn.commit()
            log.info(
                "Backfill %s %s: %d candles recebidos, %d novos, total no banco %d",
                par, tf, len(candles), novos, contar(conn, par, tf),
            )


# --------------------------------------------------------------------------- #
# Loop incremental
# --------------------------------------------------------------------------- #
def _ultimo_m5_fechado(simbolo: str):
    """Retorna o candle M5 fechado mais recente (índice -2), ou None."""
    recentes = mt5_bridge.copy_rates_from_pos(simbolo, "M5", 0, 2)
    if len(recentes) < 2:
        return None
    # oldest-first: [-2] é o último fechado, [-1] está em formação.
    return recentes[-2]


def coletar_incremental(conn, par: str, simbolo: str) -> int:
    """Insere candles novos de todos os TFs para um par. Retorna total de novos."""
    total = 0
    for tf in config.TFS_COLETA:
        candles = mt5_bridge.copy_rates_from_pos(simbolo, tf, 0, _JANELA_INCREMENTAL)
        # Descarta o candle em formação (o mais recente) — só persiste fechados.
        fechados = candles[:-1] if candles else []
        total += gravar_candles(conn, par, tf, fechados)
    return total


def loop(conn, simbolos: dict) -> None:
    """Loop principal: a cada candle M5 fechado novo, coleta incremental."""
    ultimo_visto = {par: None for par in simbolos}
    log.info("Coletor em loop. Pares: %s", ", ".join(simbolos))
    while not _parar:
        t0 = time.monotonic()
        try:
            houve_novo = False
            for par, simbolo in simbolos.items():
                fechado = _ultimo_m5_fechado(simbolo)
                if fechado is None:
                    log.debug("%s: sem candles suficientes ainda.", par)
                    continue
                t_fechado = fechado["time"]
                if ultimo_visto[par] == t_fechado:
                    continue  # nada novo neste par
                houve_novo = True
                novos = coletar_incremental(conn, par, simbolo)
                conn.commit()
                ultimo_visto[par] = t_fechado
                log.debug(
                    "%s: candle M5 fechado %s | %d candles novos | spread=%s",
                    par,
                    datetime.utcfromtimestamp(t_fechado).strftime("%Y-%m-%d %H:%M"),
                    novos,
                    fechado["spread"],
                )
                if fechado["spread"] and fechado["spread"] >= config.SPREAD_ALERTA_PIPS * 10:
                    log.warning("%s: spread alto (%s pontos) — edge em risco.", par, fechado["spread"])
            if not houve_novo:
                log.debug("Nenhum candle novo. Latência do ciclo: %.2fs", time.monotonic() - t0)
        except mt5_bridge.MT5Erro as e:
            log.error("Erro de ponte MT5: %s — tentando de novo em %ss", e, config.COLETOR_POLL_S)
        except Exception:  # noqa: BLE001 - loop resiliente
            log.exception("Erro inesperado no loop do coletor")
        # Espera interrompível.
        for _ in range(config.COLETOR_POLL_S):
            if _parar:
                break
            time.sleep(1)
    log.info("Loop encerrado.")


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
def resolver_simbolos() -> dict:
    """Mapeia cada par lógico (config) para o nome real confirmado no broker."""
    simbolos = {}
    for par in config.PARES:
        real = mt5_bridge.resolver_simbolo(par)
        simbolos[par] = real
        if real != par:
            log.info("Par %s resolvido para símbolo real '%s'", par, real)
    return simbolos


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    resumo = mt5_bridge.ping()
    log.info("Conectado ao MT5: login=%s servidor=%s saldo=%.2f %s",
             resumo["login"], resumo["servidor"], resumo["saldo"], resumo["moeda"])

    simbolos = resolver_simbolos()
    with db.sessao() as conn:
        backfill(conn, simbolos)
        loop(conn, simbolos)
    mt5_bridge.desligar()


if __name__ == "__main__":
    main()
