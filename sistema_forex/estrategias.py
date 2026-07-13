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
"""

ESTRATEGIA = "confluencia_v1"


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


def _decisao(resultado, direcao, regime, score, confluencias, motivo):
    return {
        "resultado": resultado,           # entrou | nao_entrou
        "direcao": direcao,               # compra | venda | None
        "estrategia": ESTRATEGIA,
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
