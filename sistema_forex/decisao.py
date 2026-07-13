"""Fase 4 — Estrategista (modo sombra).

A cada candle fechado novo de cada (par, TF de operação em `config.TFS_OPERACAO` — M1/M5/M15),
monta o snapshot do par a partir do que o motor (Fase 2) gravou no banco — regime, níveis e
estrutura de CONTEXTO (par-level) mais a vela/ATR/janela do próprio TF — e chama as estratégias.
Registra CADA decisão (entrou / não entrou + motivo), marcada com o `tf`, na tabela `decisoes`.
Cada TF é um livro de sombra INDEPENDENTE (comparável no /analitico "Por timeframe"). NÃO envia
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
def _ultimo(conn, par: str, tf: str):
    return conn.execute(
        "SELECT time_utc, open, high, low, close, spread FROM candles WHERE par=? AND tf=? "
        "ORDER BY time_utc DESC LIMIT 1",
        (par, tf),
    ).fetchone()


def _atr(conn, par: str, tf: str):
    """ATR do TF DE OPERAÇÃO — a régua de distância (stop/tolerância) é a volatilidade do
    próprio TF em que se opera (o M1 tem ATR bem menor que o M15)."""
    rows = conn.execute(
        "SELECT high, low, close FROM candles WHERE par=? AND tf=? "
        "ORDER BY time_utc DESC LIMIT ?",
        (par, tf, config.ATR_PERIODO * 4),
    ).fetchall()
    rows = list(reversed(rows))
    if len(rows) < config.ATR_PERIODO + 1:
        return None
    return indicadores.atr(
        [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows],
        config.ATR_PERIODO,
    )


def _niveis(conn, par: str):
    sup, res, fvgs, obs, gaps = [], [], [], [], []
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
        elif r["tipo"].startswith("ob") and r["preco2"] is not None:
            base, topo = min(r["preco"], r["preco2"]), max(r["preco"], r["preco2"])
            obs.append({"tipo": r["tipo"], "base": base, "topo": topo})
        elif r["tipo"].startswith("gap"):
            # gap_alta / gap_baixa; preco = fechamento anterior (alvo do fill).
            gaps.append({"direcao": "alta" if r["tipo"].endswith("alta") else "baixa",
                         "nivel": r["preco"]})
    return sup, res, fvgs, obs, gaps


def _extremos_dia(conn, par: str, agora_utc: int):
    """Máxima/mínima do último dia FECHADO (D1) — liquidez PDH/PDL. Pega o candle D1 cujo
    início é ANTERIOR à meia-noite UTC do dia corrente, evitando o dia em formação."""
    dia_inicio = agora_utc - (agora_utc % 86400)
    r = conn.execute(
        "SELECT high, low FROM candles WHERE par=? AND tf='D1' AND time_utc < ? "
        "ORDER BY time_utc DESC LIMIT 1",
        (par, dia_inicio),
    ).fetchone()
    return (r["high"], r["low"]) if r else (None, None)


def _janela(conn, par: str, tf: str, n: int) -> dict:
    """Últimos `n` candles do TF de operação (cronológicos) p/ a detecção de sweep+CHoCH."""
    rows = conn.execute(
        "SELECT open, high, low, close FROM candles WHERE par=? AND tf=? "
        "ORDER BY time_utc DESC LIMIT ?",
        (par, tf, n),
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


def montar_snapshot(conn, par: str, tf: str, candle) -> dict:
    """Snapshot do par para a decisão no TF DE OPERAÇÃO `tf`.

    A vela de operação, o ATR e a janela de sweep vêm do próprio `tf` (M1/M5/M15). Já os
    níveis S/R, a estrutura e o regime são CONTEXTO do par (S/R só de H1/D1/W1, regime do
    H1) e não mudam com o TF de operação — cada livro de TF opera a mesma "memória", mas com
    a régua de volatilidade e o gatilho do seu próprio timeframe.
    """
    sup, res, fvgs, obs, gaps = _niveis(conn, par)
    max_dia, min_dia = _extremos_dia(conn, par, candle["time_utc"])
    # spread em pontos → pips (pares de 3/5 casas: 1 pip = 10 pontos).
    spread_pips = (candle["spread"] or 0) / 10.0
    hora_utc = time.gmtime(candle["time_utc"]).tm_hour
    return {
        "tf": tf,
        "close": candle["close"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "spread_pips": spread_pips,
        "hora_utc": hora_utc,
        "atr": _atr(conn, par, tf),
        "regime": _regime(conn, par),
        "suportes": sup,
        "resistencias": res,
        "fvgs": fvgs,
        "obs": obs,
        "gaps": gaps,
        "max_dia": max_dia,
        "min_dia": min_dia,
        "ultimo_evento": _ultimo_evento(conn, par),
        # Janela do TF de operação p/ o sweep+CHoCH (chave histórica "m5_janela" mantida
        # para as estratégias/tests; aqui contém a janela do `tf` corrente).
        "m5_janela": _janela(conn, par, tf, config.SWEEP_JANELA),
    }


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def _gravar_decisao(conn, par: str, tf: str, time_utc: int, dec: dict) -> None:
    conn.execute(
        """
        INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, motivo, dados_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            par, time_utc, tf, dec["estrategia"], dec["direcao"], dec["resultado"], dec["motivo"],
            json.dumps({"score": dec["score"], "confluencias": dec["confluencias"],
                        "regime": dec["regime"]}),
        ),
    )


def avaliar_par(conn, par: str, tf: str, candle) -> list:
    """Avalia TODAS as estratégias ativas sobre o candle do TF `tf` e grava cada decisão.

    Cada estratégia grava a sua própria linha em `decisoes` (entrou/não + motivo, marcada
    com o `tf`), o que mantém a auditoria por estratégia E por timeframe no /analitico. O
    executor deduplica no nível de posição por (par, tf), então duas entradas simultâneas
    do mesmo livro de TF não abrem duas posições.
    """
    snap = montar_snapshot(conn, par, tf, candle)
    # Filtro de spread POR SÍMBOLO (o ouro tem spread bem maior que o forex).
    spread_max = config.param_simbolo(par, "spread_max_pips", config.SPREAD_MAX_PIPS)
    decs = [
        estrategias.avaliar(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
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
            spread_max_pips=spread_max,
            n_swing=config.SWEEP_N_SWING,
            sweep_min_atr=config.SWEEP_MIN_ATR,
            sweep_recente=config.SWEEP_RECENTE,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
        ))
    if config.OB_HABILITADA:
        decs.append(estrategias.avaliar_order_block(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
            exigir_rejeicao=config.EXIGIR_REJEICAO_OB,
        ))
    if config.PULLBACK_HABILITADA:
        decs.append(estrategias.avaliar_pullback_tendencia(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.GAP_HABILITADA:
        decs.append(estrategias.avaliar_fecha_gap(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            gap_min_atr=config.FECHA_GAP_MIN_ATR,
        ))
    if config.ROMPIMENTO_HABILITADA:
        decs.append(estrategias.avaliar_pullback_rompimento(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.EXTREMOS_HABILITADA:
        decs.append(estrategias.avaliar_rompimento_extremos(
            snap,
            sessao_utc=config.SESSAO_UTC,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    for dec in decs:
        _gravar_decisao(conn, par, tf, candle["time_utc"], dec)
    conn.commit()
    return decs


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #
def um_ciclo(conn, ultimo_visto: dict) -> None:
    # Cada (par, tf) é um livro independente: avalia o candle mais recente do seu TF de
    # operação. `ultimo_visto` é chaveado por (par, tf) para não reavaliar o mesmo candle.
    for par in config.PARES:
        for tf in config.TFS_OPERACAO:
            candle = _ultimo(conn, par, tf)
            if candle is None:
                continue
            chave = (par, tf)
            if ultimo_visto.get(chave) == candle["time_utc"]:
                continue  # nada novo neste (par, tf)
            ultimo_visto[chave] = candle["time_utc"]
            try:
                for dec in avaliar_par(conn, par, tf, candle):
                    log.info(
                        "Decisão %s %s @%s [%s]: %s %s | score=%d | %s",
                        par, tf, candle["time_utc"], dec["estrategia"], dec["resultado"],
                        dec["direcao"] or "-", dec["score"], dec["motivo"],
                    )
            except Exception:  # noqa: BLE001 - um (par,tf) não derruba o serviço
                log.exception("Falha ao decidir %s %s", par, tf)


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    uma_vez = "--uma-vez" in sys.argv
    log.info("Estrategista (modo sombra) iniciado. Pares: %s | TFs de operação: %s",
             ", ".join(config.PARES), ", ".join(config.TFS_OPERACAO))

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
