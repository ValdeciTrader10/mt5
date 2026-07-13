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
# Médias móveis — SMA e EMA (funções PURAS). Base da estratégia `pullback_medias`
# (Variante A) e do painel de médias. Retornam o VALOR ATUAL (último da série).
# --------------------------------------------------------------------------- #
def sma(valores: list, periodo: int):
    """Média móvel simples do último bloco de `periodo` valores. None se faltam dados."""
    if periodo <= 0 or len(valores) < periodo:
        return None
    return fmean(valores[-periodo:])


def ema(valores: list, periodo: int):
    """Média móvel exponencial (semente = SMA dos `periodo` primeiros). None se faltam dados."""
    if periodo <= 0 or len(valores) < periodo:
        return None
    k = 2.0 / (periodo + 1)
    e = fmean(valores[:periodo])
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e


def medias(closes: list) -> dict:
    """Conjunto de médias do doc (EMA 9/20/45 + SMA 50/200) a partir dos closes.

    Cada chave vem None quando não há candles suficientes para aquele período. Usadas como
    LEITURA (toque de média em tendência, filtro de localização) — nunca como número mágico.
    """
    return {
        "ema9": ema(closes, 9),
        "ema20": ema(closes, 20),
        "ema45": ema(closes, 45),
        "sma50": sma(closes, 50),
        "sma200": sma(closes, 200),
    }


# --------------------------------------------------------------------------- #
# Pivots clássicos (PP/R1-3/S1-3) do período FECHADO anterior — função PURA.
# --------------------------------------------------------------------------- #
def pivots_classicos(high: float, low: float, close: float) -> dict:
    """Pivots clássicos a partir do H/L/C do período FECHADO anterior (fórmula padrão).

    PP = (H+L+C)/3 é a referência; R1-3/S1-3 são as projeções. São níveis de liquidez que o
    mercado respeita muito no intraday (confluência com S/R/OB — estratégia `pivot_confluencia`).
    """
    pp = (high + low + close) / 3.0
    amp = high - low
    return {
        "pp": pp,
        "r1": 2 * pp - low,
        "s1": 2 * pp - high,
        "r2": pp + amp,
        "s2": pp - amp,
        "r3": high + 2 * (pp - low),
        "s3": low - 2 * (high - pp),
    }


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


def qualidade_sr(preco, tipo, highs, lows, closes, tol) -> dict:
    """Mede o quanto o preço RESPEITA um nível — a prova de que é S/R de verdade.

    tipo: 'suporte' | 'resistencia'. `tol` = meia-banda (em preço) do nível.
      - toque: candle cujo range [low, high] entra na banda [preco−tol, preco+tol].
      - rejeição: toque em que o preço furou o nível mas FECHOU de volta do lado certo
        (resistência: pavio acima e close abaixo; suporte: pavio abaixo e close acima).
        Rejeição é o sinal forte — mostra o mercado defendendo o nível.
    Retorna {toques, rejeicoes, respeito (=rejeicoes/toques), ult_idx (recência)}.
    """
    lo, hi = preco - tol, preco + tol
    toques = rejeicoes = 0
    ult_idx = -1
    for i in range(len(closes)):
        h, l, c = highs[i], lows[i], closes[i]
        if l <= hi and h >= lo:                      # o candle tocou a banda do nível
            toques += 1
            ult_idx = i
            if tipo == "resistencia" and h >= lo and c < lo:
                rejeicoes += 1
            elif tipo == "suporte" and l <= hi and c > hi:
                rejeicoes += 1
    respeito = round(rejeicoes / toques, 2) if toques else 0.0
    return {"toques": toques, "rejeicoes": rejeicoes, "respeito": respeito, "ult_idx": ult_idx}


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
# Order Block — a última vela contrária antes de um impulso com displacement (FVG)
# --------------------------------------------------------------------------- #
def order_blocks(opens, highs, lows, closes, atr_val: float, min_atr: float) -> list:
    """Order blocks FRESCOS (não mitigados). Um OB válido não é candle qualquer: exige
    impulso com displacement (deixa FVG) — doc/skill §4.

    - Bull OB: a última vela de BAIXA (close<open) antes de um FVG bull. Zona de demanda
      [low, high] dessa vela — alvo de pullback para COMPRA.
    - Bear OB: a última vela de ALTA antes de um FVG bear. Zona de oferta, para VENDA.
    Fresco = o preço NÃO reentrou na zona depois do impulso (mesma régua dos FVGs). Assim
    a estratégia dispara quando o preço RETESTA a zona ainda intacta.
    """
    if not atr_val:
        return []
    minimo = min_atr * atr_val
    achados = []
    n = len(highs)
    for i in range(2, n):
        # Bull FVG em i (imbalance de alta) → procura a vela-OB de baixa que o originou.
        if lows[i] > highs[i - 2] and (lows[i] - highs[i - 2]) >= minimo:
            j = i - 2
            while j >= 0 and closes[j] >= opens[j]:      # pula velas de alta do impulso
                j -= 1
            if j < 0:
                continue
            base, topo = lows[j], highs[j]
            if min(lows[i + 1:], default=topo + 1) > topo:   # não reentrou na zona
                achados.append({"tipo": "ob_bull", "base": base, "topo": topo, "i": j})
        # Bear FVG em i (imbalance de baixa) → vela-OB de alta.
        elif highs[i] < lows[i - 2] and (lows[i - 2] - highs[i]) >= minimo:
            j = i - 2
            while j >= 0 and closes[j] <= opens[j]:      # pula velas de baixa do impulso
                j -= 1
            if j < 0:
                continue
            base, topo = lows[j], highs[j]
            if max(highs[i + 1:], default=base - 1) < base:  # não reentrou na zona
                achados.append({"tipo": "ob_bear", "base": base, "topo": topo, "i": j})
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
