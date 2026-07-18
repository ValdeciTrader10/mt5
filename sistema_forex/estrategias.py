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

from . import fuzzy_score, indicadores

ESTRATEGIA = "confluencia_v1"
ESTRATEGIA_SWEEP = "sweep_choch_v1"
ESTRATEGIA_SWEEP_ABS = "sweep_choch_abs_v1"   # gêmea da caça-stops COM filtro de absorção
ESTRATEGIA_OB = "order_block_v1"
ESTRATEGIA_OB_REJ = "order_block_rej_v1"   # gêmeo do OB que EXIGE rejeição na borda do bloco
ESTRATEGIA_PULLBACK = "pullback_tendencia_v1"
ESTRATEGIA_GAP = "fecha_gap_v1"
ESTRATEGIA_ROMPIMENTO = "pullback_rompimento_v1"
ESTRATEGIA_EXTREMOS = "rompimento_extremos_v1"
ESTRATEGIA_MEDIAS = "pullback_medias_v1"
ESTRATEGIA_PIVOT = "pivot_confluencia_v1"
ESTRATEGIA_FUZZY_PURO = "fuzzy_puro_v1"      # Variante B (ETAPA 5)
ESTRATEGIA_FUZZY_PURO_LIMA = "fuzzy_puro_lima_v1"  # Variante B2 (item 4): mesma lógica, maré = Lima (76)
# Família D_LINHAS — estratégias pela DINÂMICA das curvas de score por TF (não o nível estático).
ESTRATEGIA_DIVERGENCIA = "fuzzy_divergencia_v1"        # A — esforço×resultado (score vs preço)
ESTRATEGIA_PULLBACK_LEQUE = "fuzzy_pullback_leque_v1"  # B — leque: rápida recua e reengata na maré
ESTRATEGIA_SYNC_FLIP = "fuzzy_sync_flip_v1"            # C — convergência: sync alinha rompendo a VWAP
ESTRATEGIA_EXAUSTAO = "fuzzy_exaustao_v1"              # D — clímax: score saturado rola na banda ±2σ
# Família E_SENTINELA — critérios da FORÇA contínua (micro/macro) + LEQUE (inspirado no Sentinela do PDF).
ESTRATEGIA_SENT_FORCA = "sentinela_forca_v1"          # força alinhada cruza o limiar rompendo a VWAP
ESTRATEGIA_SENT_DIVERG = "sentinela_divergencia_v1"   # micro×macro divergem (ATENÇÃO) → fade a favor do macro
ESTRATEGIA_SENT_LEQUE = "sentinela_leque_v1"          # leque comprime (mola) e expande na direção da força
# Família F_BREAKOUT — rompimento da faixa de abertura de Londres (validado fora da amostra).
ESTRATEGIA_BREAKOUT = "breakout_londres_v1"           # saída: corre até o fim da janela (máx expectância)
ESTRATEGIA_BREAKOUT_PROT = "breakout_londres_prot_v1" # + proteção: trava +2p após +10p (curva suave)

# Variante do laboratório multi-variante. As estratégias deste módulo são o GRUPO DE CONTROLE
# (Variante A). B_FUZZY_PURO / C_HIBRIDA marcam a decisão via este campo (ETAPAS 5-6).
VARIANTE_A = "A_ORIGINAL"
VARIANTE_B = "B_FUZZY_PURO"
VARIANTE_C = "C_HIBRIDA"
VARIANTE_C_CORRE = "C_CORRE"   # experimento: MESMAS entradas da C, mas saída "deixa correr" (gestor
                               # genérico: stop + giveback estrutural, SEM o corte fuzzy antecipado).
                               # Isola o EFEITO DA SAÍDA (C_HIBRIDA corta cedo × C_CORRE deixa andar).
VARIANTE_LINHAS = "D_LINHAS"   # 4º cenário: estratégias pela dinâmica das linhas de score fuzzy
VARIANTE_SENTINELA = "E_SENTINELA"   # 5º cenário: força contínua micro/macro + leque (ideia do Sentinela)
VARIANTE_BREAKOUT = "F_BREAKOUT"     # 6º cenário: rompimento da faixa de abertura de Londres

# Estratégias da Variante A cujo gatilho é uma ZONA (S/R, order block, pivot): na Variante C, a
# VIRADA de score do fuzzy na zona (transição de causa) confirma o giro do preço no nível.
ESTRATEGIAS_ZONA = (ESTRATEGIA, ESTRATEGIA_OB, ESTRATEGIA_PIVOT)

# Cenários nomeados do operacional Fuzzy Wyckoff (Variante B) — sempre logados na decisão.
CENARIOS_ENTRADA = ("ESTOURO", "PULLBACK_VWAP")     # habilitam entrada
CENARIOS_BLOQUEIO = ("EXAUSTAO", "ABSORCAO_TOPO")   # bloqueiam entrada


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


def _decisao(resultado, direcao, regime, score, confluencias, motivo, estrategia=ESTRATEGIA,
             variante=VARIANTE_A):
    return {
        "resultado": resultado,           # entrou | nao_entrou
        "direcao": direcao,               # compra | venda | None
        "estrategia": estrategia,
        "regime": regime,
        "score": score,
        "confluencias": confluencias,
        "motivo": motivo,
        "variante": variante,             # laboratório multi-variante (A_ORIGINAL por padrão)
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


def avaliar_sweep_choch_abs(snap: dict, *, sessao_utc, spread_max_pips, n_swing, sweep_min_atr,
                            sweep_recente, nivel_prox_atr, forca_min, absorcao_janela=20) -> dict:
    """Gêmea da caça-stops (`sweep_choch_v1`) que EXIGE ABSORÇÃO no candle da varredura.

    MESMA detecção (`detectar_sweep_choch`) e MESMOS gates (sessão/spread) da `sweep_choch_v1`;
    a ÚNICA diferença é um filtro extra: a vela que varreu a liquidez (`i_sweep`) tem de mostrar
    ABSORÇÃO — volume alto + corpo fraco (esforço sem resultado), a leitura Wyckoff de que o
    "smart money" ABSORVEU a liquidez varrida (spring/absorção). Usa a MESMA definição de
    absorção do resto do sistema (`fuzzy_score`), sem look-ahead (só candles ≤ i_sweep).

    É um LIVRO DE SOMBRA INDEPENDENTE e diretamente comparável à `sweep_choch_v1`: como suas
    entradas são o subconjunto das sweeps que tiveram absorção, a expectância das duas responde
    empiricamente ao pedido do dono — a caça-stops é MAIS lucrativa COM ou SEM o filtro de
    absorção? Não toca a `sweep_choch_v1` (grupo de controle intocável).
    """
    regime = snap.get("regime", "indefinido")
    jan = snap.get("m5_janela")
    atr = snap.get("atr")
    if not jan or not jan.get("close") or not atr:
        return _decisao("nao_entrou", None, regime, 0, [], "sem janela M5/ATR",
                        estrategia=ESTRATEGIA_SWEEP_ABS)

    det = detectar_sweep_choch(jan["open"], jan["high"], jan["low"], jan["close"], atr,
                               n_swing=n_swing, sweep_min_atr=sweep_min_atr,
                               sweep_recente=sweep_recente)
    if det is None:
        return _decisao("nao_entrou", None, regime, 0, [], "sem sweep+choch",
                        estrategia=ESTRATEGIA_SWEEP_ABS)

    # Filtro que DEFINE esta variante: absorção na vela da varredura (volume alto + corpo fraco).
    vols = jan.get("volume")
    flags = None
    if vols:
        flags = fuzzy_score.flags_no_indice(jan["open"], jan["high"], jan["low"], jan["close"],
                                            vols, det["i_sweep"], janela=absorcao_janela)
    if not flags or not flags.get("absorcao"):
        motivo = "sweep sem absorção" if flags else "sem volume p/ medir absorção"
        return _decisao("nao_entrou", det["direcao"], regime, 0, ["sweep+choch"], motivo,
                        estrategia=ESTRATEGIA_SWEEP_ABS)

    direcao = det["direcao"]
    conf = ["sweep+choch", "absorcao"]

    # S/R forte no nível varrido = reforço (idêntico à sweep_choch_v1). Nunca veto.
    tol = nivel_prox_atr * atr
    niveis = snap.get("suportes" if direcao == "compra" else "resistencias", [])
    nv = _mais_forte_perto(det["nivel_sweep"], niveis, tol)
    if nv and nv[1] >= forca_min:
        conf.append(f"sr_confluente_{int(nv[1])}")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    # Gates duros (iguais aos da sweep_choch_v1): sessão e spread.
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_SWEEP_ABS)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_SWEEP_ABS)

    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_SWEEP_ABS)


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
                        forca_min, pavio_min=0.5, exigir_rejeicao=False,
                        estrategia=ESTRATEGIA_OB) -> dict:
    """Avalia o reteste de Order Block. O OB fresco + preço retestando É o setup; S/R e
    regime a favor são REFORÇO (nunca veto). Rejeição na borda é confluência (gate só se
    exigir_rejeicao=True — usado pelo gêmeo `order_block_rej_v1`). Gates duros: sessão + spread."""
    _d = lambda *a: _decisao(*a, estrategia=estrategia)
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    obs = snap.get("obs", [])
    if atr is None or not obs:
        return _d("nao_entrou", None, regime, 0, [], "sem OB fresco/ATR")
    tol = nivel_prox_atr * atr
    alvo = _ob_retestado(close, obs, tol)
    if alvo is None:
        return _d("nao_entrou", None, regime, 0, [], "preço fora das zonas de OB")
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
        return _d("nao_entrou", direcao, regime, score, conf, "sem rejeição no OB (modo estrito)")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, score, conf, f"fora da sessão ({hora}h UTC)")
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _d("nao_entrou", direcao, regime, score, conf,
                  f"spread alto ({spread:.1f}p > {spread_max_pips}p)")
    return _d("entrou", direcao, regime, score, conf, "+".join(conf))


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
    # Usa o próprio close como "nível" → o toque é trivial; o que isto mede de verdade é um
    # PAVIO CONTRÁRIO grande (≥50% da vela) — rotulado honestamente p/ a auditoria de confluências.
    if candle_rejeicao(snap, direcao, close, tol, 0.5):
        conf.append("pavio_contrario")
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


# --------------------------------------------------------------------------- #
# Estratégia 8 — pullback a médias (toque EMA9/EMA20 do TF acima) em tendência
# --------------------------------------------------------------------------- #
def _perto_media(close: float, medias: dict, tol: float):
    """Média (EMA9/EMA20) mais próxima do preço dentro de `tol`, ou None. Retorna (chave, valor)."""
    cand = [(k, medias[k]) for k in ("ema9", "ema20") if medias.get(k) is not None
            and abs(close - medias[k]) <= tol]
    return min(cand, key=lambda x: abs(close - x[1])) if cand else None


def avaliar_pullback_medias(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                            pavio_min=0.5) -> dict:
    """A FAVOR da tendência (regime), o preço RECUA e toca a EMA9/EMA20 do TF ACIMA (a média
    como suporte/resistência dinâmica) e retoma. O toque na média é o setup; FVG/OB coincidente
    DOBRA o score (confluência forte — doc); rejeição no candle e regime são reforço. Gates duros:
    sessão + spread. Contra a tendência: nem avalia. `medias_acima` no snapshot = médias do TF
    superior (ex.: M5 opera, lê as médias do M15)."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    medias = snap.get("medias_acima") or {}
    if atr is None or not medias:
        return _decisao("nao_entrou", None, regime, 0, [], "sem médias/ATR",
                        estrategia=ESTRATEGIA_MEDIAS)
    if regime == "tendencia_alta":
        direcao = "compra"
    elif regime == "tendencia_baixa":
        direcao = "venda"
    else:
        return _decisao("nao_entrou", None, regime, 0, [], f"sem tendência (regime={regime})",
                        estrategia=ESTRATEGIA_MEDIAS)
    tol = nivel_prox_atr * atr
    perto = _perto_media(close, medias, tol)
    if perto is None:
        return _decisao("nao_entrou", direcao, regime, 0, ["regime"],
                        "preço longe das médias", estrategia=ESTRATEGIA_MEDIAS)
    chave, nivel = perto
    conf = ["regime", f"toque_{chave}"]
    rejeitou = candle_rejeicao(snap, direcao, nivel, tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    # FVG ou OB coincidente com a zona da média DOBRA o score (confluência forte — doc).
    dobra = False
    for f in snap.get("fvgs", []):
        if f["tipo"].endswith("bull") == (direcao == "compra") and \
           (f["base"] - tol) <= nivel <= (f["topo"] + tol):
            dobra = True
            conf.append("fvg_confluente")
            break
    for ob in snap.get("obs", []):
        d2 = "compra" if ob["tipo"] == "ob_bull" else "venda"
        if d2 == direcao and ob["base"] - tol <= nivel <= ob["topo"] + tol:
            dobra = True
            if "ob_confluente" not in conf:
                conf.append("ob_confluente")
            break
    score = len(conf) * 2 if dobra else len(conf)

    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_MEDIAS)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_MEDIAS)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_MEDIAS)


# --------------------------------------------------------------------------- #
# Estratégia 9 — toque em pivot (PP/R1/S1) confluente com S/R/OB + rejeição
# --------------------------------------------------------------------------- #
def _pivot_perto(close: float, pivots: list, tol: float):
    """Pivot (preco, tipo) mais próximo do preço dentro de `tol`, ou None."""
    cand = [(p, t) for p, t in pivots if abs(close - p) <= tol]
    return min(cand, key=lambda x: abs(close - x[0])) if cand else None


def _zona_perto(preco: float, snap: dict, tol: float) -> bool:
    """Há uma zona de S/R ou de OB dentro de `tol` de `preco`? (a confluência exigida)."""
    for p, _f in list(snap.get("suportes", [])) + list(snap.get("resistencias", [])):
        if abs(p - preco) <= tol:
            return True
    for ob in snap.get("obs", []):
        if ob["base"] - tol <= preco <= ob["topo"] + tol:
            return True
    return False


def avaliar_pivot_confluencia(snap: dict, *, sessao_utc, spread_max_pips, nivel_prox_atr,
                              pivot_sr_atr=0.5, pavio_min=0.5) -> dict:
    """Toque num pivot clássico (PP/R1-3/S1-3) que está a < `pivot_sr_atr`×ATR de uma zona de
    S/R ou OB (confluência OBRIGATÓRIA — o que dá força ao pivot) e REJEITA (gatilho). É um FADE
    do nível: pivot acima do preço = resistência → venda; abaixo = suporte → compra (brilha no
    lateral). Regime a favor = reforço. Gates duros: sessão + spread. `pivots` no snapshot =
    [(preco, tipo)] dos níveis pivot_* do motor."""
    regime = snap.get("regime", "indefinido")
    atr = snap.get("atr")
    close = snap["close"]
    pivots = snap.get("pivots", [])
    if atr is None or not pivots:
        return _decisao("nao_entrou", None, regime, 0, [], "sem pivots/ATR",
                        estrategia=ESTRATEGIA_PIVOT)
    tol = nivel_prox_atr * atr
    perto = _pivot_perto(close, pivots, tol)
    if perto is None:
        return _decisao("nao_entrou", None, regime, 0, [], "preço longe de pivot",
                        estrategia=ESTRATEGIA_PIVOT)
    nivel, tipo_pivot = perto
    if not _zona_perto(nivel, snap, pivot_sr_atr * atr):
        return _decisao("nao_entrou", None, regime, 0, ["pivot"],
                        "pivot sem confluência S/R/OB", estrategia=ESTRATEGIA_PIVOT)
    # Fade: pivot atua como suporte (abaixo/no preço) → compra; como resistência (acima) → venda.
    direcao = "compra" if close >= nivel else "venda"
    conf = ["pivot", f"confluencia_sr_{tipo_pivot}"]
    rejeitou = candle_rejeicao(snap, direcao, nivel, tol, pavio_min)
    if rejeitou:
        conf.append("rejeicao")
    if (direcao == "compra" and regime == "tendencia_alta") or \
       (direcao == "venda" and regime == "tendencia_baixa"):
        conf.append("a_favor_regime")
    score = len(conf)

    if not rejeitou:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        "sem rejeição no pivot", estrategia=ESTRATEGIA_PIVOT)
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"fora da sessão ({hora}h UTC)", estrategia=ESTRATEGIA_PIVOT)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=ESTRATEGIA_PIVOT)
    return _decisao("entrou", direcao, regime, score, conf, "+".join(conf),
                    estrategia=ESTRATEGIA_PIVOT)


# --------------------------------------------------------------------------- #
# VARIANTE B — Fuzzy Puro (ETAPA 5). Reprodução fiel do operacional Fuzzy Wyckoff.
#
# NÃO altera nenhuma estratégia da Variante A (princípio governante: tudo é aditivo). Roda em
# SOMBRA marcada variante=B_FUZZY_PURO, no TF de TIMING (M1), com a PIRÂMIDE MTF ESTRITA:
#   M15 = maré (viés macro)  ·  M5 = correnteza (setup)  ·  M1 = timing (gatilho).
# Lê os fuzzy_scores (motor, ETAPA 3), a VWAP+bandas e os 20 closes (desvio-padrão manual) e
# classifica o setup num CENÁRIO NOMEADO. Entra só em ESTOURO/PULLBACK_VWAP e com o checklist de
# 6 itens satisfeito; bloqueia em EXAUSTÃO/ABSORÇÃO DE TOPO. Saída técnica (SMA50/VWAP oposta) é
# função pura pronta para o executor plugar.
# --------------------------------------------------------------------------- #
def _lado_fuzzy(score, minimo: float) -> int:
    """Lado do TF pelo score fuzzy: +1 comprador (>=minimo), -1 vendedor (<=100-minimo), 0 neutro."""
    if score is None:
        return 0
    if score >= minimo:
        return 1
    if score <= 100 - minimo:
        return -1
    return 0


def classificar_cenario_fuzzy(snap: dict, direcao: str, forca_ok: bool):
    """Classifica o setup num cenário nomeado do Fuzzy Wyckoff (ou None). Ordem de prioridade:
    EXAUSTÃO e ABSORÇÃO DE TOPO (bloqueio) vêm antes de ESTOURO/PULLBACK_VWAP (entrada)."""
    fz = snap.get("fuzzy") or {}
    vw = snap.get("vwap") or {}
    close = snap["close"]
    m1 = fz.get("M1") or {}
    m5 = fz.get("M5") or {}
    vwap, sup1, inf1 = vw.get("vwap"), vw.get("sup1"), vw.get("inf1")
    # EXAUSTÃO: clímax detectado (M1/M5) → reversão provável, não entra a favor do impulso.
    if m1.get("exaustao") or m5.get("exaustao"):
        return "EXAUSTAO"
    # ABSORÇÃO DE TOPO/FUNDO contra o trade (esforço sem resultado no extremo da VWAP).
    if (m1.get("absorcao") or m5.get("absorcao")) and vwap is not None:
        if direcao == "compra" and sup1 is not None and close >= sup1:
            return "ABSORCAO_TOPO"
        if direcao == "venda" and inf1 is not None and close <= inf1:
            return "ABSORCAO_TOPO"
    # ESTOURO: gatilho com FORÇA (corpo >= K·σ) rompendo a VWAP na direção (momentum com lastro).
    if forca_ok and vwap is not None:
        if direcao == "compra" and close > vwap:
            return "ESTOURO"
        if direcao == "venda" and close < vwap:
            return "ESTOURO"
    # PULLBACK VWAP: preço voltou à zona de valor (aquém da VWAP) a favor da maré.
    if vwap is not None:
        if direcao == "compra" and close <= vwap:
            return "PULLBACK_VWAP"
        if direcao == "venda" and close >= vwap:
            return "PULLBACK_VWAP"
    return None


def saida_tecnica_fuzzy_puro(direcao: str, close: float, sma50=None, vwap=None) -> bool:
    """Saída técnica da Variante B: fecha quando o preço passa para o lado OPOSTO da SMA50 ou da
    VWAP (perdeu a referência de tendência/valor). PURA e testável — pronta p/ o executor plugar
    (a sombra hoje cataloga a saída pelo gestor genérico; esta função reproduz a regra didática)."""
    for ref in (sma50, vwap):
        if ref is None:
            continue
        if direcao == "compra" and close < ref:
            return True
        if direcao == "venda" and close > ref:
            return True
    return False


def avaliar_fuzzy_puro(snap: dict, *, sessao_utc, spread_max_pips, mare_min, corrente_min,
                       timing_min, std_k, checklist_min, estrategia=ESTRATEGIA_FUZZY_PURO) -> dict:
    """Variante B (Fuzzy Puro). Pirâmide MTF estrita + checklist de 6 itens (compra/venda
    espelhados) + cenário nomeado. Decisão marcada variante=B_FUZZY_PURO / estrategia=fuzzy_puro_v1.
    O `estrategia` é parametrizável para rodar LIVROS PARALELOS da MESMA lógica com maré diferente
    (A/B do item 4: `fuzzy_puro_v1` com maré 60/verde vs `fuzzy_puro_lima_v1` com maré 76/lima) —
    cada rótulo é um livro de sombra independente, comparável no /relatorio.

    Checklist (compra; venda é o espelho):
      1) maré       — M15 comprador (score >= mare_min)
      2) correnteza — M5 comprador  (score >= corrente_min)
      3) timing     — M1 comprador  (score >= timing_min) OU transição de causa
      4) valor      — preço na/aquém da VWAP (localização de desconto)
      5) força      — corpo do gatilho >= std_k × desvio-padrão dos 20 closes (energia)
      6) fluxo      — sem exaustão/absorção de topo contra o trade
    """
    regime = snap.get("regime", "indefinido")
    fz = snap.get("fuzzy") or {}
    vw = snap.get("vwap") or {}
    close = snap["close"]
    o = snap.get("open")
    m15, m5, m1 = fz.get("M15"), fz.get("M5"), fz.get("M1")
    if not m15 or not m5 or not m1:
        return _decisao("nao_entrou", None, regime, 0, [], "sem fuzzy MTF (M15/M5/M1)",
                        estrategia=estrategia, variante=VARIANTE_B)

    mare = _lado_fuzzy(m15.get("score"), mare_min)
    if mare == 0:
        return _decisao("nao_entrou", None, regime, 0, [], "sem maré (M15 neutro)",
                        estrategia=estrategia, variante=VARIANTE_B)
    direcao = "compra" if mare > 0 else "venda"
    lado = 1 if direcao == "compra" else -1

    # Força pelo desvio-padrão MANUAL dos 20 closes do TF de timing (janela do snapshot).
    closes = (snap.get("m5_janela") or {}).get("close") or []
    sigma = indicadores.desvio_padrao(closes, 20)
    corpo = abs(close - o) if o is not None else 0.0
    forca_ok = bool(sigma and sigma > 0 and corpo >= std_k * sigma)

    cenario = classificar_cenario_fuzzy(snap, direcao, forca_ok)

    corrente = _lado_fuzzy(m5.get("score"), corrente_min)
    timing = _lado_fuzzy(m1.get("score"), timing_min)
    vwap = vw.get("vwap")
    c1 = mare == lado
    c2 = corrente == lado
    c3 = (timing == lado) or bool(m1.get("transicao"))
    c4 = (vwap is not None) and ((close <= vwap) if direcao == "compra" else (close >= vwap))
    c5 = forca_ok
    c6 = not (m1.get("exaustao") or m5.get("exaustao") or cenario == "ABSORCAO_TOPO")
    itens = [("mare", c1), ("correnteza", c2), ("timing", c3), ("valor_vwap", c4),
             ("forca_std", c5), ("fluxo_limpo", c6)]
    conf = [k for k, v in itens if v]
    if cenario:
        conf.append(f"cenario_{cenario}")
    score = sum(1 for _, v in itens if v)   # itens satisfeitos (0–6)

    # Cenários de BLOQUEIO (logados) — não entra.
    if cenario in CENARIOS_BLOQUEIO:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"cenário de bloqueio ({cenario})",
                        estrategia=estrategia, variante=VARIANTE_B)
    if cenario not in CENARIOS_ENTRADA:
        return _decisao("nao_entrou", direcao, regime, score, conf, "sem cenário fuzzy claro",
                        estrategia=estrategia, variante=VARIANTE_B)

    # Gates duros (sessão + spread) — iguais às demais.
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _decisao("nao_entrou", direcao, regime, score, conf, f"fora da sessão ({hora}h UTC)",
                        estrategia=estrategia, variante=VARIANTE_B)
    spread = snap.get("spread_pips", 0.0)
    if spread > spread_max_pips:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"spread alto ({spread:.1f}p > {spread_max_pips}p)",
                        estrategia=estrategia, variante=VARIANTE_B)
    if score < checklist_min:
        return _decisao("nao_entrou", direcao, regime, score, conf,
                        f"checklist {score}/6 < {checklist_min}",
                        estrategia=estrategia, variante=VARIANTE_B)

    return _decisao("entrou", direcao, regime, score, conf, f"{cenario}|" + "+".join(conf),
                    estrategia=estrategia, variante=VARIANTE_B)


# --------------------------------------------------------------------------- #
# VARIANTE C — Híbrida (ETAPA 6). As 9 estratégias da Variante A + 7 integrações fuzzy.
#
# PRINCÍPIO GOVERNANTE (aditivo): NÃO altera a lógica interna de nenhuma estratégia. É uma CÓPIA
# PARALELA que recebe a decisão-base já pronta da Variante A (`dec_base`) e aplica, como LEITURA
# dos `fuzzy_scores`/VWAP (nada recalculado aqui), 7 integrações fuzzy:
#   ENTRADA (aqui, marcam variante=C_HIBRIDA):
#     (1) VETO de absorção contra no extremo da VWAP (esforço sem resultado no topo/fundo);
#     (2) M15 fuzzy: VETA se claramente contra a direção (maré adversa); soma confluência se a favor;
#     (3) virada de score na ZONA (OB/S-R/pivot): transição de causa a favor confirma o giro;
#     (4) sweep validado por ESFORÇO: o M5 fuzzy confirma a energia do stop-hunt + reversão;
#     (7) filtro de LOCALIZAÇÃO vs VWAP: comprar aquém / vender além da VWAP = melhor EV.
#   SAÍDA (funções puras prontas p/ o executor plugar quando ligar a gestão por variante — a
#   sombra hoje cataloga a saída pelo gestor genérico, igual à Variante B):
#     (5) saída antecipada por score M5 contra;  (6) exaustão aperta o stop.
#
# Vetos = SÓ as contradições claras (skill: não engessar — muitos gates em AND secam as entradas);
# o resto entra como confluência (ajuste de score). C só produz decisão quando a base ENTROU (há
# setup) — assim o livro C é o subconjunto fuzzy-filtrado do A, diretamente comparável (A vs C).
# --------------------------------------------------------------------------- #
def _fuzzy_contra(fz_tf: dict, direcao: str, minimo: float) -> bool:
    """True se o fuzzy do TF está claramente CONTRA a direção (lado oposto além do limiar)."""
    lado = _lado_fuzzy((fz_tf or {}).get("score"), minimo)
    return (direcao == "compra" and lado < 0) or (direcao == "venda" and lado > 0)


def avaliar_hibrida(dec_base: dict, snap: dict, *, mare_min, corrente_min):
    """Camada fuzzy da Variante C sobre a decisão de UMA estratégia da Variante A.

    Recebe a decisão-base (dec_base) — a lógica da estratégia NÃO é tocada — e devolve uma decisão
    marcada variante=C_HIBRIDA, ou None se a base não entrou (sem setup para filtrar). Ver o cabeçalho
    da seção para as 7 integrações. Vetos só nas contradições claras; demais integrações somam score.
    """
    if dec_base.get("resultado") != "entrou":
        return None
    direcao = dec_base["direcao"]
    estrategia = dec_base["estrategia"]
    regime = dec_base["regime"]
    conf = list(dec_base["confluencias"])
    base_score = dec_base.get("score") or 0
    bonus = 0
    fz = snap.get("fuzzy") or {}
    vw = snap.get("vwap") or {}
    close = snap.get("close")
    m15, m5, m1 = fz.get("M15") or {}, fz.get("M5") or {}, fz.get("M1") or {}

    def _C(resultado, motivo, score):
        return _decisao(resultado, direcao, regime, score, conf, motivo,
                        estrategia=estrategia, variante=VARIANTE_C)

    # (1) VETO de absorção contra no extremo da VWAP (esforço sem resultado no topo/fundo).
    vwap, sup1, inf1 = vw.get("vwap"), vw.get("sup1"), vw.get("inf1")
    absorve = m1.get("absorcao") or m5.get("absorcao")
    if absorve and vwap is not None and close is not None:
        if direcao == "compra" and sup1 is not None and close >= sup1:
            return _C("nao_entrou", "fuzzy veto: absorção de topo", base_score)
        if direcao == "venda" and inf1 is not None and close <= inf1:
            return _C("nao_entrou", "fuzzy veto: absorção de fundo", base_score)

    # VETO de exaustão (clímax) no timing — não perseguir um impulso que vai virar.
    if m1.get("exaustao") or m5.get("exaustao"):
        return _C("nao_entrou", "fuzzy veto: exaustão (clímax)", base_score)

    # (2) M15 fuzzy: veta se claramente contra (maré adversa); soma se a favor.
    if _fuzzy_contra(m15, direcao, mare_min):
        return _C("nao_entrou", "fuzzy veto: M15 contra a direção", base_score)
    lado_m15 = _lado_fuzzy(m15.get("score"), mare_min)
    if (direcao == "compra" and lado_m15 > 0) or (direcao == "venda" and lado_m15 < 0):
        conf.append("fuzzy_m15")
        bonus += 1

    # (3) virada de score na ZONA (OB/S-R/pivot): transição de causa a favor confirma o giro no nível.
    if estrategia in ESTRATEGIAS_ZONA and (m5.get("transicao") or m1.get("transicao")):
        conf.append("fuzzy_virada")
        bonus += 1

    # (4) sweep validado por ESFORÇO: o M5 fuzzy confirma a energia do stop-hunt + reversão.
    if estrategia == ESTRATEGIA_SWEEP:
        lado_m5 = _lado_fuzzy(m5.get("score"), corrente_min)
        if (direcao == "compra" and lado_m5 > 0) or (direcao == "venda" and lado_m5 < 0):
            conf.append("fuzzy_esforco")
            bonus += 1

    # (7) LOCALIZAÇÃO vs VWAP: comprar aquém / vender além da VWAP tem melhor EV (espaço de valor).
    if vwap is not None and close is not None:
        if (direcao == "compra" and close <= vwap) or (direcao == "venda" and close >= vwap):
            conf.append("fuzzy_vwap_valor")
            bonus += 1

    return _C("entrou", "C|" + "+".join(conf), base_score + bonus)


def saida_antecipada_hibrida(direcao: str, fuzzy_m5_score, *, minimo) -> bool:
    """Integração 5 (saída da Variante C): fecha ANTECIPADO se o M5 fuzzy vira CLARAMENTE contra a
    posição (score do lado oposto além de `minimo`). PURA — pronta p/ o executor plugar quando ligar
    a gestão por variante (a sombra hoje cataloga a saída pelo gestor genérico)."""
    lado = _lado_fuzzy(fuzzy_m5_score, minimo)
    return (direcao == "compra" and lado < 0) or (direcao == "venda" and lado > 0)


def ajuste_stop_exaustao(sl_atual: float, direcao: str, close: float, exaustao: bool,
                         *, aperto=0.5) -> float:
    """Integração 6 (saída da Variante C): sob EXAUSTÃO (clímax), APERTA o stop na direção do trade
    (reduz a distância ao preço pela fração `aperto`). PURA e conservadora — NUNCA afrouxa o stop
    (só o aproxima do preço). Pronta p/ o executor plugar na gestão por variante."""
    if not exaustao or close is None or sl_atual is None:
        return sl_atual
    if direcao == "compra":
        return max(sl_atual, close - (close - sl_atual) * aperto)
    return min(sl_atual, close + (sl_atual - close) * aperto)


def gestao_saida_variante(variante: str, direcao: str, preco: float, sl: float, *,
                          fuzzy_m5=None, exausto: bool = False, vwap=None, sma50=None,
                          m5_min: float, aperto: float,
                          idade_candles=None, min_candles: float = 0) -> dict:
    """Gestão de saída ESPECÍFICA por variante do laboratório — compõe as funções puras já testadas
    de B e C. A Variante A (controle) NUNCA passa por aqui (segue no gestor genérico). PURA/testável.
    Devolve {novo_sl, fechar, motivo}:
      - C_HIBRIDA: integração 5 (fecha se o M5 fuzzy vira CLARAMENTE contra) + integração 6 (aperta
        o stop sob EXAUSTÃO no TF do trade — só aproxima, nunca afrouxa);
      - B_FUZZY_PURO: saída técnica (preço cruzou a VWAP/SMA50 p/ o lado oposto = perdeu a referência).

    CARÊNCIA: a saída ANTECIPADA (C) / técnica (B) só pode FECHAR depois que a posição viveu ao menos
    `min_candles` velas do seu TF (`idade_candles`). Sem isso o M5 fuzzy fechava a ordem no MESMO ciclo
    da abertura ("não deixou andar") — o fuzzy no instante da entrada é a foto da entrada, não uma
    mudança de contexto. `idade_candles=None` (uso legado/teste puro) desliga a trava de tempo. O aperto
    de stop na exaustão (só aproxima) NÃO é travado — vale desde o início por ser conservador.
    """
    cedo = idade_candles is not None and idade_candles < min_candles
    if variante == "C_HIBRIDA":
        if not cedo and saida_antecipada_hibrida(direcao, fuzzy_m5, minimo=m5_min):
            return {"novo_sl": sl, "fechar": True, "motivo": "saída antecipada C (M5 fuzzy contra)"}
        return {"novo_sl": ajuste_stop_exaustao(sl, direcao, preco, exausto, aperto=aperto),
                "fechar": False, "motivo": ""}
    if variante == "B_FUZZY_PURO":
        if not cedo and saida_tecnica_fuzzy_puro(direcao, preco, sma50=sma50, vwap=vwap):
            return {"novo_sl": sl, "fechar": True, "motivo": "saída técnica B (VWAP/SMA50 oposta)"}
    return {"novo_sl": sl, "fechar": False, "motivo": ""}


# =========================================================================== #
# FAMÍLIA D_LINHAS — estratégias pela DINÂMICA das curvas de score fuzzy por TF.
#
# As 9 estratégias (Variante A) e a B/C leem o score como NÍVEL estático no instante da decisão.
# Esta família lê o MOVIMENTO das linhas: divergência linha×preço (Lei 2 de Wyckoff), pullback do
# leque (rápida recua e reengata na maré), convergência (Sync alinhando) e exaustão (clímax). Cada
# uma é um LIVRO de sombra próprio (variante=D_LINHAS) — um 4º cenário comparável no /relatorio.
# Funções PURAS/testáveis; sem look-ahead (swings só usam candles já fechados/confirmados).
#
# Snapshot esperado (montado em decisao.montar_snapshot):
#   snap["serie_op"] = {"high":[...], "low":[...], "close":[...], "score":[...]}  # TF de operação,
#       alinhado candle-a-candle (JOIN candles×fuzzy_scores), cronológico.
#   snap["score_acima"] = [scores do TF ACIMA]  (cronológico)
#   snap["sync_ult"]    = [estados de sync ... penúltimo, último]
# =========================================================================== #
def _no_valor_topo(close, vw):
    """True se o preço está no TOPO de valor (>= VWAP ou banda superior) — zona p/ procurar VENDA."""
    vwap, sup1 = vw.get("vwap"), vw.get("sup1")
    if sup1 is not None:
        return close >= sup1
    return vwap is not None and close >= vwap


def _no_valor_fundo(close, vw):
    """True se o preço está no FUNDO de valor (<= VWAP ou banda inferior) — zona p/ procurar COMPRA."""
    vwap, inf1 = vw.get("vwap"), vw.get("inf1")
    if inf1 is not None:
        return close <= inf1
    return vwap is not None and close <= vwap


def avaliar_divergencia_fuzzy(snap: dict, *, sessao_utc, spread_max_pips, n_swing: int) -> dict:
    """A — DIVERGÊNCIA esforço×resultado (Lei 2 de Wyckoff). Preço faz topo mais alto mas a linha de
    score faz topo MAIS BAIXO (esforço decrescente sustentando o preço = distribuição) → VENDA no
    topo de valor. Espelho p/ COMPRA (fundo mais baixo no preço + score subindo, no fundo de valor).
    Reversão de qualidade: invalidação estrutural apertada (atrás do topo/fundo), alvo na média."""
    regime = snap.get("regime", "indefinido")
    s = snap.get("serie_op") or {}
    highs, lows, scores = s.get("high") or [], s.get("low") or [], s.get("score") or []
    vw = snap.get("vwap") or {}
    close = snap["close"]
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_DIVERGENCIA, variante=VARIANTE_LINHAS)
    if len(highs) < n_swing * 2 + 3 or len(scores) != len(highs):
        return _d("nao_entrou", None, regime, 0, [], "sem série preço×score alinhada")
    sw = indicadores.swings(highs, lows, n_swing)
    tops = [w for w in sw if w["tipo"] == "high"]
    bots = [w for w in sw if w["tipo"] == "low"]
    # FRESCOR: só entra no candle em que o swing novo acabou de ser CONFIRMADO (i == len-1-n).
    # Sem isso, a mesma divergência gerava "entrou" TODO candle enquanto durasse (re-entrada
    # serial → N inflado com trades correlacionados, amostra menos independente que o N sugere).
    i_fresco = len(highs) - 1 - n_swing
    direcao, conf = None, []
    if len(tops) >= 2:
        t1, t2 = tops[-2], tops[-1]
        if (t2["i"] >= i_fresco and t2["preco"] > t1["preco"]
                and scores[t2["i"]] < scores[t1["i"]] and _no_valor_topo(close, vw)):
            direcao, conf = "venda", ["div_baixa", "topo_preco_sobe_score_cai", "topo_vwap"]
    if direcao is None and len(bots) >= 2:
        b1, b2 = bots[-2], bots[-1]
        if (b2["i"] >= i_fresco and b2["preco"] < b1["preco"]
                and scores[b2["i"]] > scores[b1["i"]] and _no_valor_fundo(close, vw)):
            direcao, conf = "compra", ["div_alta", "fundo_preco_cai_score_sobe", "fundo_vwap"]
    if direcao is None:
        return _d("nao_entrou", None, regime, 0, [], "sem divergência linha×preço na banda")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, len(conf), conf, f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, len(conf), conf, "spread alto")
    return _d("entrou", direcao, regime, len(conf), conf, "divergência|" + "+".join(conf))


def avaliar_pullback_leque(snap: dict, *, sessao_utc, spread_max_pips, mare_min, dip_janela: int) -> dict:
    """B — PULLBACK DO LEQUE. Na maré comprada (M15 ≥ mare_min), a linha RÁPIDA (TF de operação)
    recuou ABAIXO da lenta (TF acima) nas últimas velas e agora REENGATA (cruza de volta acima) →
    COMPRA no valor (preço na/abaixo da VWAP). Espelho p/ VENDA (maré vendida, rápida pop acima e
    reengata abaixo). Continuação a favor da tendência, pegando o fim do pullback."""
    regime = snap.get("regime", "indefinido")
    fz = snap.get("fuzzy") or {}
    s = snap.get("serie_op") or {}
    rapida = s.get("score") or []
    lenta = snap.get("score_acima") or []
    vw = snap.get("vwap") or {}
    close, vwap = snap["close"], (snap.get("vwap") or {}).get("vwap")
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_PULLBACK_LEQUE, variante=VARIANTE_LINHAS)
    mare = _lado_fuzzy((fz.get("M15") or {}).get("score"), mare_min)
    if mare == 0:
        return _d("nao_entrou", None, regime, 0, [], "sem maré (M15 neutro)")
    direcao = "compra" if mare > 0 else "venda"
    k = min(len(rapida), len(lenta))
    if k < 3:
        return _d("nao_entrou", direcao, regime, 0, [], "sem série de linhas (rápida×lenta)")
    r, l = rapida[-k:], lenta[-k:]
    j0 = max(0, k - 1 - dip_janela)
    # O TF acima pode não ter score fechado em todos os instantes (asof devolve None no início
    # da janela) — sem par completo rápida×lenta não há leitura de leque.
    if any(v is None for v in r[j0:]) or any(v is None for v in l[j0:]):
        return _d("nao_entrou", direcao, regime, 0, [], "sem série de linhas (rápida×lenta)")
    if direcao == "compra":
        recuou = any(r[j] < l[j] for j in range(j0, k - 1))     # rápida esteve abaixo da lenta
        reengatou = r[-2] <= l[-2] and r[-1] > l[-1]            # e cruzou de volta p/ cima agora
        valor = vwap is None or close <= vwap                   # desconto
    else:
        recuou = any(r[j] > l[j] for j in range(j0, k - 1))
        reengatou = r[-2] >= l[-2] and r[-1] < l[-1]
        valor = vwap is None or close >= vwap                   # prêmio
    if not (recuou and reengatou):
        return _d("nao_entrou", direcao, regime, 0, [], "sem recuo+reengate do leque")
    if not valor:
        return _d("nao_entrou", direcao, regime, 1, ["leque_reengate"], "fora do valor da VWAP")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = ["mare_M15", "leque_recuo", "leque_reengate", "valor_vwap"]
    return _d("entrou", direcao, regime, len(conf), conf, "pullback_leque|" + "+".join(conf))


def avaliar_sync_flip(snap: dict, *, sessao_utc, spread_max_pips, mare_min) -> dict:
    """C — CONVERGÊNCIA (Sync flip). As linhas SAEM de divergência (amarelo) e ALINHAM: a Sync vira
    verde (compra) ou vermelho (venda) NESTE candle, confirmada pela maré M15 e rompendo a VWAP na
    direção → entra no início do estouro (não depois que já cansou)."""
    regime = snap.get("regime", "indefinido")
    su = snap.get("sync_ult") or []
    fz = snap.get("fuzzy") or {}
    close, vwap = snap["close"], (snap.get("vwap") or {}).get("vwap")
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_SYNC_FLIP, variante=VARIANTE_LINHAS)
    if len(su) < 2:
        return _d("nao_entrou", None, regime, 0, [], "sem histórico de sync")
    prev, atual = su[-2], su[-1]
    if atual not in ("verde", "vermelho") or prev == atual:
        return _d("nao_entrou", None, regime, 0, [], "sem flip de sync p/ alinhamento")
    direcao = "compra" if atual == "verde" else "venda"
    mare = _lado_fuzzy((fz.get("M15") or {}).get("score"), mare_min)
    if (direcao == "compra" and mare < 0) or (direcao == "venda" and mare > 0):
        return _d("nao_entrou", direcao, regime, 0, [], "maré M15 contra o flip")
    rompe = vwap is None or (close > vwap if direcao == "compra" else close < vwap)
    if not rompe:
        return _d("nao_entrou", direcao, regime, 1, ["sync_flip"], "não rompeu a VWAP na direção")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = [f"sync_{prev}->{atual}", "maré_ok", "rompe_vwap"]
    return _d("entrou", direcao, regime, len(conf), conf, "sync_flip|" + "+".join(conf))


def avaliar_exaustao_fuzzy(snap: dict, *, sessao_utc, spread_max_pips, sat_candles: int,
                           sat_alto: float, sat_baixo: float) -> dict:
    """D — EXAUSTÃO (clímax). A linha de score do TF de operação ficou PRESA no extremo (>= sat_alto
    p/ topo, <= sat_baixo p/ fundo) por `sat_candles` velas e agora ROLA (perde o extremo) com o
    preço na banda ±2σ da VWAP → fade do clímax (Lei do esforço esgotado). Reversão curta rumo à
    média; invalidação apertada além do extremo."""
    regime = snap.get("regime", "indefinido")
    s = snap.get("serie_op") or {}
    scores = s.get("score") or []
    vw = snap.get("vwap") or {}
    close, vwap = snap["close"], vw.get("vwap")
    sup2, inf2 = vw.get("sup2"), vw.get("inf2")
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_EXAUSTAO, variante=VARIANTE_LINHAS)
    if len(scores) < sat_candles + 2:
        return _d("nao_entrou", None, regime, 0, [], "série de score curta")
    presos = scores[-(sat_candles + 1):-1]      # as velas ANTES da atual (o platô saturado)
    atual, anterior = scores[-1], scores[-2]
    direcao = None
    # "No extremo" = banda ±2σ DE VERDADE quando ela existe (a tese é fade de clímax na banda);
    # a VWAP só serve de fallback quando a banda não está disponível. O OR antigo deixava
    # QUALQUER preço acima da VWAP passar por "banda 2σ" — o livro media outra hipótese.
    if all(v >= sat_alto for v in presos) and atual < anterior:          # topo saturado rolando
        no_extremo = (close >= sup2) if sup2 is not None else (vwap is not None and close > vwap)
        if no_extremo:
            direcao = "venda"
    if direcao is None and all(v <= sat_baixo for v in presos) and atual > anterior:  # fundo saturado
        no_extremo = (close <= inf2) if inf2 is not None else (vwap is not None and close < vwap)
        if no_extremo:
            direcao = "compra"
    if direcao is None:
        return _d("nao_entrou", None, regime, 0, [], "sem exaustão (saturação+rollover na banda)")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = ["saturado", "rollover", "banda_2sigma"]
    return _d("entrou", direcao, regime, len(conf), conf, "exaustão|" + "+".join(conf))


# =========================================================================== #
# FAMÍLIA E_SENTINELA — FORÇA contínua (micro/macro) + LEQUE (5º cenário).
#
# Inspirada no "Sentinel_Sync_Line" do criador do PDF: em vez do score como nível estático (A/B/C) ou
# do movimento das linhas de score (D), lê a FORÇA CONTÍNUA (fuzzy_score.forca_sync: micro=M1/M5,
# macro=M15/H1) e o LEQUE (amplitude entre as linhas). Cada uma é um livro de sombra próprio
# (variante=E_SENTINELA), comparável no /relatorio. Snapshot: snap["forca"] (dict atual) e
# snap["forca_serie"] (histórico curto, cronológico). Funções PURAS/testáveis, sem look-ahead.
# =========================================================================== #
def avaliar_sentinela_forca(snap: dict, *, sessao_utc, spread_max_pips, forca_min) -> dict:
    """E1 — FORÇA ALINHADA (o 'gain verde' do Sentinela). Micro E macro no mesmo lado (estado verde/
    vermelho) e a LINHA de força CRUZA o limiar neste candle, rompendo a VWAP na direção → estouro."""
    regime = snap.get("regime", "indefinido")
    serie = snap.get("forca_serie") or []
    vw = snap.get("vwap") or {}
    vwap, close = vw.get("vwap"), snap["close"]
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_SENT_FORCA, variante=VARIANTE_SENTINELA)
    if len(serie) < 2:
        return _d("nao_entrou", None, regime, 0, [], "sem série de força")
    ant, at = serie[-2], serie[-1]
    direcao = None
    if at.get("estado") == "verde" and ant.get("forca", 50) < forca_min <= at.get("forca", 0):
        direcao = "compra"
    elif at.get("estado") == "vermelho" and ant.get("forca", 50) > (100 - forca_min) >= at.get("forca", 100):
        direcao = "venda"
    if direcao is None:
        return _d("nao_entrou", None, regime, 0, [], "sem cruzamento de força alinhada")
    if vwap is not None and ((close <= vwap) if direcao == "compra" else (close >= vwap)):
        return _d("nao_entrou", direcao, regime, 1, ["forca_alinhada"], "não rompeu a VWAP na direção")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = [f"forca_{at['estado']}", f"micro{at['micro']}", f"macro{at['macro']}", "rompe_vwap"]
    return _d("entrou", direcao, regime, len(conf), conf, "sentinela_forca|" + "+".join(conf))


def avaliar_sentinela_divergencia(snap: dict, *, sessao_utc, spread_max_pips) -> dict:
    """E2 — DIVERGÊNCIA micro×macro (o 'ATENÇÃO' do Sentinela). O micro (rápido) puxa CONTRA a maré
    macro; no extremo da banda VWAP, fade a favor do MACRO (a maré manda; o micro é repuxo)."""
    regime = snap.get("regime", "indefinido")
    f = snap.get("forca") or {}
    vw = snap.get("vwap") or {}
    close = snap["close"]
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_SENT_DIVERG, variante=VARIANTE_SENTINELA)
    if not f or not f.get("divergencia"):
        return _d("nao_entrou", None, regime, 0, [], "sem divergência micro×macro")
    macro = f.get("macro", 0.0)
    if macro == 0:
        return _d("nao_entrou", None, regime, 0, [], "macro neutro")
    direcao = "venda" if macro < 0 else "compra"   # segue a maré macro
    sup1, inf1 = vw.get("sup1"), vw.get("inf1")
    no_extremo = ((direcao == "venda" and (sup1 is None or close >= sup1)) or
                  (direcao == "compra" and (inf1 is None or close <= inf1)))
    if not no_extremo:
        return _d("nao_entrou", direcao, regime, 1, ["divergencia"], "fora do extremo da banda")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = ["divergencia_micro_macro", "segue_macro", "banda_extremo"]
    return _d("entrou", direcao, regime, len(conf), conf, "sentinela_diverg|" + "+".join(conf))


def avaliar_sentinela_leque(snap: dict, *, sessao_utc, spread_max_pips, estreito, largo) -> dict:
    """E3 — COMPRESSÃO→EXPANSÃO do LEQUE (mola). O leque esteve COMPRIMIDO (≤ estreito) e AGORA
    EXPANDE (≥ largo e crescendo) com a força alinhada → estouro na direção da força, rompendo a VWAP."""
    regime = snap.get("regime", "indefinido")
    serie = snap.get("forca_serie") or []
    f = snap.get("forca") or {}
    vw = snap.get("vwap") or {}
    vwap, close = vw.get("vwap"), snap["close"]
    _d = lambda *a: _decisao(*a, estrategia=ESTRATEGIA_SENT_LEQUE, variante=VARIANTE_SENTINELA)
    if len(serie) < 3:
        return _d("nao_entrou", None, regime, 0, [], "sem série de leque")
    leques = [s.get("leque", 0.0) for s in serie]
    comprimiu = min(leques[-4:-1]) <= estreito       # esteve comprimido nas velas anteriores
    expandiu = leques[-1] >= largo and leques[-1] > leques[-2]
    estado = f.get("estado")
    if not (comprimiu and expandiu and estado in ("verde", "vermelho")):
        return _d("nao_entrou", None, regime, 0, [], "sem compressão→expansão do leque")
    direcao = "compra" if estado == "verde" else "venda"
    if vwap is not None and ((close <= vwap) if direcao == "compra" else (close >= vwap)):
        return _d("nao_entrou", direcao, regime, 1, ["leque_expandiu"], "não rompeu a VWAP na direção")
    hora = snap.get("hora_utc", 0)
    if not (sessao_utc[0] <= hora < sessao_utc[1]):
        return _d("nao_entrou", direcao, regime, 0, [], f"fora da sessão ({hora}h UTC)")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", direcao, regime, 0, [], "spread alto")
    conf = ["leque_comprimiu", "leque_expandiu", f"forca_{estado}", "rompe_vwap"]
    return _d("entrou", direcao, regime, len(conf), conf, "sentinela_leque|" + "+".join(conf))


# =========================================================================== #
# FAMÍLIA F_BREAKOUT — rompimento da faixa de abertura de Londres (6º cenário).
#
# Achado do estudo histórico (validado FORA DA AMOSTRA em H1+M15): o movimento grande do forex nasce
# na abertura de Londres. Não prevê direção (o rompimento dá) e deixa correr. A ENTRADA é a mesma para
# os dois livros; eles diferem só na SAÍDA (o executor aplica a proteção só ao `_prot_v1`). O estado de
# sessão (faixa de abertura + "primeiro rompimento do dia?") é calculado no snapshot (decisao._or_londres).
# =========================================================================== #
def avaliar_breakout_londres(snap: dict, *, estrategia, spread_max_pips) -> dict:
    """Entra no PRIMEIRO fechamento que rompe a faixa de abertura de Londres, na direção do rompimento.
    `snap['or_londres']` = {entrar, direcao, sl_pips, or_high, or_low} vindo do motor (sem look-ahead)."""
    regime = snap.get("regime", "indefinido")
    orl = snap.get("or_londres") or {}
    _d = lambda *a: _decisao(*a, estrategia=estrategia, variante=VARIANTE_BREAKOUT)
    if not orl or not orl.get("entrar"):
        return _d("nao_entrou", (orl or {}).get("direcao"), regime, 0, [], "sem rompimento da OR de Londres")
    if snap.get("spread_pips", 0.0) > spread_max_pips:
        return _d("nao_entrou", orl["direcao"], regime, 0, [], "spread alto")
    conf = ["rompeu_OR_londres", f"OR{orl['sl_pips']}p"]
    dec = _d("entrou", orl["direcao"], regime, len(conf), conf,
             f"rompimento OR Londres ({orl['direcao']})")
    dec["sl_pips"] = orl["sl_pips"]        # o executor usa a OR oposta como stop (não o ATR)
    return dec


def gerir_breakout(estrategia, direcao, preco, entrada, sl, *, hora, fim_hora, mfe_pips,
                   trig_pips, lock_pips, pip, prot_estrategia) -> dict:
    """Gestão do F_BREAKOUT (o stop da OR é emulado pelo stop de emergência do executor; aqui só a
    SAÍDA DE SESSÃO + a PROTEÇÃO). Devolve {novo_sl, fechar, motivo}. PURA/testável.
      - fecha no FIM da janela de Londres (`hora >= fim_hora`);
      - só o livro `_prot_v1`: quando o MFE atinge `trig_pips` (+100 pipetes), sobe o stop p/ entrada
        + `lock_pips` (+2p) — só APROXIMA na direção do lucro, nunca afrouxa."""
    if hora >= fim_hora:
        return {"novo_sl": sl, "fechar": True, "motivo": "fim da janela de Londres"}
    if estrategia == prot_estrategia and mfe_pips >= trig_pips:
        alvo = entrada + lock_pips * pip if direcao == "compra" else entrada - lock_pips * pip
        novo = max(sl, alvo) if direcao == "compra" else min(sl, alvo)   # só aperta p/ o lucro
        return {"novo_sl": novo, "fechar": False, "motivo": ""}
    return {"novo_sl": sl, "fechar": False, "motivo": ""}
