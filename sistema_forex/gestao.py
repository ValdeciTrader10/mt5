"""Fase 5 — Gestão de risco e saída (funções PURAS e testáveis).

Toda a matemática de "quando e como sair" mora aqui, sem tocar no MT5: o executor
(`executor.py`) passa números vindos do tick ao vivo e recebe a AÇÃO a tomar.

Saída por (nesta ordem de prioridade):
  1. Tempo máximo na posição (`TEMPO_MAX_POSICAO_H`).
  2. Força contrária de ESTRUTURA — evento SMC (BOS/CHOCH) contra a posição, já no lucro.
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
                  be_trigger_r, giveback_r, tempo_max_h) -> tuple:
    """Decide a ação para uma posição aberta. Retorna (acao, motivo).

    acao ∈ {"manter", "fechar", "mover_be"}. `r` é o R atual; `r_max`, o pico de R.
    """
    if idade_h >= tempo_max_h:
        return ("fechar", f"tempo máximo ({idade_h:.1f}h ≥ {tempo_max_h}h)")

    if r > 0 and ultimo_evento and _oposto(direcao, ultimo_evento.get("direcao", "")):
        return ("fechar", f"força contrária: {ultimo_evento.get('evento')} {ultimo_evento.get('direcao')}")

    if r_max >= be_trigger_r and r <= r_max - giveback_r:
        return ("fechar", f"reversão: cedeu {giveback_r:.1f}R do pico ({r_max:.1f}R → {r:.1f}R)")

    if not be_movido and r >= be_trigger_r:
        return ("mover_be", f"R ≥ {be_trigger_r:.1f} → break-even")

    return ("manter", "")


def drawdown_estourou(saldo_inicial_dia: float, equity_atual: float, dd_max_pct: float) -> bool:
    """True se a perda do dia atingiu o teto (equity caiu dd_max_pct% do saldo inicial)."""
    if not saldo_inicial_dia:
        return False
    queda_pct = (saldo_inicial_dia - equity_atual) / saldo_inicial_dia * 100
    return queda_pct >= dd_max_pct
