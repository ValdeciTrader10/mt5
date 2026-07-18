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

from . import config, db, estrategias, fuzzy_score, indicadores

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


def _pivots(conn, par: str) -> list:
    """Níveis pivot do motor (pivot_pp/pivot_r*/pivot_s*) como [(preco, tipo)] — p/ a estratégia
    `pivot_confluencia`. `tipo` sem o prefixo (pp/r1/s1…) para logar mais curto."""
    rows = conn.execute(
        "SELECT tipo, preco FROM niveis WHERE par=? AND ativo=1 AND tipo LIKE 'pivot_%'", (par,)
    ).fetchall()
    return [(r["preco"], r["tipo"].replace("pivot_", "")) for r in rows]


def _medias_tf_acima(conn, par: str, tf: str) -> dict:
    """Médias (EMA9/20/45 + SMA50/200) do TF ACIMA do de operação (contexto de tendência da
    estratégia `pullback_medias`). M1→M5, M5→M15, M15→H1. Sem TF acima mapeado → {}."""
    tf_acima = config.TF_ACIMA.get(tf)
    if not tf_acima:
        return {}
    rows = conn.execute(
        "SELECT close FROM candles WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf_acima, config.MEDIAS_JANELA),
    ).fetchall()
    closes = [r["close"] for r in reversed(rows)]
    return indicadores.medias(closes) if closes else {}


def _extremos_dia(conn, par: str, agora_utc: int):
    """Máxima/mínima do último dia FECHADO (D1) — liquidez PDH/PDL. `agora_utc` é a hora do
    candle (= hora do SERVIDOR), então a fronteira já é meia-noite do servidor: pega o D1 cujo
    início é ANTERIOR à meia-noite do dia corrente, evitando o dia em formação."""
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
        "SELECT open, high, low, close, tick_volume FROM candles WHERE par=? AND tf=? "
        "ORDER BY time_utc DESC LIMIT ?",
        (par, tf, n),
    ).fetchall()
    rows = list(reversed(rows))
    return {
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
        "volume": [r["tick_volume"] for r in rows],   # p/ o filtro de absorção (sweep_choch_abs_v1)
    }


def _ultimo_evento(conn, par: str):
    # M1 FORA: com o M1 coletado, o "último evento" era quase sempre um micro-BOS/CHoCH de M1
    # (swing a cada poucos minutos) — o contexto de estrutura das estratégias virava ruído,
    # contra a metodologia (estrutura de contexto = TFs maiores). M5+ preserva a intenção
    # original (antes do M1 entrar na coleta, o TF mais fino era o M5).
    r = conn.execute(
        "SELECT evento, direcao, tf FROM estrutura WHERE par=? AND tf != 'M1' "
        "ORDER BY time_utc DESC LIMIT 1",
        (par,),
    ).fetchone()
    return {"evento": r["evento"], "direcao": r["direcao"], "tf": r["tf"]} if r else None


def _regime(conn, par: str):
    r = conn.execute(
        "SELECT regime FROM regime_log WHERE par=? ORDER BY time_utc DESC LIMIT 1", (par,)
    ).fetchone()
    return r["regime"] if r else "indefinido"


def _fuzzy_mtf(conn, par: str) -> dict:
    """Últimos fuzzy_scores da pirâmide MTF (M15/M5/M1) da Variante B: {tf:{score,estado,flags}}."""
    return fuzzy_score.scores_recentes(conn, par, config.FUZZY_B_PIRAMIDE)


def _serie_op(conn, par: str, tf: str, n: int) -> dict:
    """Série ALINHADA candle-a-candle do TF de operação: high/low/close + score fuzzy (JOIN por
    time_utc), últimos n candles cronológicos. Base das estratégias D_LINHAS (divergência/exaustão)."""
    rows = conn.execute(
        "SELECT c.time_utc, c.high, c.low, c.close, f.score FROM candles c "
        "JOIN fuzzy_scores f ON f.par=c.par AND f.tf=c.tf AND f.time_utc=c.time_utc "
        "WHERE c.par=? AND c.tf=? ORDER BY c.time_utc DESC LIMIT ?",
        (par, tf, n)).fetchall()
    rows = list(reversed(rows))
    return {"high": [r["high"] for r in rows], "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows], "score": [r["score"] for r in rows],
            "time": [r["time_utc"] for r in rows]}


def _score_acima_alinhado(conn, par: str, tf_op: str, tf_acima: str, tempos: list) -> list:
    """Scores do TF ACIMA alinhados (asof pelo FECHAMENTO) aos candles do TF de operação.

    O `score_acima` antigo era só "os últimos N scores do TF acima", comparado POSICIONALMENTE
    com a série do TF de operação no pullback do leque — misturava instantes diferentes (M5 de
    5min atrás vs M15 de 15min atrás; o "reengate" disparava pelo movimento da LENTA). Aqui
    lenta[j] = último score do TF acima cujo candle já FECHOU quando o candle rápido j fechou
    (sem look-ahead; None no início da janela, antes do 1º fechamento do TF acima)."""
    if not tempos:
        return []
    passo_op = config.MINUTOS_TF.get(tf_op, 5) * 60
    passo_ac = config.MINUTOS_TF.get(tf_acima, 15) * 60
    rows = conn.execute(
        "SELECT time_utc, score FROM fuzzy_scores WHERE par=? AND tf=? AND time_utc>=? "
        "ORDER BY time_utc",
        (par, tf_acima, tempos[0] - 48 * 3600)).fetchall()
    serie = [(r["time_utc"] + passo_ac, r["score"]) for r in rows]   # (fechamento, score)
    out, j, ult = [], 0, None
    for t in tempos:
        ref = t + passo_op            # quando o candle rápido fecha (= quando a decisão o vê)
        while j < len(serie) and serie[j][0] <= ref:
            ult = serie[j][1]
            j += 1
        out.append(ult)
    return out


def _score_serie(conn, par: str, tf: str, n: int) -> list:
    """Série cronológica de scores fuzzy de um TF (p/ o leque: a linha do TF ACIMA)."""
    rows = conn.execute(
        "SELECT score FROM fuzzy_scores WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf, n)).fetchall()
    return [r["score"] for r in reversed(rows)]


def _sync_ult(conn, par: str, k: int = 2) -> list:
    """Últimos k estados da Sync Line (cronológico) — p/ detectar o flip de alinhamento (D_LINHAS/C)."""
    rows = conn.execute(
        "SELECT estado FROM sync_line WHERE par=? ORDER BY time_utc DESC LIMIT ?", (par, k)).fetchall()
    return [r["estado"] for r in reversed(rows)]


def _forca_leque(conn, par: str, tf: str, n: int) -> list:
    """Série de FORÇA/LEQUE contínua (E_SENTINELA) nas últimas n velas do TF de operação — asof dos
    scores M1/M5/M15/H1 em cada instante (`fuzzy_score.forca_serie`). Cronológica."""
    rows = conn.execute(
        "SELECT time_utc FROM candles WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf, n)).fetchall()
    tempos = [r["time_utc"] for r in reversed(rows)]
    return fuzzy_score.forca_serie(conn, par, tempos, decay=config.SENT_FORCA_DECAY,
                                   escala=config.SENT_FORCA_ESCALA) if tempos else []


def _vwap_bandas(conn, par: str) -> dict:
    """VWAP + bandas do par (níveis do motor) como dict — contexto da Variante B."""
    mapa = {"vwap": "vwap", "vwap_sup1": "sup1", "vwap_inf1": "inf1",
            "vwap_sup2": "sup2", "vwap_inf2": "inf2"}
    out = {}
    for r in conn.execute(
        "SELECT tipo, preco FROM niveis WHERE par=? AND ativo=1 AND tipo LIKE 'vwap%'", (par,)):
        chave = mapa.get(r["tipo"])
        if chave:
            out[chave] = r["preco"]
    return out


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
        "pivots": _pivots(conn, par),
        "medias_acima": _medias_tf_acima(conn, par, tf),
        # Contexto fuzzy da Variante B (ETAPA 5): pirâmide MTF + VWAP/bandas do motor.
        "fuzzy": _fuzzy_mtf(conn, par),
        "vwap": _vwap_bandas(conn, par),
        "ultimo_evento": _ultimo_evento(conn, par),
        # Janela do TF de operação p/ o sweep+CHoCH (chave histórica "m5_janela" mantida
        # para as estratégias/tests; aqui contém a janela do `tf` corrente).
        "m5_janela": _janela(conn, par, tf, config.SWEEP_JANELA),
        # Família D_LINHAS (dinâmica das curvas de score): série alinhada preço×score do TF de
        # operação, a linha do TF ACIMA (leque) e o histórico da Sync (flip de alinhamento).
        "serie_op": (_so := _serie_op(conn, par, tf, config.LINHAS_JANELA)),
        "score_acima": _score_acima_alinhado(conn, par, tf, config.TF_ACIMA.get(tf, tf),
                                             _so.get("time") or []),
        "sync_ult": _sync_ult(conn, par, 2),
        # Família E_SENTINELA: FORÇA contínua (micro/macro) + LEQUE, atual e histórico curto.
        "forca_serie": (_fl := _forca_leque(conn, par, tf, config.SENT_FORCA_JANELA)),
        "forca": _fl[-1] if _fl else {},
    }


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #
def _fuzzy_tf(conn, par: str, tf: str, time_utc: int):
    """Score fuzzy do (par, tf) no candle da decisão (ou o último disponível se o motor ainda
    não pontuou este candle). None se não houver — o EV cai no neutro (0.5)."""
    r = conn.execute(
        "SELECT score FROM fuzzy_scores WHERE par=? AND tf=? AND time_utc=?",
        (par, tf, time_utc)).fetchone()
    if r is None:
        r = conn.execute(
            "SELECT score FROM fuzzy_scores WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT 1",
            (par, tf)).fetchone()
    return r["score"] if r else None


def _sync_estado(conn, par: str):
    r = conn.execute(
        "SELECT estado FROM sync_line WHERE par=? ORDER BY time_utc DESC LIMIT 1", (par,)).fetchone()
    return r["estado"] if r else None


def _vwap(conn, par: str):
    r = conn.execute(
        "SELECT preco FROM niveis WHERE par=? AND ativo=1 AND tipo='vwap' LIMIT 1", (par,)).fetchone()
    return r["preco"] if r else None


def _scores_ev(conn, par: str, tf: str, time_utc: int, dec: dict) -> dict:
    """EV score (4 componentes) do sinal — só CARIMBA a decisão (não bloqueia na v1). Junta
    confluência da estratégia + alinhamento fuzzy/sync + localização vs VWAP."""
    direcao = dec.get("direcao")
    fuzzy_tf = _fuzzy_tf(conn, par, tf, time_utc)
    sync = _sync_estado(conn, par)
    vwap = _vwap(conn, par)
    ev = fuzzy_score.ev_score(
        confluencia=min((dec.get("score") or 0) / 4.0, 1.0),
        fuzzy=fuzzy_score.componente_fuzzy(direcao, fuzzy_tf),
        sync=fuzzy_score.componente_sync(direcao, sync),
        localizacao=fuzzy_score.componente_localizacao(direcao, dec.get("_close"), vwap),
    )
    ev.update({"fuzzy_tf": fuzzy_tf, "sync": sync})
    return ev


def _gravar_decisao(conn, par: str, tf: str, time_utc: int, dec: dict, mercado: str = "forex") -> None:
    dados = {"score": dec["score"], "confluencias": dec["confluencias"], "regime": dec["regime"]}
    if dec.get("sl_pips") is not None:      # F_BREAKOUT: stop na OR (o executor lê e usa no lugar do ATR)
        dados["sl_pips"] = dec["sl_pips"]
    if config.EV_HABILITADO:
        try:
            dados["ev"] = _scores_ev(conn, par, tf, time_utc, dec)
        except Exception:  # noqa: BLE001 - EV é informativo; nunca derruba a gravação
            log.exception("Falha ao calcular EV de %s %s", par, tf)
    conn.execute(
        """
        INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, motivo, dados_json,
                              criada_utc, variante, mercado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            par, time_utc, tf, dec["estrategia"], dec["direcao"], dec["resultado"], dec["motivo"],
            json.dumps(dados),
            int(time.time()),
            dec.get("variante", "A_ORIGINAL"),
            mercado,
        ),
    )


def avaliar_par(conn, par: str, tf: str, candle, *, mercado: str = "forex",
                sessao_utc=None, spread_max=None) -> list:
    """Avalia TODAS as estratégias ativas sobre o candle do TF `tf` e grava cada decisão.

    Cada estratégia grava a sua própria linha em `decisoes` (entrou/não + motivo, marcada
    com o `tf`), o que mantém a auditoria por estratégia E por timeframe no /analitico. O
    executor deduplica no nível de posição por (par, tf), então duas entradas simultâneas
    do mesmo livro de TF não abrem duas posições.

    Reuso pelo mercado B3 (ETAPA 8b): as estratégias são funções PURAS e agnósticas de
    mercado — o `decisao_b3` chama esta mesma função com `mercado="b3"` e a janela de
    sessão/spread da B3, marcando a decisão para o executor de sombra da B3 (a ponte da
    B3 é data-only; o executor do forex ignora as decisões `mercado='b3'`).
    """
    snap = montar_snapshot(conn, par, tf, candle)
    sessao_utc = config.SESSAO_UTC if sessao_utc is None else sessao_utc
    # Filtro de spread POR SÍMBOLO (o ouro tem spread bem maior que o forex; a B3 usa o seu).
    if spread_max is None:
        spread_max = config.param_simbolo(par, "spread_max_pips", config.SPREAD_MAX_PIPS)
    decs = [
        estrategias.avaliar(
            snap,
            sessao_utc=sessao_utc,
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
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            n_swing=config.SWEEP_N_SWING,
            sweep_min_atr=config.SWEEP_MIN_ATR,
            sweep_recente=config.SWEEP_RECENTE,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
        ))
    if config.SWEEP_ABS_HABILITADA:
        decs.append(estrategias.avaliar_sweep_choch_abs(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            n_swing=config.SWEEP_N_SWING,
            sweep_min_atr=config.SWEEP_MIN_ATR,
            sweep_recente=config.SWEEP_RECENTE,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            absorcao_janela=config.SWEEP_ABS_JANELA,
        ))
    if config.OB_HABILITADA:
        decs.append(estrategias.avaliar_order_block(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
            exigir_rejeicao=config.EXIGIR_REJEICAO_OB,
        ))
    # Gêmeo A/B do order block (motivado pela auditoria: 28/28 perdedoras da C_HIBRIDA foram
    # CONTRA de imediato = entrada sem confirmação). `order_block_rej_v1` = MESMA detecção, mas
    # SÓ entra se a vela REJEITAR a borda do bloco (pavio + fecha de volta). Livro de sombra
    # independente e comparável ao `order_block_v1` (a original fica intocada como controle).
    if config.OB_REJ_HABILITADA:
        decs.append(estrategias.avaliar_order_block(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
            exigir_rejeicao=True,
            estrategia=estrategias.ESTRATEGIA_OB_REJ,
        ))
    if config.PULLBACK_HABILITADA:
        decs.append(estrategias.avaliar_pullback_tendencia(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.GAP_HABILITADA:
        decs.append(estrategias.avaliar_fecha_gap(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            gap_min_atr=config.FECHA_GAP_MIN_ATR,
        ))
    if config.ROMPIMENTO_HABILITADA:
        decs.append(estrategias.avaliar_pullback_rompimento(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            forca_min=config.SR_FORCA_MIN,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.EXTREMOS_HABILITADA:
        decs.append(estrategias.avaliar_rompimento_extremos(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.MEDIAS_HABILITADA:
        decs.append(estrategias.avaliar_pullback_medias(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    if config.PIVOT_HABILITADA:
        decs.append(estrategias.avaliar_pivot_confluencia(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            nivel_prox_atr=config.NIVEL_PROX_ATR,
            pivot_sr_atr=config.PIVOT_SR_ATR,
            pavio_min=config.REJEICAO_PAVIO_MIN,
        ))
    # VARIANTE B — Fuzzy Puro (ETAPA 5). Grupo PARALELO (aditivo) às 9 estratégias da Variante A;
    # roda UMA vez por par, só no TF de TIMING (M1), com a pirâmide MTF estrita. Marca a decisão
    # variante=B_FUZZY_PURO — a auditoria compara A vs B por (par, tf).
    if config.FUZZY_B_HABILITADA and tf == config.FUZZY_B_TIMING_TF:
        decs.append(estrategias.avaliar_fuzzy_puro(
            snap,
            sessao_utc=sessao_utc,
            spread_max_pips=spread_max,
            mare_min=config.FUZZY_B_MARE_MIN,
            corrente_min=config.FUZZY_B_CORRENTE_MIN,
            timing_min=config.FUZZY_B_TIMING_MIN,
            std_k=config.FUZZY_B_STD_K,
            checklist_min=config.FUZZY_B_CHECKLIST_MIN,
        ))
        # Livro PARALELO A/B (item 4): mesma lógica, maré FIEL ao PDF (Lima=76). Estratégia
        # `fuzzy_puro_lima_v1` (livro de sombra independente) — os dados dizem se Lima rende mais.
        if config.FUZZY_B2_HABILITADA:
            decs.append(estrategias.avaliar_fuzzy_puro(
                snap,
                sessao_utc=sessao_utc,
                spread_max_pips=spread_max,
                mare_min=config.FUZZY_B2_MARE_MIN,
                corrente_min=config.FUZZY_B_CORRENTE_MIN,
                timing_min=config.FUZZY_B_TIMING_MIN,
                std_k=config.FUZZY_B_STD_K,
                checklist_min=config.FUZZY_B_CHECKLIST_MIN,
                estrategia=estrategias.ESTRATEGIA_FUZZY_PURO_LIMA,
            ))

    # VARIANTE C — Híbrida (ETAPA 6). Grupo PARALELO (aditivo): espelha CADA decisão "entrou" da
    # Variante A e aplica a camada fuzzy (leitura dos fuzzy_scores/VWAP), marcando C_HIBRIDA. Só
    # produz decisão quando a estratégia-base entrou (há setup) → o livro C é o subconjunto
    # fuzzy-filtrado do A, diretamente comparável (A vs C) no relatório. Não toca a Variante A/B.
    if config.HIBRIDA_HABILITADA:
        for dec in list(decs):
            if dec.get("variante") != estrategias.VARIANTE_A:
                continue  # C só espelha a Variante A (B tem seu próprio livro fuzzy_puro_v1)
            dc = estrategias.avaliar_hibrida(
                dec, snap,
                mare_min=config.FUZZY_B_MARE_MIN,
                corrente_min=config.FUZZY_B_CORRENTE_MIN,
            )
            if dc is not None:
                decs.append(dc)
                # Experimento "deixa correr": gêmeo com a MESMA entrada, marcado C_CORRE — no executor
                # ele NÃO passa pela saída fuzzy (cai no gestor genérico = stop + giveback estrutural),
                # isolando o efeito da SAÍDA (C_HIBRIDA corta cedo × C_CORRE deixa andar).
                if config.EXPERIMENTO_CORRE_HABILITADO:
                    gemeo = dict(dc)
                    gemeo["variante"] = estrategias.VARIANTE_C_CORRE
                    decs.append(gemeo)

    # FAMÍLIA D_LINHAS — estratégias pela DINÂMICA das linhas de score (4º cenário, aditivo). Cada
    # uma é um livro de sombra próprio (variante=D_LINHAS), comparável no /relatorio.
    if config.DIVERGENCIA_HABILITADA:
        decs.append(estrategias.avaliar_divergencia_fuzzy(
            snap, sessao_utc=sessao_utc, spread_max_pips=spread_max, n_swing=config.LINHAS_N_SWING))
    if config.PULLBACK_LEQUE_HABILITADA:
        decs.append(estrategias.avaliar_pullback_leque(
            snap, sessao_utc=sessao_utc, spread_max_pips=spread_max,
            mare_min=config.LEQUE_MARE_MIN, dip_janela=config.LEQUE_DIP_JANELA))
    if config.SYNC_FLIP_HABILITADA:
        decs.append(estrategias.avaliar_sync_flip(
            snap, sessao_utc=sessao_utc, spread_max_pips=spread_max, mare_min=config.LEQUE_MARE_MIN))
    if config.EXAUSTAO_HABILITADA:
        decs.append(estrategias.avaliar_exaustao_fuzzy(
            snap, sessao_utc=sessao_utc, spread_max_pips=spread_max,
            sat_candles=config.EXAUSTAO_SAT_CANDLES, sat_alto=config.EXAUSTAO_SAT_ALTO,
            sat_baixo=config.EXAUSTAO_SAT_BAIXO))

    # FAMÍLIA E_SENTINELA — força contínua (micro/macro) + leque (5º cenário, aditivo).
    if config.SENTINELA_HABILITADA:
        if config.SENT_FORCA_HABILITADA:
            decs.append(estrategias.avaliar_sentinela_forca(
                snap, sessao_utc=sessao_utc, spread_max_pips=spread_max, forca_min=config.SENT_FORCA_MIN))
        if config.SENT_DIVERG_HABILITADA:
            decs.append(estrategias.avaliar_sentinela_divergencia(
                snap, sessao_utc=sessao_utc, spread_max_pips=spread_max))
        if config.SENT_LEQUE_HABILITADA:
            decs.append(estrategias.avaliar_sentinela_leque(
                snap, sessao_utc=sessao_utc, spread_max_pips=spread_max,
                estreito=config.SENT_LEQUE_ESTREITO, largo=config.SENT_LEQUE_LARGO))

    for dec in decs:
        dec["_close"] = candle["close"]     # p/ o componente de localização (VWAP) do EV
        _gravar_decisao(conn, par, tf, candle["time_utc"], dec, mercado=mercado)
    conn.commit()
    return decs


# --------------------------------------------------------------------------- #
# FAMÍLIA F_BREAKOUT — rompimento da faixa de abertura de Londres (6º cenário, forex-only)
# --------------------------------------------------------------------------- #
def _pip_par(par: str) -> float:
    return 0.01 if "JPY" in par else 0.0001


def _or_londres(conn, par: str, tf: str, candle) -> dict:
    """Estado de sessão do breakout de Londres p/ o candle atual (sem look-ahead): a FAIXA DE ABERTURA
    (OR) do dia + se ESTE candle é o PRIMEIRO fechamento que a rompe, dentro da janela. Devolve
    {entrar, direcao, sl_pips, or_high, or_low} ou {} (fora da janela / sem OR).

    ⚠️ No H1 a "OR de 45min" é na prática a 1ª VELA INTEIRA (60min — o high/low do candle das 10h
    cobre 10:00–11:00): igual ao estudo histórico que validou o edge, mas os livros M15 e H1 NÃO
    testam a mesma OR (a do H1 é maior) — lembrar disso ao comparar M15×H1 no relatório."""
    t = candle["time_utc"]
    dia0 = t - (t % 86400)
    or_ini = dia0 + config.BREAKOUT_OR_HORA * 3600
    or_fim = or_ini + config.BREAKOUT_OR_MIN * 60
    janela_fim = dia0 + config.BREAKOUT_FIM_HORA * 3600
    passo = config.MINUTOS_TF.get(tf, 15) * 60
    # Janela de entrada: após a OR e com o candle FECHANDO antes do fim da janela. Sem o
    # `t+passo`, o candle que abre antes das 17h mas FECHA às 17h (H1 das 16h, M15 das 16:45)
    # gerava decisão ~17:00 → o executor abria e fechava em segundos ("fim da janela") =
    # trade-lixo de −spread sistematicamente no livro.
    if not (or_fim <= t and t + passo < janela_fim):
        return {}
    r = conn.execute("SELECT MAX(high) hi, MIN(low) lo FROM candles WHERE par=? AND tf=? "
                     "AND time_utc>=? AND time_utc<?", (par, tf, or_ini, or_fim)).fetchone()
    if not r or r["hi"] is None:
        return {}
    hi, lo = r["hi"], r["lo"]
    pip = _pip_par(par)
    sl_pips = (hi - lo) / pip
    if sl_pips < config.BREAKOUT_OR_MIN_PIPS:    # faixa degenerada
        return {}
    ja = conn.execute("SELECT close FROM candles WHERE par=? AND tf=? AND time_utc>=? AND time_utc<? "
                      "ORDER BY time_utc", (par, tf, or_fim, t)).fetchall()
    for x in ja:                                 # já houve rompimento hoje? (então não é o primeiro)
        if x["close"] > hi or x["close"] < lo:
            return {"entrar": False}
    c = candle["close"]
    if c > hi:
        direcao = "compra"
    elif c < lo:
        direcao = "venda"
    else:
        return {"entrar": False}
    return {"entrar": True, "direcao": direcao, "sl_pips": round(sl_pips, 1),
            "or_high": hi, "or_low": lo}


def avaliar_breakout_par(conn, par: str, tf: str, candle) -> list:
    """Avalia o breakout de Londres (2 livros: sem proteção e com proteção) para o candle do TF.
    Grava as decisões marcadas variante=F_BREAKOUT. Forex-only (a B3 tem pregão próprio)."""
    if not config.BREAKOUT_HABILITADA or tf not in config.BREAKOUT_TFS or par in config.BREAKOUT_EXCLUI:
        return []
    orl = _or_londres(conn, par, tf, candle)
    if not orl:
        return []
    snap = {"or_londres": orl, "regime": _regime(conn, par),
            "spread_pips": (candle["spread"] or 0) / 10.0}
    spread_max = config.param_simbolo(par, "spread_max_pips", config.SPREAD_MAX_PIPS)
    decs = []
    for est in (estrategias.ESTRATEGIA_BREAKOUT, estrategias.ESTRATEGIA_BREAKOUT_PROT):
        dec = estrategias.avaliar_breakout_londres(snap, estrategia=est, spread_max_pips=spread_max)
        dec["_close"] = candle["close"]
        _gravar_decisao(conn, par, tf, candle["time_utc"], dec, mercado="forex")
        decs.append(dec)
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

    # FAMÍLIA F_BREAKOUT (6º cenário) — roda nos SEUS TFs (M15/H1), independente de TFS_OPERACAO
    # (o H1 não é TF de operação, mas o breakout precisa dele). Livro próprio, forex-only.
    if config.BREAKOUT_HABILITADA:
        for par in config.PARES:
            if par in config.BREAKOUT_EXCLUI:
                continue
            for tf in config.BREAKOUT_TFS:
                candle = _ultimo(conn, par, tf)
                if candle is None:
                    continue
                chave = ("BRK", par, tf)
                if ultimo_visto.get(chave) == candle["time_utc"]:
                    continue
                ultimo_visto[chave] = candle["time_utc"]
                try:
                    avaliar_breakout_par(conn, par, tf, candle)
                except Exception:  # noqa: BLE001
                    log.exception("Falha no breakout %s %s", par, tf)


def watermark_inicial(conn, mercado: str = "forex") -> dict:
    """Semeia `ultimo_visto` do BANCO no arranque: o último candle já DECIDIDO por (par, tf).

    Sem isso, todo restart (Dokploy redeploya a cada push) reavaliava o candle corrente — que o
    processo anterior já tinha avaliado — e gravava ~20 decisões DUPLICADAS por livro (N inflado
    no /relatorio e no gate; e, se a posição original já tivesse fechado, o executor reabria o
    mesmo sinal atrasado). Também bloqueia decidir sobre um candle VELHO após downtime longo
    (fim de semana): se ele já foi decidido, não é redecidido.
    """
    marca = {}
    filtro = "mercado='b3'" if mercado == "b3" else "(mercado IS NULL OR mercado='forex')"
    for r in conn.execute(
        f"SELECT par, tf, MAX(time_utc) t FROM decisoes "
        f"WHERE {filtro} AND variante != 'F_BREAKOUT' GROUP BY par, tf"
    ):
        marca[(r["par"], r["tf"])] = r["t"]
    for r in conn.execute(
        f"SELECT par, tf, MAX(time_utc) t FROM decisoes "
        f"WHERE {filtro} AND variante = 'F_BREAKOUT' GROUP BY par, tf"
    ):
        marca[("BRK", r["par"], r["tf"])] = r["t"]
    return marca


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

    ultimo_visto = None
    with db.sessao() as conn:
        if uma_vez:
            # Em debug, força avaliar o candle atual mesmo que já visto.
            um_ciclo(conn, {})
            return
        ultimo_visto = watermark_inicial(conn)   # não redecide candle já decidido (restart)
        while not _parar:
            um_ciclo(conn, ultimo_visto)
            for _ in range(config.DECISAO_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    log.info("Estrategista encerrado.")


if __name__ == "__main__":
    main()
