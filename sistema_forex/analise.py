"""Fase 2 — Motor de análise.

Lê os candles que o coletor gravou e calcula a "memória" do sistema:
níveis (S/R, FVG, gaps), estrutura SMC (HH/HL/LH/LL, BOS/CHOCH) e o regime (ADX).
Persiste tudo em `niveis`, `estrutura` e `regime_log` para o painel/gráfico (Fase 3)
e as estratégias (Fase 4) consumirem.

Roda como serviço próprio (`python -m sistema_forex.analise`), separado do coletor:
trabalha só sobre o banco, então NÃO depende da ponte MT5. A cada ciclo recalcula do
zero por par (snapshot idempotente): apaga o que era daquele par e regrava.

    python -m sistema_forex.analise          # loop contínuo
    python -m sistema_forex.analise --uma-vez # um ciclo e sai (debug)
"""

import logging
import signal
import sys
import time
from datetime import datetime

from . import config, db, indicadores

log = logging.getLogger("analise")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover - sinal do SO
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


# --------------------------------------------------------------------------- #
# Leitura de candles
# --------------------------------------------------------------------------- #
def _carregar(conn, par: str, tf: str, limite: int) -> dict:
    """Candles cronológicos de par/tf como colunas paralelas (opens/highs/...)."""
    rows = conn.execute(
        """
        SELECT time_utc, open, high, low, close
        FROM candles WHERE par = ? AND tf = ?
        ORDER BY time_utc DESC LIMIT ?
        """,
        (par, tf, limite),
    ).fetchall()
    rows = list(reversed(rows))
    return {
        "time": [r["time_utc"] for r in rows],
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
    }


def _tamanho_pip(preco_ref: float) -> float:
    """Estima 1 pip pelo nº de casas do preço (0.0001 p/ 5 casas, 0.01 p/ JPY etc.).

    Sem symbol_info aqui (o motor não fala com o MT5); a heurística basta para gaps.
    """
    return 0.01 if preco_ref >= 20 else 0.0001


# --------------------------------------------------------------------------- #
# Persistência (snapshot idempotente por par)
# --------------------------------------------------------------------------- #
def _limpar_par(conn, par: str) -> None:
    conn.execute("DELETE FROM niveis WHERE par = ?", (par,))
    conn.execute("DELETE FROM estrutura WHERE par = ?", (par,))


def _grava_nivel(conn, par, tipo, preco, tf, criado_em, forca, preco2=None) -> None:
    conn.execute(
        """
        INSERT INTO niveis (par, tipo, preco, preco2, tf_origem, criado_em, forca, ativo)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (par, tipo, preco, preco2, tf, criado_em, forca),
    )


def _grava_evento(conn, par, tf, time_utc, evento, preco, direcao) -> None:
    conn.execute(
        "INSERT INTO estrutura (par, tf, time_utc, evento, preco, direcao) VALUES (?, ?, ?, ?, ?, ?)",
        (par, tf, time_utc, evento, preco, direcao),
    )


# --------------------------------------------------------------------------- #
# Análise de um par
# --------------------------------------------------------------------------- #
def analisar_par(conn, par: str) -> dict:
    """Recalcula níveis/estrutura/regime de um par. Retorna um resumo p/ log/painel."""
    agora = int(datetime.utcnow().timestamp())
    resumo = {"par": par, "suporte": 0, "resistencia": 0, "fvg": 0, "gaps": 0,
              "eventos": 0, "regime": "indefinido", "adx": None}

    _limpar_par(conn, par)

    for tf in config.TFS_COLETA:
        d = _carregar(conn, par, tf, config.ANALISE_JANELA)
        if len(d["close"]) < config.ATR_PERIODO * 2:
            continue

        atr_val = indicadores.atr(d["high"], d["low"], d["close"], config.ATR_PERIODO)
        n_swing = config.SWING_N.get(tf, config.SWING_N_M5)
        sw = indicadores.rotular_swings(indicadores.swings(d["high"], d["low"], n_swing))

        # Suporte / resistência
        sr = indicadores.niveis_sr(sw, atr_val, config.SR_CLUSTER_ATR, config.SR_FORCA_MIN)
        for preco, forca in sr["resistencia"]:
            _grava_nivel(conn, par, "resistencia", preco, tf, agora, forca)
            resumo["resistencia"] += 1
        for preco, forca in sr["suporte"]:
            _grava_nivel(conn, par, "suporte", preco, tf, agora, forca)
            resumo["suporte"] += 1

        # FVGs não mitigados — zona guardada em (preco=base, preco2=topo).
        for f in indicadores.fvgs(d["high"], d["low"], atr_val, config.FVG_MIN_ATR):
            _grava_nivel(conn, par, f["tipo"], f["base"], tf, agora, 1, preco2=f["topo"])
            resumo["fvg"] += 1

        # Eventos de estrutura (SMC)
        for e in indicadores.eventos_estrutura(sw):
            t = d["time"][e["i"]] if e["i"] < len(d["time"]) else agora
            _grava_evento(conn, par, tf, t, e["evento"], e["preco"], e["direcao"])
            resumo["eventos"] += 1

        # Gaps só no TF de regime (sessão) — evita ruído nos TFs baixos
        if tf == config.TF_REGIME:
            pip = _tamanho_pip(d["close"][-1])
            for g in indicadores.gaps(d["open"], d["close"], pip, config.GAP_MIN_PIPS, config.GAP_MAX_PIPS):
                _grava_nivel(conn, par, f"gap_{g['direcao']}", g["preco"], tf, agora, 1)
                resumo["gaps"] += 1

    # Regime pelo ADX no TF de referência
    dref = _carregar(conn, par, config.TF_REGIME, config.ANALISE_JANELA)
    if len(dref["close"]) >= config.ADX_PERIODO * 2:
        res = indicadores.adx(dref["high"], dref["low"], dref["close"], config.ADX_PERIODO)
        atr_ref = indicadores.atr(dref["high"], dref["low"], dref["close"], config.ATR_PERIODO)
        if res:
            adx_v, pdi, mdi = res
            regime = indicadores.classificar_regime(
                adx_v, pdi, mdi, config.ADX_TENDENCIA, config.ADX_LATERAL
            )
            resumo["regime"], resumo["adx"] = regime, round(adx_v, 1)
            conn.execute(
                "INSERT INTO regime_log (par, time_utc, regime, adx, atr) VALUES (?, ?, ?, ?, ?)",
                (par, agora, regime, adx_v, atr_ref),
            )

    conn.commit()
    return resumo


# --------------------------------------------------------------------------- #
# Leitura do snapshot (para painel e gráfico)
# --------------------------------------------------------------------------- #
def niveis_ativos(conn, par: str, tf: str = None) -> list:
    """Níveis ativos de um par (opcionalmente filtrando por TF de origem)."""
    sql = "SELECT tipo, preco, preco2, tf_origem, forca FROM niveis WHERE par = ? AND ativo = 1"
    args = [par]
    if tf:
        sql += " AND tf_origem = ?"
        args.append(tf)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def resumo_par(conn, par: str) -> dict:
    """Resumo da análise de um par para o painel: regime + contagem de níveis."""
    reg = conn.execute(
        "SELECT regime, adx FROM regime_log WHERE par = ? ORDER BY time_utc DESC LIMIT 1",
        (par,),
    ).fetchone()
    contagem = {t: 0 for t in ("suporte", "resistencia", "fvg", "gap", "evento")}
    for r in conn.execute(
        "SELECT tipo, COUNT(*) n FROM niveis WHERE par = ? AND ativo = 1 GROUP BY tipo", (par,)
    ):
        if r["tipo"].startswith("fvg"):
            contagem["fvg"] += r["n"]
        elif r["tipo"].startswith("gap"):
            contagem["gap"] += r["n"]
        elif r["tipo"] in ("suporte", "resistencia"):
            contagem[r["tipo"]] += r["n"]
    contagem["evento"] = conn.execute(
        "SELECT COUNT(*) n FROM estrutura WHERE par = ?", (par,)
    ).fetchone()["n"]
    return {
        "par": par,
        "regime": reg["regime"] if reg else "indefinido",
        "adx": round(reg["adx"], 1) if reg and reg["adx"] is not None else None,
        **contagem,
    }


def um_ciclo(conn) -> None:
    for par in config.PARES:
        try:
            r = analisar_par(conn, par)
            log.info(
                "Análise %s: regime=%s adx=%s | S=%d R=%d FVG=%d gaps=%d eventos=%d",
                r["par"], r["regime"], r["adx"], r["suporte"], r["resistencia"],
                r["fvg"], r["gaps"], r["eventos"],
            )
        except Exception:  # noqa: BLE001 - um par não pode derrubar o motor
            log.exception("Falha ao analisar %s", par)


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    uma_vez = "--uma-vez" in sys.argv
    log.info("Motor de análise iniciado (%s). Pares: %s",
             "ciclo único" if uma_vez else f"loop a cada {config.ANALISE_POLL_S}s",
             ", ".join(config.PARES))

    with db.sessao() as conn:
        if uma_vez:
            um_ciclo(conn)
            return
        while not _parar:
            um_ciclo(conn)
            for _ in range(config.ANALISE_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    log.info("Motor encerrado.")


if __name__ == "__main__":
    main()
