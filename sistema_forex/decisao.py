"""Fase 4 — Estrategista (modo sombra).

Uma vez por candle M5 fechado novo, monta o snapshot do par a partir do que o motor
(Fase 2) gravou no banco — regime, ATR, níveis, estrutura — e chama `estrategias.avaliar`.
Registra CADA decisão (entrou / não entrou + motivo) na tabela `decisoes`. NÃO envia
ordens: é o modo sombra que valida a lógica contra o mercado ao vivo antes da Fase 5.

    python -m sistema_forex.decisao            # loop
    python -m sistema_forex.decisao --uma-vez  # um ciclo (debug)
"""

import json
import logging
import signal
import sys
import time

from . import config, db, estrategias, indicadores

log = logging.getLogger("decisao")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover - sinal do SO
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


# --------------------------------------------------------------------------- #
# Montagem do snapshot (a partir do banco)
# --------------------------------------------------------------------------- #
def _ultimo_m5(conn, par: str):
    return conn.execute(
        "SELECT time_utc, open, high, low, close, spread FROM candles WHERE par=? AND tf='M5' "
        "ORDER BY time_utc DESC LIMIT 1",
        (par,),
    ).fetchone()


def _atr_m5(conn, par: str):
    rows = conn.execute(
        "SELECT high, low, close FROM candles WHERE par=? AND tf='M5' "
        "ORDER BY time_utc DESC LIMIT ?",
        (par, config.ATR_PERIODO * 4),
    ).fetchall()
    rows = list(reversed(rows))
    if len(rows) < config.ATR_PERIODO + 1:
        return None
    return indicadores.atr(
        [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows],
        config.ATR_PERIODO,
    )


def _niveis(conn, par: str):
    sup, res, fvgs = [], [], []
    for r in conn.execute(
        "SELECT tipo, preco, preco2, forca FROM niveis WHERE par=? AND ativo=1", (par,)
    ):
        if r["tipo"] == "suporte":
            sup.append((r["preco"], r["forca"]))
        elif r["tipo"] == "resistencia":
            res.append((r["preco"], r["forca"]))
        elif r["tipo"].startswith("fvg") and r["preco2"] is not None:
            base, topo = min(r["preco"], r["preco2"]), max(r["preco"], r["preco2"])
            fvgs.append({"tipo": r["tipo"], "base": base, "topo": topo})
    return sup, res, fvgs


def _janela_m5(conn, par: str, n: int) -> dict:
    """Últimos `n` candles M5 (cronológicos) para a detecção de sweep+CHoCH."""
    rows = conn.execute(
        "SELECT open, high, low, close FROM candles WHERE par=? AND tf='M5' "
        "ORDER BY time_utc DESC LIMIT ?",
        (par, n),
    ).fetchall()
    rows = list(reversed(rows))
    return {
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
    }


def _ultimo_evento(conn, par: str):
    r = conn.execute(
        "SELECT evento, direcao, tf FROM estrutura WHERE par=? ORDER BY time_utc DESC LIMIT 1",
        (par,),
    ).fetchone()
    return {"evento": r["evento"], "direcao": r["direcao"], "tf": r["tf"]} if r else None


def _regime(conn, par: str):
    r = conn.execute(
        "SELECT regime FROM regime_log WHERE par=? ORDER BY time_utc DESC LIMIT 1", (par,)
    ).fetchone()
    return r["regime"] if r else "indefinido"


def montar_snapshot(conn, par: str, candle_m5) -> dict:
    """Snapshot do par para a decisão, a partir do último candle M5 fechado."""
    sup, res, fvgs = _niveis(conn, par)
    # spread em pontos → pips (pares de 3/5 casas: 1 pip = 10 pontos).
    spread_pips = (candle_m5["spread"] or 0) / 10.0
    hora_utc = time.gmtime(candle_m5["time_utc"]).tm_hour
    return {
        "close": candle_m5["close"],
        "open": candle_m5["open"],
        "high": candle_m5["high"],
        "low": candle_m5["low"],
        "spread_pips": spread_pips,
        "hora_utc": hora_utc,
        "atr": _atr_m5(conn, par),
        "regime": _regime(conn, par),
        "suportes": sup,
        "resistencias": res,
        "fvgs": fvgs,
        "ultimo_evento": _ultimo_evento(conn, par),
        "m5_janela": _janela_m5(conn, par, config.SWEEP_JANELA),
    }


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def _gravar_decisao(conn, par: str, time_utc: int, dec: dict) -> None:
    conn.execute(
        """
        INSERT INTO decisoes (par, time_utc, estrategia, direcao, resultado, motivo, dados_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            par, time_utc, dec["estrategia"], dec["direcao"], dec["resultado"], dec["motivo"],
            json.dumps({"score": dec["score"], "confluencias": dec["confluencias"],
                        "regime": dec["regime"]}),
        ),
    )


def avaliar_par(conn, par: str, candle_m5) -> list:
    """Avalia TODAS as estratégias ativas sobre o candle M5 e grava cada decisão.

    Cada estratégia grava a sua própria linha em `decisoes` (entrou/não + motivo), o que
    mantém a auditoria por estratégia no /analitico. O executor deduplica no nível de
    posição (MAX_POS_POR_PAR), então duas entradas simultâneas não abrem duas posições.
    """
    snap = montar_snapshot(conn, par, candle_m5)
    decs = [
        estrategias.avaliar(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=config.SPREAD_MAX_PIPS,
            score_min=config.SCORE_MIN_CONFLUENCIAS,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
            exigir_rejeicao=config.EXIGIR_REJEICAO_SR,
        ),
    ]
    if config.SWEEP_HABILITADA:
        decs.append(estrategias.avaliar_sweep_choch(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=config.SPREAD_MAX_PIPS,
            n_swing=config.SWEEP_N_SWING,
            sweep_min_atr=config.SWEEP_MIN_ATR,
            sweep_recente=config.SWEEP_RECENTE,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
        ))
    for dec in decs:
        _gravar_decisao(conn, par, candle_m5["time_utc"], dec)
    conn.commit()
    return decs


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #
def um_ciclo(conn, ultimo_visto: dict) -> None:
    for par in config.PARES:
        candle = _ultimo_m5(conn, par)
        if candle is None:
            continue
        if ultimo_visto.get(par) == candle["time_utc"]:
            continue  # nada novo neste par
        ultimo_visto[par] = candle["time_utc"]
        try:
            for dec in avaliar_par(conn, par, candle):
                log.info(
                    "Decisão %s @%s [%s]: %s %s | score=%d | %s",
                    par, candle["time_utc"], dec["estrategia"], dec["resultado"],
                    dec["direcao"] or "-", dec["score"], dec["motivo"],
                )
        except Exception:  # noqa: BLE001 - um par não derruba o serviço
            log.exception("Falha ao decidir %s", par)


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    uma_vez = "--uma-vez" in sys.argv
    log.info("Estrategista (modo sombra) iniciado. Pares: %s", ", ".join(config.PARES))

    ultimo_visto = {}
    with db.sessao() as conn:
        if uma_vez:
            # Em debug, força avaliar o candle atual mesmo que já visto.
            um_ciclo(conn, {})
            return
        while not _parar:
            um_ciclo(conn, ultimo_visto)
            for _ in range(config.DECISAO_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    log.info("Estrategista encerrado.")


if __name__ == "__main__":
    main()
