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

import json
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


def _grava_nivel(conn, par, tipo, preco, tf, criado_em, forca, preco2=None,
                 n_toques=0, ultimo_toque=None, meta_json=None) -> None:
    conn.execute(
        """
        INSERT INTO niveis (par, tipo, preco, preco2, tf_origem, criado_em, forca,
                            n_toques, ultimo_toque, meta_json, ativo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (par, tipo, preco, preco2, tf, criado_em, forca, n_toques, ultimo_toque, meta_json),
    )


def _grava_evento(conn, par, tf, time_utc, evento, preco, direcao) -> None:
    conn.execute(
        "INSERT INTO estrutura (par, tf, time_utc, evento, preco, direcao) VALUES (?, ?, ?, ?, ?, ?)",
        (par, tf, time_utc, evento, preco, direcao),
    )


def _marcar_confluencia(conn, par: str, atr_ref: float) -> int:
    """Reforça as ZONAS DE CONFLUÊNCIA: níveis do MESMO tipo (suporte/resistência), de TFs
    diferentes, que caem dentro de `SR_CONFLUENCIA_ATR × ATR` um do outro. Cada vizinho soma
    `SR_CONFLUENCIA_BONUS × força` — a marcação que reúne topos/fundos alinhados vira o S/R mais
    forte (o que o preço mais respeita). Guarda `confluencia`=nº de vizinhos no `meta`. Idempotente
    (roda sobre o snapshot recém-gravado do par). Retorna quantos níveis foram reforçados."""
    if not atr_ref or config.SR_CONFLUENCIA_BONUS <= 0:
        return 0
    tol = config.SR_CONFLUENCIA_ATR * atr_ref
    reforcados = 0
    for tipo in ("suporte", "resistencia"):
        rows = conn.execute(
            "SELECT id, preco, forca, meta_json FROM niveis WHERE par=? AND tipo=? AND ativo=1",
            (par, tipo),
        ).fetchall()
        for r in rows:
            vizinhos = sum(1 for x in rows if x["id"] != r["id"] and abs(x["preco"] - r["preco"]) <= tol)
            if not vizinhos:
                continue
            nova_forca = round(r["forca"] * (1 + config.SR_CONFLUENCIA_BONUS * vizinhos), 1)
            meta = {}
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except (ValueError, TypeError):
                pass
            meta["confluencia"] = vizinhos
            conn.execute("UPDATE niveis SET forca=?, meta_json=? WHERE id=?",
                         (nova_forca, json.dumps(meta), r["id"]))
            reforcados += 1
    return reforcados


# --------------------------------------------------------------------------- #
# Análise de um par
# --------------------------------------------------------------------------- #
def analisar_par(conn, par: str) -> dict:
    """Recalcula níveis/estrutura/regime de um par. Retorna um resumo p/ log/painel."""
    agora = int(datetime.utcnow().timestamp())
    resumo = {"par": par, "suporte": 0, "resistencia": 0, "fvg": 0, "ob": 0, "gaps": 0,
              "eventos": 0, "regime": "indefinido", "adx": None}

    _limpar_par(conn, par)

    for tf in config.TFS_COLETA:
        d = _carregar(conn, par, tf, config.ANALISE_JANELA)
        if len(d["close"]) < config.ATR_PERIODO * 2:
            continue

        atr_val = indicadores.atr(d["high"], d["low"], d["close"], config.ATR_PERIODO)
        n_swing = config.SWING_N.get(tf, config.SWING_N_M5)
        sw = indicadores.rotular_swings(indicadores.swings(d["high"], d["low"], n_swing))

        # Suporte / resistência — SÓ dos TFs fortes (H1/D1/W1), com nota de QUALIDADE
        # (toques + rejeições + recência + peso do TF). M5/M15 não geram S/R (ruído).
        if tf in config.SR_TFS and atr_val:
            sr = indicadores.niveis_sr(sw, atr_val, config.SR_CLUSTER_ATR, config.SR_FORCA_MIN)
            tol = config.SR_TOQUE_ATR * atr_val
            peso = config.SR_TF_PESO.get(tf, 1.0)
            n = len(d["close"])
            for tipo in ("resistencia", "suporte"):
                candidatos = []
                for preco, _f in sr[tipo]:
                    q = indicadores.qualidade_sr(preco, tipo, d["high"], d["low"], d["close"], tol)
                    recente = q["ult_idx"] >= 0 and (n - 1 - q["ult_idx"]) <= config.SR_RECENCIA_CANDLES
                    recencia = 1.0 if recente else 0.5
                    # Rejeição pesa 2×; toques contam pouco (evita inflar por consolidação).
                    forca = round((q["rejeicoes"] * 2 + min(q["toques"], 20) * 0.25 + 1)
                                  * peso * recencia, 1)
                    if forca >= config.SR_QUALIDADE_MIN:
                        candidatos.append((preco, forca, q))
                candidatos.sort(key=lambda x: x[1], reverse=True)
                for preco, forca, q in candidatos[:config.SR_MAX_POR_TIPO]:  # só os melhores
                    ult_t = d["time"][q["ult_idx"]] if 0 <= q["ult_idx"] < len(d["time"]) else agora
                    meta = json.dumps({"toques": q["toques"], "rejeicoes": q["rejeicoes"],
                                       "respeito": q["respeito"], "peso_tf": peso, "tf": tf})
                    _grava_nivel(conn, par, tipo, preco, tf, agora, forca,
                                 n_toques=q["toques"], ultimo_toque=ult_t, meta_json=meta)
                    resumo[tipo] += 1

        # FVGs não mitigados — zona guardada em (preco=base, preco2=topo).
        for f in indicadores.fvgs(d["high"], d["low"], atr_val, config.FVG_MIN_ATR):
            _grava_nivel(conn, par, f["tipo"], f["base"], tf, agora, 1, preco2=f["topo"])
            resumo["fvg"] += 1

        # Order blocks frescos — só nos TFs de estrutura (M15/H1); M5 = ruído.
        if tf in config.OB_TFS and atr_val:
            for ob in indicadores.order_blocks(d["open"], d["high"], d["low"], d["close"],
                                               atr_val, config.OB_MIN_ATR):
                _grava_nivel(conn, par, ob["tipo"], ob["base"], tf, agora, 1, preco2=ob["topo"])
                resumo["ob"] += 1

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
    atr_ref = None
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

    # Reforça as zonas de CONFLUÊNCIA de S/R (topos/fundos alinhados de TFs diferentes) — as
    # marcações que o preço mais respeita (pedido do dono). Roda sobre o snapshot recém-gravado.
    resumo["confluencias"] = _marcar_confluencia(conn, par, atr_ref)

    conn.commit()
    return resumo


# --------------------------------------------------------------------------- #
# Leitura do snapshot (para painel e gráfico)
# --------------------------------------------------------------------------- #
def niveis_ativos(conn, par: str, tf: str = None) -> list:
    """Níveis ativos de um par (opcionalmente filtrando por TF de origem).

    Inclui `meta` (toques/rejeições/respeito/peso_tf) quando disponível, para o painel,
    o gráfico e as estratégias avaliarem a qualidade do nível.
    """
    sql = ("SELECT tipo, preco, preco2, tf_origem, forca, n_toques, meta_json "
           "FROM niveis WHERE par = ? AND ativo = 1")
    args = [par]
    if tf:
        sql += " AND tf_origem = ?"
        args.append(tf)
    saida = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        if d.get("meta_json"):
            try:
                d["meta"] = json.loads(d["meta_json"])
            except (ValueError, TypeError):
                d["meta"] = None
        saida.append(d)
    return saida


def resumo_par(conn, par: str) -> dict:
    """Resumo da análise de um par para o painel: regime + contagem de níveis."""
    reg = conn.execute(
        "SELECT regime, adx FROM regime_log WHERE par = ? ORDER BY time_utc DESC LIMIT 1",
        (par,),
    ).fetchone()
    contagem = {t: 0 for t in ("suporte", "resistencia", "fvg", "ob", "gap", "evento")}
    for r in conn.execute(
        "SELECT tipo, COUNT(*) n FROM niveis WHERE par = ? AND ativo = 1 GROUP BY tipo", (par,)
    ):
        if r["tipo"].startswith("fvg"):
            contagem["fvg"] += r["n"]
        elif r["tipo"].startswith("ob"):
            contagem["ob"] += r["n"]
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
                "Análise %s: regime=%s adx=%s | S=%d R=%d FVG=%d OB=%d gaps=%d eventos=%d",
                r["par"], r["regime"], r["adx"], r["suporte"], r["resistencia"],
                r["fvg"], r["ob"], r["gaps"], r["eventos"],
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
