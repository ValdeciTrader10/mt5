"""ETAPA 3 — Fuzzy score (Fuzzy Wyckoff), Sync Line e EV score.

A parte MATEMÁTICA é PURA e testável (nada de banco/MT5 aqui): entram as
características de um candle (delta/range/vol/corpo/seq, já normalizadas por
referência recente), sai um score 0–100 com estado (lima/verde/branco/fúcsia/
vermelho) e as flags de leitura de fluxo (absorção/exaustão/transição de causa).

Modelo (fuzzy triangular + regras SE-ENTÃO, defuzzificação por média ponderada):
  - magnitude do movimento |delta| (corpo em nº de "ranges médios"): fraco/médio/forte
  - volume relativo à média: baixo/médio/alto
  - corpo (|C-O|/range): doji (fraco) ↔ marubozu (cheio)
  A força direcional sai da combinação (rally = forte+corpo cheio+volume alto → ~100),
  e dois moduladores de FLUXO puxam o score:
  - ABSORÇÃO: volume alto com corpo fraco (muito esforço, pouco resultado) → some com a
    convicção direcional e levanta a flag.
  - EXAUSTÃO: impulso forte + volume alto no fim de uma sequência longa (clímax) → puxa
    o score de volta para o neutro (50) — alerta de reversão.
  - TRANSIÇÃO DE CAUSA: a vela INVERTE uma sequência estabelecida com volume relevante
    (mudança de caráter incipiente).

`atualizar_par` é a orquestração (lê candles do banco, calcula o que falta e grava em
`fuzzy_scores` com cache por (par, tf, candle)). `sync_line`/`ev_score` completam a ETAPA 3.
"""

import logging
from statistics import fmean

from . import config, indicadores

log = logging.getLogger("fuzzy")

# Estados por faixa de score (0–100). 50 = neutro; >50 comprador, <50 vendedor.
ESTADOS = ("vermelho", "fucsia", "branco", "verde", "lima")


def estado_por_score(score: float) -> str:
    """Rótulo de estado (cor) a partir do score fuzzy 0–100. Bandas FIÉIS ao PDF Fuzzy Wyckoff
    (item 1): lima 76–100 · verde 56–75 · branco 46–55 · fúcsia 26–45 · vermelho 0–25. Só afeta a
    COR (painel) e o componente EV da sync (não-bloqueante) — as entradas usam o score numérico."""
    if score >= 76:
        return "lima"
    if score >= 56:
        return "verde"
    if score >= 46:
        return "branco"
    if score >= 26:
        return "fucsia"
    return "vermelho"


# --------------------------------------------------------------------------- #
# Fuzzificação triangular / ombros (funções puras)
# --------------------------------------------------------------------------- #
def _tri(x: float, a: float, b: float, c: float) -> float:
    """Pertinência triangular: 0 em a e c, 1 em b, linear entre."""
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    return (x - a) / (b - a) if x < b else (c - x) / (c - b)


def _ombro_sobe(x: float, a: float, b: float) -> float:
    """Ombro crescente: 0 até a, sobe linear, 1 a partir de b."""
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


def _ombro_desce(x: float, a: float, b: float) -> float:
    """Ombro decrescente: 1 até a, desce linear, 0 a partir de b."""
    if x <= a:
        return 1.0
    if x >= b:
        return 0.0
    return (b - x) / (b - a)


# --------------------------------------------------------------------------- #
# Características do candle (normalizadas pela referência recente) — PURA
# --------------------------------------------------------------------------- #
def _sequencia(dirs: list, fim: int) -> int:
    """Comprimento (com sinal) da sequência de velas da MESMA cor terminando em `fim`.

    dirs[j] ∈ {+1, -1, 0}. Ex.: 3 velas de alta seguidas → +3; 2 de baixa → -2; doji → 0.
    """
    if fim < 0 or fim >= len(dirs) or dirs[fim] == 0:
        return 0
    s = dirs[fim]
    run = 0
    k = fim
    while k >= 0 and dirs[k] == s:
        run += 1
        k -= 1
    return s * run


def caracteristicas(opens, highs, lows, closes, volumes, *, janela: int = 20):
    """Características normalizadas do ÚLTIMO candle (o fechado, alvo do score). None se faltam
    dados. `janela` = nº de velas ANTERIORES usadas como referência (range/volume médios)."""
    n = len(closes)
    if n < 3 or len(volumes) != n:
        return None
    i = n - 1
    rng = highs[i] - lows[i]
    corpo = abs(closes[i] - opens[i]) / rng if rng > 0 else 0.0
    ini = max(0, i - janela)
    ranges = [highs[j] - lows[j] for j in range(ini, i)]
    range_med = fmean(ranges) if ranges else (rng or 1e-9)
    if range_med <= 0:
        range_med = rng or 1e-9
    vols = [float(volumes[j] or 0) for j in range(ini, i)]
    vol_med = fmean(vols) if vols and sum(vols) > 0 else float(volumes[i] or 1)
    if vol_med <= 0:
        vol_med = 1.0
    dirs = [1 if closes[j] > opens[j] else (-1 if closes[j] < opens[j] else 0) for j in range(n)]
    return {
        "delta": (closes[i] - opens[i]) / range_med,      # corpo com sinal, em ranges médios
        "rng": rng / range_med,                            # range relativo
        "vol": float(volumes[i] or 0) / vol_med,           # volume relativo
        "corpo": corpo,                                    # 0 (doji) .. 1 (marubozu)
        "seq": _sequencia(dirs, i),                        # sequência incluindo a atual
        "seq_ant": _sequencia(dirs, i - 1),                # sequência até a anterior
    }


# --------------------------------------------------------------------------- #
# Inferência fuzzy → score + flags (PURA)
# --------------------------------------------------------------------------- #
def pontuar(carac: dict) -> dict:
    """Roda o motor fuzzy sobre as características e devolve score/estado/flags.

    score ∈ [0,100] (50 neutro). Retorna também as pertinências para depuração/painel.
    """
    delta = carac["delta"]
    vol = carac["vol"]
    corpo = carac["corpo"]
    seq = carac["seq"]
    seq_ant = carac["seq_ant"]
    mov = abs(delta)
    direcao = 1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0)

    # Magnitude do movimento (em ranges médios)
    forte = _ombro_sobe(mov, 0.9, 1.6)
    medio = _tri(mov, 0.35, 0.8, 1.3)
    fraco = _ombro_desce(mov, 0.25, 0.7)
    # Volume relativo
    v_alto = _ombro_sobe(vol, 1.2, 1.9)
    v_baixo = _ombro_desce(vol, 0.7, 1.1)
    # Corpo
    corpo_cheio = _ombro_sobe(corpo, 0.5, 0.8)
    corpo_fraco = _ombro_desce(corpo, 0.2, 0.45)

    # Regras de convicção direcional (defuzzificação por média ponderada de singletons).
    w_rally = min(forte, corpo_cheio, v_alto)          # impulso com volume → 1.0
    w_imp = min(medio, max(corpo_cheio, v_alto))       # impulso comum      → 0.7
    w_norm = min(medio, v_baixo)                       # movimento sem lastro→ 0.45
    w_fraco = fraco                                    # quase parado        → 0.15
    num = w_rally * 1.0 + w_imp * 0.7 + w_norm * 0.45 + w_fraco * 0.15
    den = w_rally + w_imp + w_norm + w_fraco
    conviccao = (num / den) if den > 0 else 0.0        # 0..1

    # ABSORÇÃO: volume alto + corpo fraco (esforço sem resultado). Some com a convicção.
    absorcao_g = min(v_alto, corpo_fraco)
    conviccao *= (1 - 0.7 * absorcao_g)

    # EXAUSTÃO: impulso forte + volume alto no fim de sequência longa (clímax) → reversão.
    seq_longa = _ombro_sobe(abs(seq), 3, 6)
    exaustao_g = min(forte, v_alto, seq_longa)

    score = 50.0 + direcao * conviccao * 50.0
    # A exaustão PUXA o score de volta ao neutro (alerta de que o impulso vai virar).
    score = 50.0 + (score - 50.0) * (1 - 0.85 * exaustao_g)

    # TRANSIÇÃO DE CAUSA: a vela inverteu uma sequência estabelecida (seq flipou p/ ±1 após
    # uma corrida ≥3 no sentido oposto) com volume ao menos médio → mudança de caráter.
    transicao = bool(abs(seq) == 1 and abs(seq_ant) >= 3 and (seq * seq_ant) < 0
                     and _ombro_sobe(vol, 0.9, 1.4) >= 0.5)

    return {
        "score": round(score, 1),
        "estado": estado_por_score(score),
        "absorcao": absorcao_g >= 0.4,
        "exaustao": exaustao_g >= 0.35,
        "transicao": transicao,
        "pert": {"forte": round(forte, 2), "v_alto": round(v_alto, 2),
                 "corpo_cheio": round(corpo_cheio, 2), "absorcao_g": round(absorcao_g, 2),
                 "exaustao_g": round(exaustao_g, 2)},
    }


def avaliar_candle(opens, highs, lows, closes, volumes, *, janela: int = 20):
    """Atalho: características + pontuação de uma janela de candles. None se faltam dados."""
    carac = caracteristicas(opens, highs, lows, closes, volumes, janela=janela)
    if carac is None:
        return None
    r = pontuar(carac)
    r.update({k: carac[k] for k in ("delta", "rng", "vol", "corpo", "seq")})
    return r


def flags_no_indice(opens, highs, lows, closes, volumes, i: int, *, janela: int = 20):
    """Flags/score fuzzy do candle no ÍNDICE `i` (não necessariamente o último), SEM look-ahead.

    Fatia a janela até `i` (inclusive) e reaproveita `caracteristicas`+`pontuar`: assim a
    absorção/exaustão/transição de um candle INTERNO (ex.: a vela que varreu a liquidez no
    sweep) usa EXATAMENTE a mesma definição do resto do sistema, olhando só candles ≤ i.
    Retorna o dict de `pontuar` (com `absorcao`/`exaustao`/`transicao`) ou None se faltam dados.
    """
    n = len(closes)
    if i < 2 or i >= n or len(volumes) != n:
        return None
    sl = slice(0, i + 1)
    carac = caracteristicas(opens[sl], highs[sl], lows[sl], closes[sl], volumes[sl],
                            janela=janela)
    return pontuar(carac) if carac is not None else None


# --------------------------------------------------------------------------- #
# Sync Line micro/macro — alinhamento entre timeframes (PURA)
# --------------------------------------------------------------------------- #
def _lado(score) -> int:
    """Lado do score: +1 comprador (>=60), -1 vendedor (<=40), 0 neutro. None → 0."""
    if score is None:
        return 0
    if score >= 60:
        return 1
    if score <= 40:
        return -1
    return 0


def _cor_alinhamento(lados: list) -> str:
    """verde se todos os lados presentes concordam em alta, vermelho em baixa, amarelo se
    divergem/neutros. Lista de scores (pode conter None)."""
    ls = [_lado(s) for s in lados if s is not None]
    if not ls or any(x == 0 for x in ls) or (1 in ls and -1 in ls):
        return "amarelo"
    return "verde" if ls[0] == 1 else "vermelho"


def sync_line(scores_por_tf: dict, *, micro_tfs=("M1", "M5"), macro_tfs=("M15", "H1")) -> dict:
    """Sync Line: micro (TFs finos) e macro (TFs altos) + estado combinado.

    `scores_por_tf`: {tf: score}. Micro/macro verdes → estado verde; ambos vermelhos → vermelho;
    qualquer divergência → amarelo (não sincronizado). É o semáforo de alinhamento do painel.
    """
    micro = _cor_alinhamento([scores_por_tf.get(tf) for tf in micro_tfs])
    macro = _cor_alinhamento([scores_por_tf.get(tf) for tf in macro_tfs])
    if micro == macro and micro in ("verde", "vermelho"):
        estado = micro
    else:
        estado = "amarelo"
    return {"micro": micro, "macro": macro, "estado": estado}


def forca_sync(scores_por_tf: dict, *, micro_tfs=("M1", "M5"), macro_tfs=("M15", "H1")) -> dict:
    """FORÇA CONTÍNUA (inspirada no 'Sentinel_Sync_Line' do criador do PDF). Diferente da `sync_line`
    (3 estados), devolve uma linha NUMÉRICA que mostra a força construindo/divergindo ANTES da cor virar.

    - `micro` = média dos (score−50) dos TFs finos (M1/M5) → esforço direcional de curto prazo (−50..+50).
    - `macro` = idem dos TFs altos (M15/H1) → a maré (oceano).
    - `forca` = 50 + média(micro, macro), recentrada em 0..100 (50 neutro) → a linha p/ o painel (comparável
      às 4 linhas de score).
    - `estado` = verde (ambos +), vermelho (ambos −), amarelo = DIVERGÊNCIA micro×macro (o 'ATENÇÃO').
    PURA/testável. A fórmula é fiel ao PRINCÍPIO do PDF (esforço micro/macro) — o PDF não dá a fórmula
    numérica exata do Sentinela, então esta é a nossa versão para VALIDAR por comparação na sombra."""
    def _media_desvio(tfs):
        vals = [scores_por_tf.get(t) for t in tfs]
        vals = [v for v in vals if v is not None]
        return (sum(vals) / len(vals) - 50.0) if vals else 0.0
    micro = _media_desvio(micro_tfs)
    macro = _media_desvio(macro_tfs)
    forca = max(0.0, min(100.0, 50.0 + 0.5 * (micro + macro)))
    if micro > 0 and macro > 0:
        estado = "verde"
    elif micro < 0 and macro < 0:
        estado = "vermelho"
    else:
        estado = "amarelo"
    return {"micro": round(micro, 1), "macro": round(macro, 1), "forca": round(forca, 1),
            "estado": estado, "divergencia": bool(micro * macro < 0)}


def leque_spread(scores_por_tf: dict, tfs=("M1", "M5", "M15", "H1")) -> float:
    """Abertura do LEQUE = amplitude (máx−mín) entre as linhas de score dos TFs. Grande = leque ABERTO
    (tendência, linhas espalhadas); pequeno = COMPRIMIDO (mola, consenso). PURA."""
    vals = [scores_por_tf.get(t) for t in tfs if scores_por_tf.get(t) is not None]
    return round(max(vals) - min(vals), 1) if len(vals) >= 2 else 0.0


def forca_serie(conn, par: str, tempos, *, micro_tfs=("M1", "M5"), macro_tfs=("M15", "H1")) -> list:
    """Série de FORÇA/LEQUE alinhada (asof) aos instantes `tempos` (cronológicos): para cada t usa o
    ÚLTIMO score ≤ t de cada TF (M1/M5/M15/H1) e aplica `forca_sync`/`leque_spread`. Base da linha do
    painel e do histórico p/ as estratégias E_SENTINELA. Sem look-ahead (só scores já fechados ≤ t)."""
    tfs = tuple(dict.fromkeys(list(micro_tfs) + list(macro_tfs)))
    sebe = {}
    for tf in tfs:
        rows = conn.execute(
            "SELECT time_utc, score FROM fuzzy_scores WHERE par=? AND tf=? ORDER BY time_utc ASC",
            (par, tf)).fetchall()
        sebe[tf] = [(r["time_utc"], r["score"]) for r in rows]
    ponteiro = {tf: 0 for tf in tfs}
    ult = {tf: None for tf in tfs}
    saida = []
    for t in tempos:
        for tf in tfs:
            serie = sebe[tf]
            while ponteiro[tf] < len(serie) and serie[ponteiro[tf]][0] <= t:
                ult[tf] = serie[ponteiro[tf]][1]
                ponteiro[tf] += 1
        f = forca_sync(ult, micro_tfs=micro_tfs, macro_tfs=macro_tfs)
        f["time"] = t
        f["leque"] = leque_spread(ult, tfs=tfs)
        saida.append(f)
    return saida


# --------------------------------------------------------------------------- #
# EV score — 4 componentes (PURA). NÃO bloqueia na v1: só carimba a decisão.
# --------------------------------------------------------------------------- #
def ev_score(*, confluencia: float, fuzzy: float, sync: float, localizacao: float,
             pesos=(0.35, 0.25, 0.20, 0.20)) -> dict:
    """Nota de Expected Value 0–100 a partir de 4 componentes já normalizados (0–1):

      - confluencia: força/score da própria estratégia (quanto de evidência a favor).
      - fuzzy: alinhamento do fuzzy_score do TF com a direção do sinal.
      - sync: alinhamento da Sync Line (multi-TF) com a direção.
      - localizacao: qualidade da localização (ex.: comprar abaixo / vender acima da VWAP).

    Média ponderada → 0–100. Componentes fora de [0,1] são grampeados. Só informa (v1).
    """
    comps = {"confluencia": confluencia, "fuzzy": fuzzy, "sync": sync, "localizacao": localizacao}
    vals = [max(0.0, min(1.0, v if v is not None else 0.5)) for v in comps.values()]
    total = sum(w * v for w, v in zip(pesos, vals)) / (sum(pesos) or 1)
    return {"ev": round(total * 100, 1), "componentes": {k: round(v, 2)
            for k, v in zip(comps, vals)}}


def componente_fuzzy(direcao: str, score) -> float:
    """Componente EV do fuzzy: quão a favor da `direcao` está o score fuzzy (0–1). 0.5 se None."""
    if score is None:
        return 0.5
    if direcao == "compra":
        return max(0.0, min(1.0, (score - 50) / 50 + 0.5))
    if direcao == "venda":
        return max(0.0, min(1.0, (50 - score) / 50 + 0.5))
    return 0.5


def componente_sync(direcao: str, estado: str) -> float:
    """Componente EV da Sync Line (0–1) para a `direcao`. amarelo/None = 0.5."""
    if estado == "verde":
        return 1.0 if direcao == "compra" else 0.0
    if estado == "vermelho":
        return 1.0 if direcao == "venda" else 0.0
    return 0.5


def componente_localizacao(direcao: str, close, vwap) -> float:
    """Componente EV de localização vs VWAP (0–1): comprar ABAIXO / vender ACIMA da VWAP tem
    melhor EV (espaço até a média de valor). Sem VWAP → 0.5 (neutro)."""
    if vwap is None or close is None:
        return 0.5
    if direcao == "compra":
        return 0.7 if close <= vwap else 0.35
    if direcao == "venda":
        return 0.7 if close >= vwap else 0.35
    return 0.5


# --------------------------------------------------------------------------- #
# Orquestração: calcula e grava o que falta em `fuzzy_scores` (cache por candle)
# --------------------------------------------------------------------------- #
def _existentes(conn, par: str, tf: str, desde: int) -> set:
    return {r["time_utc"] for r in conn.execute(
        "SELECT time_utc FROM fuzzy_scores WHERE par=? AND tf=? AND time_utc>=?",
        (par, tf, desde)).fetchall()}


def atualizar_par(conn, par: str, agora: int, tfs=None, limite: int = None, janela: int = None) -> int:
    """Calcula o fuzzy_score dos candles ainda não pontuados de cada TF e grava (cache por
    (par, tf, candle)). Recalcula por janela deslizante (só os candles recentes) para não varrer
    o histórico todo a cada ciclo. Retorna quantos scores novos foram gravados."""
    tfs = tfs or config.FUZZY_TFS
    limite = limite or config.FUZZY_JANELA
    janela = janela or config.FUZZY_REF_JANELA
    gravados = 0
    for tf in tfs:
        rows = conn.execute(
            # Item 6: volume REAL (contratos) quando existe — na B3 é o volume Wyckoff verdadeiro
            # (alimenta absorção/exaustão); no forex real_volume é NULL → cai no tick_volume.
            "SELECT time_utc, open, high, low, close, "
            "COALESCE(NULLIF(real_volume,0), tick_volume) AS vol FROM candles "
            "WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
            (par, tf, limite),
        ).fetchall()
        rows = list(reversed(rows))
        if len(rows) < 3:
            continue
        desde = rows[0]["time_utc"]
        ja = _existentes(conn, par, tf, desde)
        opens = [r["open"] for r in rows]
        highs = [r["high"] for r in rows]
        lows = [r["low"] for r in rows]
        closes = [r["close"] for r in rows]
        vols = [r["vol"] for r in rows]
        # Cada candle i (a partir do 3º) é pontuado com a janela ATÉ i (sem look-ahead).
        for i in range(2, len(rows)):
            t = rows[i]["time_utc"]
            if t in ja:
                continue
            r = avaliar_candle(opens[:i + 1], highs[:i + 1], lows[:i + 1], closes[:i + 1],
                               vols[:i + 1], janela=janela)
            if r is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO fuzzy_scores (par, tf, time_utc, score, estado, delta, "
                "rng, vol, corpo, seq, absorcao, exaustao, transicao, criado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (par, tf, t, r["score"], r["estado"], r["delta"], r["rng"], r["vol"],
                 r["corpo"], int(r["seq"]), int(r["absorcao"]), int(r["exaustao"]),
                 int(r["transicao"]), agora),
            )
            gravados += 1
    return gravados


def scores_recentes(conn, par: str, tfs=None) -> dict:
    """Último fuzzy_score de cada TF (para a Sync Line e o EV). {tf: {score, estado, ...}}."""
    tfs = tfs or config.FUZZY_TFS
    saida = {}
    for tf in tfs:
        r = conn.execute(
            "SELECT time_utc, score, estado, absorcao, exaustao, transicao FROM fuzzy_scores "
            "WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT 1", (par, tf)).fetchone()
        if r:
            saida[tf] = dict(r)
    return saida


def atualizar_sync(conn, par: str, agora: int) -> dict:
    """Calcula a Sync Line do par a partir dos últimos scores e grava em `sync_line`
    (cache por (par, time_utc do candle mais recente)). Retorna o dict da sync line."""
    scores = scores_recentes(conn, par)
    if not scores:
        return {}
    sl = sync_line({tf: v["score"] for tf, v in scores.items()},
                   micro_tfs=config.SYNC_MICRO_TFS, macro_tfs=config.SYNC_MACRO_TFS)
    t = max(v["time_utc"] for v in scores.values())
    micro_score = fmean([scores[tf]["score"] for tf in config.SYNC_MICRO_TFS if tf in scores]) \
        if any(tf in scores for tf in config.SYNC_MICRO_TFS) else None
    macro_score = fmean([scores[tf]["score"] for tf in config.SYNC_MACRO_TFS if tf in scores]) \
        if any(tf in scores for tf in config.SYNC_MACRO_TFS) else None
    conn.execute(
        "INSERT OR REPLACE INTO sync_line (par, time_utc, micro, macro, estado, micro_score, "
        "macro_score, criado_em) VALUES (?,?,?,?,?,?,?,?)",
        (par, t, sl["micro"], sl["macro"], sl["estado"], micro_score, macro_score, agora),
    )
    return sl
