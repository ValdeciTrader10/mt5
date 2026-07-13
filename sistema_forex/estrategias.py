"""Fase 4 — Lógica de decisão de entrada (PURA e testável).

Recebe um "snapshot" do mercado (regime, ATR, preço, spread, hora e os níveis/
estrutura que o motor calculou) e devolve UMA decisão: entrar (com direção,
estratégia, confluências) ou não entrar (com o motivo). Nada aqui toca no MT5 nem
no banco — o serviço `decisao.py` monta o snapshot e persiste o resultado.

Modelo v1 — confluências + filtros (gates):
  Direção candidata pelo regime (tendência) ou pelo extremo (lateral). Somam-se
  confluências a favor (regime, nível forte, evento de estrutura, FVG). Entra só se
  passar nos filtros duros (sessão, spread) E o score >= SCORE_MIN_CONFLUENCIAS.

O catálogo de estratégias do doc pode ser plugado depois; a espinha (snapshot →
confluências → gates → decisão auditável) já fica pronta e testada.

2ª estratégia — `sweep_choch_v1` (liquidity sweep + CHoCH no M5):
  A entrada de MAIOR qualidade do doc/skill (§4). Roda EM PARALELO à confluencia_v1
  (cada uma grava a própria decisão; o executor deduplica no nível de posição). É uma
  hipótese própria (stop-hunt + mudança de caráter), com gatilho autossuficiente — S/R
  entra só como REFORÇO (nunca veto), regime nunca gateia (é reversão, brilha no extremo).
"""

from . import indicadores

ESTRATEGIA = "confluencia_v1"
ESTRATEGIA_SWEEP = "sweep_choch_v1"
ESTRATEGIA_OB = "order_block_v1"
ESTRATEGIA_PULLBACK = "pullback_tendencia_v1"
ESTRATEGIA_GAP = "fecha_gap_v1"
ESTRATEGIA_ROMPIMENTO = "pullback_rompimento_v1"
ESTRATEGIA_EXTREMOS = "rompimento_extremos_v1"


def _mais_forte_perto(preco: float, niveis: list, tol: float):
    """Nível (preco, forca) mais forte dentro de `tol` do preço, ou None."""
    candidatos = [(p, f) for p, f in niveis if abs(preco - p) <= tol]
    return max(candidatos, key=lambda x: x[1]) if candidatos else None


def candle_rejeicao(snap: dict, direcao: str, nivel: float, tol: float, pavio_min: float) -> bool:
    """True se o último candle REJEITOU o nível na direção desejada.

    "O preço parou no nível e mostrou reversão": o candle entra na zona do S/R, deixa um
    pavio contrário ≥ `pavio_min` do range e FECHA de volta (na metade a favor).
      - compra: rejeição em SUPORTE (pavio inferior longo, fecha na metade de cima).
      - venda:  rejeição em RESISTÊNCIA (pavio superior longo, fecha na metade de baixo).
    """
    o, h, l, c = snap.get("open"), snap.get("high"), snap.get("low"), snap.get("close")
    if None in (o, h, l, c):
        return False
    rng = h - l
    if rng <= 0:
        return False
    if direcao == "compra":
        tocou = l <= nivel + tol                  # o candle entrou na zona do suporte
        pavio = (min(o, c) - l) / rng             # pavio inferior (rejeição de baixo)
        fechou_a_favor = c >= l + rng * 0.5
        return tocou and pavio >= pavio_min and fechou_a_favor
    tocou = h >= nivel - tol                       # zona da resistência
    pavio = (h - max(o, c)) / rng                  # pavio superior (rejeição de cima)
    fechou_a_favor = c <= h - rng * 0.5
    return tocou and pavio >= pavio_min and fechou_a_favor


def _decisao(resultado, direcao, regime, score, confluencias, motivo, estrategia=ESTRATEGIA):
    return {
        "resultado": resultado,           # entrou | nao_entrou
        "direcao": direcao,               # compra | venda | None
        "estrategia": estrategia,
        "regime": regime,
        "score": score,
        "confluencias": confluencias,
        "motivo": motivo,
    }


def avaliar(snap: dict, *, sessao_utc, spread_max_pips, score_min, nivel_prox_atr,
            forca_min, pavio_min=0.5, exigir_rejeicao=False) -> dict:
    """Avalia o snapshot e devolve a decisão (dict). Ver módulo para o modelo."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    tol = (nivel_prox_atr * atr) if atr else 0.0

    sup = snap.get("suportes", [])
    res = snap.get("resistencias", [])
    perto_sup = _mais_forte_perto(close, sup, tol) if tol else None
    perto_res = _mais_forte_perto(close, res, tol) if tol else None

    # --- Direção candidata ---
    if regime == "tendencia_alta":
        direcao = "compra"
    elif regime == "tendencia_baixa":
        direcao = "venda"
    elif regime == "lateral":
        if perto_sup and not perto_res:
            direcao = "compra"
        elif perto_res and not perto_sup:
            direcao = "venda"
        else:
            direcao = None
    else:
        direcao = None

    if direcao is None:
        return _decisao("nao_entrou", None, regime, 0, [], f"sem viés (regime={regime})")

    # --- Confluências a favor da direção ---
    conf = []
    if regime in ("tendencia_alta", "tendencia_baixa"):
        conf.append("regime")
    elif regime == "lateral":
        conf.append("extremo_lateral")

    nivel = perto_sup if direcao == "compra" else perto_res
    if nivel and nivel[1] >= forca_min:
        conf.append(f"nivel_forca_{int(nivel[1])}")

    ev = snap.get("ultimo_evento")
    if ev and ((direcao == "compra" and ev["direcao"] == "alta")
               or (direcao == "venda" and ev["direcao"] == "baixa")):
        conf.append(f"{ev['evento']}_{ev['tf']}")

    for f in snap.get("fvgs", []):
        a_favor = f["tipo"].endswith("bull") == (direcao == "compra")
        if a_favor and (f["base"] - tol) <= close <= (f["topo"] + tol):
            conf.append("fvg")
            break

    # Rejeição no nível (compra→suporte / venda→resistência) vira CONFLUÊNCIA que aumenta o
    # score — NÃO é obrigatória por padrão (para não engessar e secar as entradas). Só vira
    # gate no fade lateral quando exigir_rejeicao=True (modo estrito, opcional).
    nivel_ref = perto_sup if direcao == "compra" else perto_res
    rejeitou = bool(nivel_ref and candle_rejeicao(snap, direcao, nivel_ref[0], tol, pavio_min))
    if rejeitou:
        conf.append("rejeicao")
    score = len(conf)
    if exigir_rejeicao and regime == "lateral" and not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no nível (modo estrito)")

    # --- Filtros duros (gates) ---
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf, f"fora da sessão ({hora}h UTC)")

    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)")

    if score < score_min:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"confluências insuficientes ({score} < {score_min})")

    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf))


# --------------------------------------------------------------------------- #
# Estratégia 2 — liquidity sweep + CHoCH (M5). Função PURA de detecção.
# --------------------------------------------------------------------------- #
def _swings_hl(highs, lows, n_swing):
    """Swings fractais separados em (highs, lows) como listas de (indice, preco)."""
    sw = indicadores.swings(highs, lows, n_swing)
    hs = [(s["i"], s["preco"]) for s in sw if s["tipo"] == "high"]
    ls = [(s["i"], s["preco"]) for s in sw if s["tipo"] == "low"]
    return hs, ls


def detectar_sweep_choch(opens, highs, lows, closes, atr, *, n_swing, sweep_min_atr,
                         sweep_recente):
    """Liquidity sweep + CHoCH nas velas M5 (a ÚLTIMA é a vela de decisão, já fechada).

    Sem look-ahead: decide só sobre candles fechados. Sequência (compra):
      1) SWEEP de um swing LOW: uma vela posterior fura o nível (pavio abaixo, penetração
         ≥ `sweep_min_atr`·ATR) mas FECHA de volta acima — stop-hunt/falha (bear trap).
      2) CHoCH de alta: a vela atual FECHA acima do swing HIGH mais recente (mudança de
         caráter), e a vela anterior ainda não — rompimento fresco (dispara uma vez).
    Venda é o espelho (varre swing high, fecha de volta abaixo, CHoCH de baixa).

    Retorna {'direcao','nivel_sweep','nivel_choch','i_sweep'} ou None.
    """
    n = len(closes)
    if not atr or atr <= 0 or n < n_swing * 2 + 2:
        return None
    last = n - 1
    pen = sweep_min_atr * atr
    highs_sw, lows_sw = _swings_hl(highs, lows, n_swing)

    # ---- Compra: varre swing low, fecha de volta, e rompe o swing high recente ----
    h_ref = next((p for i, p in reversed(highs_sw) if i < last), None)
    if h_ref is not None and closes[last] > h_ref >= closes[last - 1]:   # CHoCH de alta fresco
        for i_low, nivel in reversed(lows_sw):          # swing low mais recente primeiro
            if nivel >= h_ref:
                continue
            i_sweep = None
            for k in range(i_low + 1, last + 1):        # vela que varreu e fechou de volta
                if lows[k] < nivel - pen and closes[k] > nivel:
                    i_sweep = k
            if i_sweep is not None and (last - i_sweep) <= sweep_recente:
                return {"direcao": "compra", "nivel_sweep": nivel,
                        "nivel_choch": h_ref, "i_sweep": i_sweep}

    # ---- Venda: varre swing high, fecha de volta, e rompe o swing low recente ----
    l_ref = next((p for i, p in reversed(lows_sw) if i < last), None)
    if l_ref is not None and closes[last] < l_ref <= closes[last - 1]:   # CHoCH de baixa fresco
        for i_high, nivel in reversed(highs_sw):
            if nivel <= l_ref:
                continue
            i_sweep = None
            for k in range(i_high + 1, last + 1):
                if highs[k] > nivel + pen and closes[k] < nivel:
                    i_sweep = k
            if i_sweep is not None and (last - i_sweep) <= sweep_recente:
                return {"direcao": "venda", "nivel_sweep": nivel,
                        "nivel_choch": l_ref, "i_sweep": i_sweep}
    return None


def avaliar_sweep_choch(snap: dict, *, sessao_utc, spread_max_pips, n_swing, sweep_min_atr,
                        sweep_recente, nivel_prox_atr, forca_min) -> dict:
    """Avalia a 2ª estratégia (sweep+CHoCH) sobre a janela M5 do snapshot.

    O padrão É o sinal (não exige score mínimo de confluências como a confluencia_v1).
    Gates duros continuam valendo (sessão + spread — liquidez/custo). S/R forte no nível
    varrido é REFORÇO informativo (nunca veto); regime nunca gateia.
    """
    regime = snap.get("regime", "indefinido")
    jan = snap.get("m5_janela")
    atr = snap.get("atr")
    if not jan or not jan.get("close") or not atr:
        return _decisao("nao_entrou", None, regime, 0, [], "sem janela M5/ATR",
                        estrategia=ESTRATEGIA_SWEEP)

    det = detectar_sweep_choch(jan["open"], jan["high"], jan["low"], jan["close"], atr,
                               n_swing=n_swing, sweep_min_atr=sweep_min_atr,
                               sweep_recente=sweep_recente)
    if det is None:
        return _decisao("nao_entrou", None, regime, 0, [], "sem sweep+choch",
                        estrategia=ESTRATEGIA_SWEEP)

    direcao = det["direcao"]
    conf = ["sweep+choch"]

    # S/R forte no nível varrido = versão premium (sweep de liquidez EM S/R real). Reforço.
    tol = nivel_prox_atr * atr
    niveis = snap.get("suportes" if direcao == "compra" else "resistencias", [])
    nv = _mais_forte_perto(det["nivel_sweep"], niveis, tol)
    if nv and nv[1] >= forca_min:
        conf.append(f"sr_confluente_{int(nv[1])}")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    # Gates duros (iguais aos da confluencia_v1): sessão e spread.
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_SWEEP)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_SWEEP)

    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_SWEEP)


# --------------------------------------------------------------------------- #
# Estratégia 3 — reteste de Order Block (M15/H1) + rejeição
# --------------------------------------------------------------------------- #
def _ob_retestado(close: float, obs: list, tol: float):
    """OB fresco cujo preço está retestando (dentro da zona ± tol). O mais PRÓXIMO do
    preço vence. Retorna (direcao, ob) ou None."""
    alvo = None
    for ob in obs:
        base, topo = ob["base"], ob["topo"]
        if base - tol <= close <= topo + tol:
            dist = abs(close - (base + topo) / 2)
            if alvo is None or dist < alvo[0]:
                direc = "compra" if ob["tipo"] == "ob_bull" else "venda"
                alvo = (dist, direc, ob)
    return (alvo[1], alvo[2]) if alvo else None


def avaliar_order_block(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                        forca_min, pavio_min=0.5, exigir_rejeicao=False) -> dict:
    """Avalia o reteste de Order Block. O OB fresco + preço retestando É o setup; S/R e
    regime a favor são REFORÇO (nunca veto). Rejeição na borda é confluência (gate só se
    exigir_rejeicao=True). Gates duros: sessão + spread."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    obs = snap.get("obs", [])
    if atr is None or not obs:
        return _decisao("nao_entrou", None, regime, 0, [], "sem OB fresco/ATR",
                        estrategia=ESTRATEGIA_OB)
    tol = nivel_prox_atr * atr
    alvo = _ob_retestado(close, obs, tol)
    if alvo is None:
        return _decisao("nao_entrou", None, regime, 0, [], "preço fora das zonas de OB",
                        estrategia=ESTRATEGIA_OB)
    direcao, ob = alvo
    conf = ["order_block"]
    # rejeição na borda de entrada da zona (compra→base como suporte; venda→topo como resistência)
    nivel_ref = ob["base"] if direcao == "compra" else ob["topo"]
    rejeitou = candle_rejeicao(snap, direcao, nivel_ref, tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    niveis = snap.get("suportes" if direcao == "compra" else "resistencias", [])
    nv = _mais_forte_perto(nivel_ref, niveis, tol)
    if nv and nv[1] >= forca_min:
        conf.append(f"sr_confluente_{int(nv[1])}")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    if exigir_rejeicao and not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no OB (modo estrito)", estrategia=ESTRATEGIA_OB)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_OB)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_OB)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_OB)


# --------------------------------------------------------------------------- #
# Estratégia 4 — pullback a favor da tendência + rejeição em S/R forte
# --------------------------------------------------------------------------- #
def avaliar_pullback_tendencia(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                               forca_min, pavio_min=0.5) -> dict:
    """Em tendência (H1), o preço recua a um S/R FORTE na direção da tendência, REJEITA e
    retoma. A rejeição é o GATILHO (obrigatória — é a tese). OB fresco coincidente = reforço.
    Gates duros: sessão + spread. Contra a tendência: nem avalia."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    if atr is None:
        return _decisao("nao_entrou", None, regime, 0, [], "sem ATR",
                        estrategia=ESTRATEGIA_PULLBACK)
    if regime == "tendencia_alta":
        direcao = "compra"
    elif regime == "tendencia_baixa":
        direcao = "venda"
    else:
        return _decisao("nao_entrou", None, regime, 0, [], f"sem tendência (regime={regime})",
                        estrategia=ESTRATEGIA_PULLBACK)
    tol = nivel_prox_atr * atr
    niveis = snap.get("suportes" if direcao == "compra" else "resistencias", [])
    nv = _mais_forte_perto(close, niveis, tol)
    if not nv or nv[1] < forca_min:
        return _decisao("nao_entrou", direcao, regime, 0, ["regime"],
                        "sem S/R forte no pullback", estrategia=ESTRATEGIA_PULLBACK)
    conf = ["regime", f"nivel_forca_{int(nv[1])}"]
    rejeitou = candle_rejeicao(snap, direcao, nv[0], tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    for ob in snap.get("obs", []):
        d2 = "compra" if ob["tipo"] == "ob_bull" else "venda"
        if d2 == direcao and ob["base"] - tol <= close <= ob["topo"] + tol:
            conf.append("ob_confluente")
            break
    score = len(conf)

    if not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no pullback", estrategia=ESTRATEGIA_PULLBACK)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_PULLBACK)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_PULLBACK)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_PULLBACK)


# --------------------------------------------------------------------------- #
# Estratégia 5 — fechamento de gap (fade rumo ao fechamento anterior)
# --------------------------------------------------------------------------- #
def avaliar_fecha_gap(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                      forca_min, gap_min_atr=0.5) -> dict:
    """Gap de sessão/notícia tende a ser PREENCHIDO (o preço volta ao fechamento anterior).
    Opera A FAVOR do preenchimento: gap de ALTA (abriu acima) → VENDA rumo ao nível de
    fechamento (abaixo); gap de BAIXA → COMPRA rumo ao nível (acima). Gatilho = a vela virou
    na direção do fill (momentum) e o gap AINDA tem espaço (não preenchido). S/R no alvo e a
    rejeição no extremo são REFORÇO (nunca veto). Gates duros: sessão + spread.

    `gaps` no snapshot: [{'direcao': 'alta'|'baixa', 'nivel': preço-alvo (fechamento anterior)}].
    O gap mais PRÓXIMO com espaço suficiente vence (evita alvo longe demais/já preenchido).
    """
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    o = snap.get("open")
    gaps = snap.get("gaps", [])
    if atr is None or o is None or not gaps:
        return _decisao("nao_entrou", None, regime, 0, [], "sem gap aberto/ATR",
                        estrategia=ESTRATEGIA_GAP)
    tol = nivel_prox_atr * atr
    alvo = None
    for g in gaps:
        nivel = g["nivel"]
        if g["direcao"] == "alta" and close > nivel:        # abriu acima → fill p/ baixo → venda
            dist = close - nivel
            if alvo is None or dist < alvo[0]:
                alvo = (dist, "venda", nivel)
        elif g["direcao"] == "baixa" and close < nivel:     # abriu abaixo → fill p/ cima → compra
            dist = nivel - close
            if alvo is None or dist < alvo[0]:
                alvo = (dist, "compra", nivel)
    if alvo is None:
        return _decisao("nao_entrou", None, regime, 0, [], "gap já preenchido",
                        estrategia=ESTRATEGIA_GAP)
    dist, direcao, nivel = alvo
    if dist < gap_min_atr * atr:
        return _decisao("nao_entrou", direcao, regime, 0, ["gap"],
                        "sem espaço até o alvo do gap", estrategia=ESTRATEGIA_GAP)
    # Gatilho: a vela fechou NA direção do fill (momentum de reversão para o gap).
    momentum = (close < o) if direcao == "venda" else (close > o)
    conf = ["gap"]
    if momentum:
        conf.append("momentum_fill")
    if candle_rejeicao(snap, direcao, close, tol, 0.5):     # rejeição no extremo = reforço
        conf.append("rejeicao")
    niveis = snap.get("suportes" if direcao == "compra" else "resistencias", [])
    nv = _mais_forte_perto(nivel, niveis, tol)
    if nv and nv[1] >= forca_min:
        conf.append(f"sr_alvo_{int(nv[1])}")
    score = len(conf)

    if not momentum:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem momentum p/ o fill", estrategia=ESTRATEGIA_GAP)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_GAP)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_GAP)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_GAP)


# --------------------------------------------------------------------------- #
# Estratégia 6 — pullback ao rompimento (reteste com inversão de polaridade)
# --------------------------------------------------------------------------- #
def avaliar_pullback_rompimento(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                                forca_min, pavio_min=0.5) -> dict:
    """Rompimento + reteste (polaridade): um nível S/R foi ROMPIDO (BOS) e o preço RETESTA o
    nível agora INVERTIDO — resistência rompida vira suporte / suporte rompido vira resistência
    — e REJEITA, retomando na direção do rompimento. A rejeição no nível invertido é o GATILHO
    (é a confirmação do reteste). Direção pelo BOS; regime a favor e força do nível = reforço.
    Gates duros: sessão + spread. Distinto do `pullback_tendencia` (que rejeita num S/R do MESMO
    lado da tendência) — aqui o nível é o que FOI rompido (polaridade invertida)."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    ev = snap.get("ultimo_evento")
    if atr is None or not ev or ev.get("evento") != "BOS":
        return _decisao("nao_entrou", None, regime, 0, [], "sem rompimento (BOS)",
                        estrategia=ESTRATEGIA_ROMPIMENTO)
    if ev["direcao"] == "alta":
        direcao = "compra"
        niveis = snap.get("resistencias", [])      # resistência rompida → agora suporte
    elif ev["direcao"] == "baixa":
        direcao = "venda"
        niveis = snap.get("suportes", [])           # suporte rompido → agora resistência
    else:
        return _decisao("nao_entrou", None, regime, 0, [], "BOS sem direção",
                        estrategia=ESTRATEGIA_ROMPIMENTO)
    tol = nivel_prox_atr * atr
    nv = _mais_forte_perto(close, niveis, tol)
    if not nv:
        return _decisao("nao_entrou", direcao, regime, 0, ["bos"],
                        "sem nível rompido no reteste", estrategia=ESTRATEGIA_ROMPIMENTO)
    conf = ["bos", "reteste_rompimento"]
    if nv[1] >= forca_min:
        conf.append(f"nivel_forca_{int(nv[1])}")
    rejeitou = candle_rejeicao(snap, direcao, nv[0], tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    if not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no reteste", estrategia=ESTRATEGIA_ROMPIMENTO)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_ROMPIMENTO)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_ROMPIMENTO)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_ROMPIMENTO)


# --------------------------------------------------------------------------- #
# Estratégia 7 — rompimento da máx/mín do dia anterior (PDH/PDL) + reteste
# --------------------------------------------------------------------------- #
def avaliar_rompimento_extremos(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                                pavio_min=0.5) -> dict:
    """Rompimento da MÁXIMA/MÍNIMA do dia anterior (PDH/PDL — liquidez clássica) + reteste.
    Preço rompe a PDH e RETESTA por cima (nível vira suporte) rejeitando → COMPRA; rompe a PDL
    e retesta por baixo (vira resistência) → VENDA. A rejeição no reteste é o GATILHO; regime a
    favor = reforço. Gates duros: sessão + spread.

    `max_dia`/`min_dia` no snapshot = high/low do último dia FECHADO (D1)."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    pdh = snap.get("max_dia")
    pdl = snap.get("min_dia")
    if atr is None or pdh is None or pdl is None:
        return _decisao("nao_entrou", None, regime, 0, [], "sem máx/mín do dia/ATR",
                        estrategia=ESTRATEGIA_EXTREMOS)
    tol = nivel_prox_atr * atr
    # Rompeu (close além do extremo) e ainda RETESTANDO (a até `tol` do nível rompido).
    if close >= pdh and (close - pdh) <= tol:
        direcao, nivel = "compra", pdh
    elif close <= pdl and (pdl - close) <= tol:
        direcao, nivel = "venda", pdl
    else:
        return _decisao("nao_entrou", None, regime, 0, [], "sem reteste de máx/mín do dia",
                        estrategia=ESTRATEGIA_EXTREMOS)
    conf = ["rompeu_extremo_dia"]
    rejeitou = candle_rejeicao(snap, direcao, nivel, tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    if not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no reteste", estrategia=ESTRATEGIA_EXTREMOS)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_EXTREMOS)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_EXTREMOS)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_EXTREMOS)
