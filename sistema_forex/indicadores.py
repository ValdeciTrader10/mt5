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


def desvio_padrao(valores: list, periodo: int = 20):
    """Desvio-padrão populacional MANUAL (sem lib) do último bloco de `periodo` valores.

    Base da leitura de "força/energia" da Variante B (Fuzzy Puro): mede a volatilidade recente
    sobre 20 closes para decidir se o candle-gatilho tem lastro. None se faltam dados."""
    if periodo <= 0 or len(valores) < periodo:
        return None
    bloco = valores[-periodo:]
    media = sum(bloco) / periodo
    var = sum((v - media) ** 2 for v in bloco) / periodo
    return var ** 0.5


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
# VWAP diária + bandas de desvio (±1σ/±2σ) — função PURA (ETAPA 3).
# --------------------------------------------------------------------------- #
def vwap_bandas(highs, lows, closes, volumes, k1: float = 1.0, k2: float = 2.0):
    """VWAP acumulada do bloco de candles recebido + bandas ±k·σ (desvio ponderado por volume).

    Recebe SÓ os candles do período (ex.: o dia de servidor corrente — quem chama recorta a
    janela e faz o reset 00:00). Preço típico tp=(H+L+C)/3, ponderado por `volumes` (tick_volume).
    σ é o desvio-padrão de tp ponderado por volume — a "largura" da VWAP, base das bandas que
    Wyckoff/fluxo usam como zona de valor. None se não há volume (protege divisão por zero).
    """
    if not closes or not volumes or len(closes) != len(volumes):
        return None
    soma_pv = soma_v = soma_pv2 = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        vol = float(v or 0)
        if vol <= 0:
            continue
        tp = (h + l + c) / 3.0
        soma_pv += tp * vol
        soma_v += vol
        soma_pv2 += tp * tp * vol
    if soma_v <= 0:
        return None
    vwap = soma_pv / soma_v
    var = max(soma_pv2 / soma_v - vwap * vwap, 0.0)
    sigma = var ** 0.5
    return {
        "vwap": vwap,
        "sup1": vwap + k1 * sigma, "inf1": vwap - k1 * sigma,
        "sup2": vwap + k2 * sigma, "inf2": vwap - k2 * sigma,
        "sigma": sigma,
    }


def vwap_serie(highs, lows, closes, volumes, chaves, k1: float = 1.0, k2: float = 2.0):
    """VWAP acumulada + bandas ±kσ candle-a-candle, RESETANDO a acumulação quando `chaves[i]`
    muda (âncora de sessão: meia-noite no forex, abertura do pregão na B3). É a VWAP como uma
    CURVA que se desenvolve ao longo do dia — o jeito que o manual fuzzy/Wyckoff a lê — e não
    um único valor horizontal. Devolve lista alinhada aos candles: cada item é
    {vwap, sup1, inf1, sup2, inf2} ou None enquanto ainda não houve volume na sessão. PURA."""
    n = len(closes)
    if not n or not (len(highs) == len(lows) == len(volumes) == len(chaves) == n):
        return []
    saida = []
    chave_atual = object()   # sentinela: força reset no 1º candle
    soma_pv = soma_v = soma_pv2 = 0.0
    for i in range(n):
        if chaves[i] != chave_atual:      # nova sessão → zera a acumulação
            chave_atual = chaves[i]
            soma_pv = soma_v = soma_pv2 = 0.0
        vol = float(volumes[i] or 0)
        if vol > 0:
            tp = (highs[i] + lows[i] + closes[i]) / 3.0
            soma_pv += tp * vol
            soma_v += vol
            soma_pv2 += tp * tp * vol
        if soma_v <= 0:
            saida.append(None)
            continue
        vwap = soma_pv / soma_v
        var = max(soma_pv2 / soma_v - vwap * vwap, 0.0)
        sigma = var ** 0.5
        saida.append({
            "vwap": vwap,
            "sup1": vwap + k1 * sigma, "inf1": vwap - k1 * sigma,
            "sup2": vwap + k2 * sigma, "inf2": vwap - k2 * sigma,
        })
    return saida


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
      - VISITA: sequência CONTÍNUA de candles tocando a banda conta como UM toque — 30 velas de
        consolidação raspando a borda não são 30 evidências independentes (antes inflavam a
        força e fabricavam "S/R forte" onde o preço nunca testou o nível de verdade).
      - rejeição: visita em que o preço FUROU O NÍVEL em si (não só a borda da banda) e FECHOU
        de volta do lado certo (resistência: pavio ≥ preço e close abaixo da banda; suporte:
        pavio ≤ preço e close acima). No máx. 1 rejeição por visita.
    Retorna {toques, rejeicoes, respeito (=rejeicoes/toques), ult_idx (recência)}.
    """
    lo, hi = preco - tol, preco + tol
    toques = rejeicoes = 0
    ult_idx = -1
    em_visita = visita_rejeitou = False
    for i in range(len(closes)):
        h, l, c = highs[i], lows[i], closes[i]
        if l <= hi and h >= lo:                      # o candle tocou a banda do nível
            if not em_visita:
                em_visita, visita_rejeitou = True, False
                toques += 1
            ult_idx = i
            rejeitou = (tipo == "resistencia" and h >= preco and c < lo) or \
                       (tipo == "suporte" and l <= preco and c > hi)
            if rejeitou and not visita_rejeitou:
                visita_rejeitou = True
                rejeicoes += 1
        else:
            em_visita = False
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


# --------------------------------------------------------------------------- #
# VSA (Volume Spread Analysis) — sinais Wyckoff da ÚLTIMA barra + DELTA de fluxo
# --------------------------------------------------------------------------- #
def vsa_sinais(opens, highs, lows, closes, volumes, *, janela=20,
               vol_alto=1.3, vol_baixo=0.7, deltas=None):
    """Sinais VSA (Wyckoff/WAPV) da ÚLTIMA barra, lidos de spread × posição do fechamento ×
    VOLUME relativo à média recente. PURA/sem look-ahead (só a barra atual + histórico anterior):

      - `spring`   : varre a mínima recente (novo fundo) e FECHA de volta pra cima com volume alto
                     → falso rompimento p/ baixo (absorção de venda) = viés de COMPRA.
      - `upthrust` : varre a máxima recente e FECHA de volta pra baixo com volume alto
                     → falso rompimento p/ cima = viés de VENDA.
      - `no_supply`: barra de QUEDA com volume BAIXO (secou a venda) = viés de COMPRA.
      - `no_demand`: barra de ALTA com volume BAIXO (secou a compra) = viés de VENDA.
      - `climax`   : volume EXTREMO (≥2× a média) — clímax de esforço.

    Em mercado com fluxo real (B3, `deltas` dado), acrescenta `delta` da barra e `delta_pos`/
    `delta_neg` (sinal do delta) — a confirmação de agressão que o WAPV usa. No forex `deltas`
    é None (o volume é só tick_volume; delta não existe). Retorna dict ou None (dados curtos)."""
    n = len(closes)
    if n < janela + 1 or len(volumes) < janela + 1:
        return None
    ref = [v for v in volumes[-janela - 1:-1] if v is not None]
    if len(ref) < janela // 2 or sum(ref) <= 0:
        return None
    media_vol = sum(ref) / len(ref)
    o, h, l, c, v = opens[-1], highs[-1], lows[-1], closes[-1], volumes[-1]
    spread = h - l
    if spread <= 0 or v is None or media_vol <= 0:
        return None
    close_pos = (c - l) / spread            # 0 = fechou na mínima, 1 = fechou na máxima
    vol_rel = v / media_vol
    up, down = c > o, c < o
    hh = max(highs[-janela - 1:-1]); ll = min(lows[-janela - 1:-1])
    out = {
        "vol_rel": round(vol_rel, 2), "close_pos": round(close_pos, 2),
        "spring":   bool(l < ll and close_pos >= 0.6 and vol_rel >= vol_alto),
        "upthrust": bool(h > hh and close_pos <= 0.4 and vol_rel >= vol_alto),
        "no_supply": bool(down and vol_rel <= vol_baixo),
        "no_demand": bool(up and vol_rel <= vol_baixo),
        "climax":   bool(vol_rel >= 2.0),
    }
    if deltas is not None and len(deltas) == n and deltas[-1] is not None:
        d = deltas[-1]
        out["delta"] = d
        out["delta_pos"] = d > 0     # agressão compradora
        out["delta_neg"] = d < 0     # agressão vendedora
    return out


def delta_de_ticks(ticks):
    """DELTA de fluxo (Σ volume agressor comprador − Σ agressor vendedor) de uma lista de trade
    ticks. PURA/testável. Cada tick = dict com `volume` e:
      - `flag` in {'buy','sell'} quando o feed dá o agressor (ideal, futuros B3), OU
      - só `last` (preço do negócio) → classifica pela REGRA DO TICK (uptick=compra, downtick=venda,
        repete o último em preço igual). Retorna float (0.0 se sem volume) ou None se lista vazia."""
    if not ticks:
        return None
    delta = 0.0
    ult_lado = 1  # 1 compra, -1 venda (para a regra do tick em preço igual)
    prev = None
    for t in ticks:
        vol = t.get("volume") or 0
        flag = t.get("flag")
        if flag == "buy":
            lado = 1
        elif flag == "sell":
            lado = -1
        else:
            p = t.get("last")
            if p is None or prev is None:
                lado = ult_lado
            elif p > prev:
                lado = 1
            elif p < prev:
                lado = -1
            else:
                lado = ult_lado
            prev = p if p is not None else prev
        ult_lado = lado
        delta += lado * vol
    return delta
