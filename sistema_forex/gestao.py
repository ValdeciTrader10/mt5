"""Fase 5 — Gestão de risco e saída (funções PURAS e testáveis).

Toda a matemática de "quando e como sair" mora aqui, sem tocar no MT5: o executor
(`executor.py`) passa números vindos do tick ao vivo e recebe a AÇÃO a tomar.

Saída por (nesta ordem de prioridade):
  1. Tempo máximo na posição (`TEMPO_MAX_POSICAO_H`).
  2. Força contrária de ESTRUTURA — evento SMC (BOS/CHOCH) contra a posição, MAS
     "com direito a desenvolver" (ver `avaliar_saida`): só protege lucro já feito
     (r ≥ estrut_min_r) e, se o sinal for fraco (BOS) e ainda houver ESPAÇO até o
     próximo nível contrário, SEGURA — deixa o preço andar em vez de sair no ruído.
  3. Força contrária de PREÇO — depois de atingir BE_TRIGGER_R, cede GIVEBACK_R do pico
     (reversão do momentum, capturada a cada tick).
  4. Break-even — em BE_TRIGGER_R move o stop para a entrada.

Contabilidade de pips SEMPRE por price_open/price_current (lição do MASMC).
"""


def calcular_sl(direcao: str, preco: float, atr: float, *, mult, min_pips, max_pips, pip) -> float:
    """Stop de emergência no servidor: `mult`×ATR da entrada, preso entre min/max pips."""
    dist = mult * atr if atr else min_pips * pip
    dist = max(min_pips * pip, min(dist, max_pips * pip))
    return preco - dist if direcao == "compra" else preco + dist


def pips(direcao: str, entrada: float, atual: float, pip: float) -> float:
    """Pips entre entrada e preço atual, com sinal (positivo = a favor)."""
    delta = (atual - entrada) if direcao == "compra" else (entrada - atual)
    return delta / pip if pip else 0.0


def r_por_risco(direcao: str, entrada: float, atual: float, risco: float) -> float:
    """Resultado atual em múltiplos de R, usando o RISCO INICIAL (fixo).

    Importante: o R usa o risco da abertura, não a distância ao stop atual — senão,
    ao mover o stop para o break-even, o denominador viraria zero e o R se perderia.
    """
    if not risco:
        return 0.0
    ganho = (atual - entrada) if direcao == "compra" else (entrada - atual)
    return ganho / risco


def _oposto(direcao: str, dir_evento: str) -> bool:
    return (direcao == "compra" and dir_evento == "baixa") or (direcao == "venda" and dir_evento == "alta")


def avaliar_saida(*, direcao, r, r_max, idade_h, ultimo_evento, be_movido,
                  be_trigger_r, giveback_r, tempo_max_h,
                  espaco_r=None, estrut_min_r=1.0, espaco_segurar_r=1.0) -> tuple:
    """Decide a ação para uma posição aberta. Retorna (acao, motivo).

    acao ∈ {"manter", "fechar", "mover_be"}. `r` é o R atual; `r_max`, o pico de R.

    Parâmetros da saída por estrutura "com direito a desenvolver":
      - `ultimo_evento`: dict {evento, direcao, tf} do evento SMC relevante para SAIR
        (o executor já filtra para os TFs de estrutura — M15/H1 —, deixando o M5 de fora).
      - `espaco_r`: espaço até o próximo nível contrário à frente, em múltiplos de R.
        `None` = sem nível à frente (campo aberto) → tratado como "muito espaço".
      - `estrut_min_r`: só sai por estrutura contrária depois deste lucro (em R).
      - `espaco_segurar_r`: com espaço ≥ isto e sinal fraco (BOS), SEGURA a posição.
    """
    if idade_h >= tempo_max_h:
        return ("fechar", f"tempo máximo ({idade_h:.1f}h ≥ {tempo_max_h}h)")

    # Força contrária de ESTRUTURA — só sai se o preço MOSTRAR reversão (CHOCH), com lucro
    # já desenvolvido. Um BOS de continuação contra, ou mera proximidade a um nível, NÃO
    # fecha: "ativou uma estratégia, deixa o preço andar" (o giveback/tempo/stop protegem).
    # (`espaco_r`/`espaco_segurar_r` mantidos por compat; a decisão agora é só pela reversão.)
    if r >= estrut_min_r and ultimo_evento and _oposto(direcao, ultimo_evento.get("direcao", "")):
        if (ultimo_evento.get("evento") or "").upper() == "CHOCH":
            return ("fechar",
                    f"reversão confirmada: CHOCH {ultimo_evento.get('direcao')} (r={r:.1f})")

    if r_max >= be_trigger_r and r <= r_max - giveback_r:
        return ("fechar", f"reversão: cedeu {giveback_r:.1f}R do pico ({r_max:.1f}R → {r:.1f}R)")

    if not be_movido and r >= be_trigger_r:
        return ("mover_be", f"R ≥ {be_trigger_r:.1f} → break-even")

    return ("manter", "")


def _moedas(par: str) -> tuple:
    """(base, quote) de um par tipo 'EURUSD#', 'GBPUSD#', 'USDCAD'."""
    p = par.replace("#", "").upper()
    return p[:3], p[3:6]


def exposicao_moedas(posicoes) -> dict:
    """Exposição líquida por moeda. `posicoes`: iterável de dicts com {par, direcao}.

    Compra do par = +1 na base / −1 na quote; venda = o inverso (1 unidade por posição).
    Ex.: comprar EURUSD e GBPUSD → {EUR:+1, GBP:+1, USD:−2} (risco dobrado short-USD).
    """
    exp: dict = {}
    for p in posicoes:
        base, quote = _moedas(p["par"])
        s = 1 if p["direcao"] == "compra" else -1
        exp[base] = exp.get(base, 0) + s
        exp[quote] = exp.get(quote, 0) - s
    return exp


def viola_correlacao(posicoes, par_novo: str, direcao_nova: str, limite: int) -> bool:
    """True se abrir (par_novo, direcao_nova) faria alguma moeda passar de `limite` líquido."""
    futuras = list(posicoes) + [{"par": par_novo, "direcao": direcao_nova}]
    return any(abs(v) > limite for v in exposicao_moedas(futuras).values())


def drawdown_estourou(saldo_inicial_dia: float, equity_atual: float, dd_max_pct: float) -> bool:
    """True se a perda do dia atingiu o teto (equity caiu dd_max_pct% do saldo inicial)."""
    if not saldo_inicial_dia:
        return False
    queda_pct = (saldo_inicial_dia - equity_atual) / saldo_inicial_dia * 100
    return queda_pct >= dd_max_pct
