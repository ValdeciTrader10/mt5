"""Indicadores e leitura de estrutura de mercado (Fase 2) — funções PURAS.

Nada aqui toca no MT5 nem no banco: entram listas de preços, saem números/estruturas.
Isso mantém a matemática testável isoladamente (DEBUG desde a v1 → também testável
desde a v1). O motor (`analise.py`) é quem lê candles do banco e persiste o resultado.

Cobre:
- ATR (Wilder) e ADX/±DI (Wilder) — volatilidade e força de tendência (regime).
- Swings fractais (pivôs) e rótulos SMC (HH/HL/LH/LL).
- Eventos de estrutura: BOS (continuação) e CHOCH (reversão).
- Suporte/resistência por clusterização de swings (força = nº de toques).
- FVG (Fair Value Gap, imbalance de 3 velas) não mitigados.
- Gaps de sessão (abertura vs. fechamento anterior).
"""

from statistics import fmean

# --------------------------------------------------------------------------- #
# Suavização de Wilder (RMA) — base do ATR e do ADX
# --------------------------------------------------------------------------- #
def wilder(valores: list, periodo: int) -> list:
    """Média móvel de Wilder (RMA). Retorna a série suavizada (len = n-periodo+1)."""
    if periodo <= 0 or len(valores) < periodo:
        return []
    media = fmean(valores[:periodo])
    saida = [media]
    for v in valores[periodo:]:
        media = (media * (periodo - 1) + v) / periodo
        saida.append(media)
    return saida


def _true_ranges(highs, lows, closes) -> list:
    """True Range de cada candle a partir do 2º (alinhado ao índice i=1..n-1)."""
    trs = []
    for i in range(1, len(closes)):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return trs


def atr(highs, lows, closes, periodo: int = 14):
    """ATR atual (Wilder). None se não há candles suficientes."""
    trs = _true_ranges(highs, lows, closes)
    serie = wilder(trs, periodo)
    return serie[-1] if serie else None


def adx(highs, lows, closes, periodo: int = 14):
    """Retorna (adx, plus_di, minus_di) atuais — força e direção da tendência.

    None se faltam candles. ADX alto = tendência; baixo = lateral (ver config).
    """
    n = len(closes)
    if n < periodo * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    atr_s = wilder(trs, periodo)
    plus_s = wilder(plus_dm, periodo)
    minus_s = wilder(minus_dm, periodo)
    if not atr_s:
        return None
    dxs, pdi_ult, mdi_ult = [], 0.0, 0.0
    for a, p, m in zip(atr_s, plus_s, minus_s):
        if a == 0:
            dxs.append(0.0)
            continue
        pdi = 100 * p / a
        mdi = 100 * m / a
        soma = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / soma if soma else 0.0)
        pdi_ult, mdi_ult = pdi, mdi
    adx_s = wilder(dxs, periodo)
    if not adx_s:
        return None
    return (adx_s[-1], pdi_ult, mdi_ult)


# --------------------------------------------------------------------------- #
# Swings fractais + rótulos SMC
# --------------------------------------------------------------------------- #
def swings(highs, lows, n: int) -> list:
    """Pivôs fractais: high/low que superam estritamente os `n` candles de cada lado.

    Retorna lista ordenada por índice: {"i", "tipo": high|low, "preco"}.
    """
    res = []
    for i in range(n, len(highs) - n):
        viz_h = highs[i - n:i] + highs[i + 1:i + n + 1]
        if viz_h and highs[i] > max(viz_h):
            res.append({"i": i, "tipo": "high", "preco": highs[i]})
        viz_l = lows[i - n:i] + lows[i + 1:i + n + 1]
        if viz_l and lows[i] < min(viz_l):
            res.append({"i": i, "tipo": "low", "preco": lows[i]})
    res.sort(key=lambda s: s["i"])
    return res


def rotular_swings(sw: list) -> list:
    """Adiciona o rótulo SMC (HH/HL/LH/LL) comparando cada swing ao anterior do MESMO tipo."""
    ult_high = ult_low = None
    for s in sw:
        if s["tipo"] == "high":
            s["label"] = "HH" if (ult_high is not None and s["preco"] > ult_high) else "LH"
            ult_high = s["preco"]
        else:
            s["label"] = "HL" if (ult_low is not None and s["preco"] > ult_low) else "LL"
            ult_low = s["preco"]
    return sw


def eventos_estrutura(sw_rotulados: list) -> list:
    """Deriva BOS/CHOCH da sequência de swings rotulados.

    Modelo (heurístico, mas fiel ao SMC básico): os rompimentos que importam são
    HH (alta) e LL (baixa). Se o rompimento vai NO SENTIDO do viés vigente é BOS
    (continuação); se INVERTE o viés é CHOCH (mudança de caráter). HL/LH são
    pullbacks e não geram evento por si.
    """
    eventos = []
    vies = None
    for s in sw_rotulados:
        label = s.get("label")
        if label == "HH":
            evento = "CHOCH" if vies == "baixa" else "BOS"
            eventos.append({"i": s["i"], "evento": evento, "direcao": "alta", "preco": s["preco"]})
            vies = "alta"
        elif label == "LL":
            evento = "CHOCH" if vies == "alta" else "BOS"
            eventos.append({"i": s["i"], "evento": evento, "direcao": "baixa", "preco": s["preco"]})
            vies = "baixa"
    return eventos


# --------------------------------------------------------------------------- #
# Suporte / resistência (clusterização de swings)
# --------------------------------------------------------------------------- #
def _clusterizar(precos: list, tolerancia: float, forca_min: int) -> list:
    """Agrupa preços próximos (< tolerância entre vizinhos). Retorna (preco_medio, n)."""
    if not precos:
        return []
    ordenados = sorted(precos)
    grupos, atual = [], [ordenados[0]]
    for p in ordenados[1:]:
        if p - atual[-1] > tolerancia:
            grupos.append(atual)
            atual = []
        atual.append(p)
    grupos.append(atual)
    return [(fmean(g), len(g)) for g in grupos if len(g) >= forca_min]


def niveis_sr(sw: list, atr_val: float, cluster_atr: float, forca_min: int) -> dict:
    """Suportes (de swing lows) e resistências (de swing highs) por clusterização.

    tolerância = cluster_atr * ATR. `forca` de cada nível = nº de toques no cluster.
    """
    if not atr_val:
        return {"suporte": [], "resistencia": []}
    tol = cluster_atr * atr_val
    highs = [s["preco"] for s in sw if s["tipo"] == "high"]
    lows = [s["preco"] for s in sw if s["tipo"] == "low"]
    return {
        "resistencia": _clusterizar(highs, tol, forca_min),
        "suporte": _clusterizar(lows, tol, forca_min),
    }


# --------------------------------------------------------------------------- #
# FVG (Fair Value Gap) — imbalance de 3 velas, não mitigado
# --------------------------------------------------------------------------- #
def fvgs(highs, lows, atr_val: float, min_atr: float) -> list:
    """FVGs relevantes (>= min_atr*ATR) ainda NÃO mitigados por preço posterior.

    - Bull: low[i] > high[i-2]  → zona [high[i-2], low[i]].
    - Bear: high[i] < low[i-2]  → zona [high[i], low[i-2]].
    Mitigado = preço posterior reentrou na zona.
    """
    if not atr_val:
        return []
    minimo = min_atr * atr_val
    achados = []
    n = len(highs)
    for i in range(2, n):
        if lows[i] > highs[i - 2] and (lows[i] - highs[i - 2]) >= minimo:
            base, topo = highs[i - 2], lows[i]
            if min(lows[i + 1:], default=topo) > base:  # não reentrou
                achados.append({"tipo": "fvg_bull", "base": base, "topo": topo, "i": i})
        elif highs[i] < lows[i - 2] and (lows[i - 2] - highs[i]) >= minimo:
            base, topo = highs[i], lows[i - 2]
            if max(highs[i + 1:], default=base) < topo:  # não reentrou
                achados.append({"tipo": "fvg_bear", "base": base, "topo": topo, "i": i})
    return achados


# --------------------------------------------------------------------------- #
# Gaps de sessão (abertura vs. fechamento anterior)
# --------------------------------------------------------------------------- #
def gaps(opens, closes, tamanho_pip: float, min_pips: float, max_pips: float) -> list:
    """Gaps de abertura entre `min_pips` e `max_pips`. Retorna {i, direcao, pips, preco}."""
    if not tamanho_pip:
        return []
    achados = []
    for i in range(1, len(opens)):
        delta = opens[i] - closes[i - 1]
        pips = abs(delta) / tamanho_pip
        if min_pips <= pips <= max_pips:
            achados.append({
                "i": i,
                "direcao": "alta" if delta > 0 else "baixa",
                "pips": round(pips, 1),
                "preco": closes[i - 1],
            })
    return achados


# --------------------------------------------------------------------------- #
# Regime a partir do ADX (config: ADX_TENDENCIA / ADX_LATERAL)
# --------------------------------------------------------------------------- #
def classificar_regime(adx_val, plus_di, minus_di, adx_tend: float, adx_lat: float) -> str:
    """tendencia_alta / tendencia_baixa / lateral / indefinido a partir do ADX/±DI."""
    if adx_val is None:
        return "indefinido"
    if adx_val >= adx_tend:
        return "tendencia_alta" if plus_di >= minus_di else "tendencia_baixa"
    if adx_val <= adx_lat:
        return "lateral"
    return "transicao"
