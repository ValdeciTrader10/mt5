"""Calibração de escala da B3 (ETAPA 8b) — deriva WIN/WDO DOS CANDLES, não chuta.

Motivação (lição GOLD, registrada na memória do projeto): no ouro o 1º cap de SL era MENOR
que uma vela → 100% dos trades insta-estopavam (-1R). WIN/WDO têm escala ainda mais diferente
do forex (o mini índice move centenas de pontos por vela; o mini dólar, dezenas). Antes de
ligar o executor de sombra da B3, é PRECISO calibrar a escala a partir dos candles JÁ
coletados — jamais no chute.

O que este módulo entrega (espelha o padrão do `auditoria.py`: funções puras + leitura do
banco + dossiê de texto que o dono cola no chat):
  * `passo_preco`     — o TICK real do instrumento derivado da grade de preços (GCD dos deltas);
  * `estatisticas_tf` — por TF: distribuição de range da vela, ATR (mediana/p90) e spread;
  * `sugerir_params`  — piso/teto de SL e `tamanho_pip` para `PARAMS_SIMBOLO` de WIN/WDO,
                        dimensionados para que o ATR×mult MANDE sem clampar dentro do ruído
                        (SL nunca menor que uma vela p90 — a regra do ouro);
  * `valor_ponto`     — o valor-por-ponto (BRL) do contrato, base do P&L em reais da sombra
                        (default conhecido em `config_b3`, confirmado via `symbol_info` quando
                        a ponte B3 está acessível).

Sem viés look-ahead nem escrita: só LÊ candles fechados. Tudo testável em `tests/test_b3.py`.

Uso (CLI, roda contra o banco padrão da VPS):
    python -m sistema_forex.calibracao_b3                 # dossiê de todos os pares B3
    python -m sistema_forex.calibracao_b3 WIN$N           # só um par
    python -m sistema_forex.calibracao_b3 --json          # JSON em vez do texto
    python -m sistema_forex.calibracao_b3 --broker        # confirma valor-por-ponto no broker
"""

import json
import logging
import math
import sys
from datetime import datetime, timezone
from math import gcd

from . import config, config_b3, db, indicadores

log = logging.getLogger("calibracao_b3")


# --------------------------------------------------------------------------- #
# Estatística pura (sem numpy — a VPS roda o stdlib)
# --------------------------------------------------------------------------- #
def percentil(valores, p: float):
    """Percentil `p` (0–100) por interpolação linear. None se a lista está vazia."""
    xs = sorted(v for v in valores if v is not None)
    if not xs:
        return None
    if p <= 0:
        return xs[0]
    if p >= 100:
        return xs[-1]
    k = (len(xs) - 1) * (p / 100.0)
    f = int(math.floor(k))
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def passo_preco(precos, casas: int = 6):
    """TICK do instrumento = passo da grade de preços, derivado dos candles.

    Toma os preços DISTINTOS, ordena, e devolve o GCD dos deltas consecutivos (o maior passo
    que divide todos). Ex.: WIN move de 5 em 5 pontos → tick 5; WDO de 0,5 em 0,5 → tick 0,5.
    Robusto a ruído de ponto flutuante (arredonda a `casas`). None se há < 2 preços distintos.
    """
    vals = sorted({round(p, casas) for p in precos if p is not None})
    if len(vals) < 2:
        return None
    fator = 10 ** casas
    g = 0
    for a, b in zip(vals, vals[1:]):
        d = round((b - a) * fator)
        if d > 0:
            g = gcd(g, d)
    return (g / fator) if g else None


def serie_atr(highs, lows, closes, periodo: int = 14):
    """Série de ATR (Wilder) ao longo da janela — para tirar mediana/p90 da volatilidade."""
    trs = indicadores._true_ranges(highs, lows, closes)
    return indicadores.wilder(trs, periodo)


# --------------------------------------------------------------------------- #
# Estatísticas por timeframe (função pura: entram candles, saem números)
# --------------------------------------------------------------------------- #
def estatisticas_tf(candles: dict, spreads, periodo_atr: int = 14) -> dict:
    """Distribuição de volatilidade/tick/spread de UM timeframe (tudo em PONTOS de preço).

    `candles` = {"open","high","low","close":[...]}; `spreads` = lista do campo `spread` do
    candle (pontos inteiros do broker). Sem look-ahead: são candles já fechados.
    """
    highs, lows, closes = candles["high"], candles["low"], candles["close"]
    n = len(closes)
    ranges = [h - l for h, l in zip(highs, lows)]
    serie = serie_atr(highs, lows, closes, periodo_atr)
    precos = list(highs) + list(lows) + list(candles["open"]) + list(closes)
    return {
        "n": n,
        "tick": passo_preco(precos),
        "range_med": percentil(ranges, 50),
        "range_p90": percentil(ranges, 90),
        "range_max": max(ranges) if ranges else None,
        "atr_atual": serie[-1] if serie else None,
        "atr_med": percentil(serie, 50),
        "atr_p90": percentil(serie, 90),
        "spread_med": percentil(spreads, 50),
        "spread_p90": percentil(spreads, 90),
        "spread_max": max(spreads) if spreads else None,
    }


def _arredonda_cima(valor: float, tick: float):
    """Arredonda para CIMA ao múltiplo do tick (SL sempre num preço negociável)."""
    if not tick or tick <= 0 or valor is None:
        return valor
    return math.ceil(round(valor / tick, 6)) * tick


def sugerir_params(por_tf: dict, tf_base: str = "M5", mult_sl: float = 3.0,
                   folga_teto: float = 1.3) -> dict:
    """Sugere `tamanho_pip`/`sl_min`/`sl_max`/`spread_max` para o `PARAMS_SIMBOLO` da B3.

    Dimensionado pela REGRA DO OURO: o executor coloca SL = ATR×`mult_sl`; o piso e o teto só
    existem como guarda-corpo. Então:
      * `sl_min` NUNCA pode ficar menor que uma vela normal (senão insta-estopa) → piso =
        max(1 ATR mediano, range p90 da vela), arredondado ao tick;
      * `sl_max` tem de ser LARGO o bastante para o ATR×mult mandar até em pregão volátil →
        teto = ATR p90 × mult × folga (não clampa a vela gigante para dentro do ruído);
      * `tamanho_pip` = o TICK derivado dos candles (a unidade de "pip" da B3).
    Tudo em PONTOS de preço; também expõe em "pips" (=tick) para casar com o SL_*_PIPS do executor.
    """
    base = por_tf.get(tf_base)
    if not base or base.get("atr_med") is None:
        return {"suficiente": False, "tf_base": tf_base,
                "motivo": f"sem candles/ATR suficientes no TF base {tf_base}"}
    tick = base.get("tick")
    atr_med = base["atr_med"]
    atr_p90 = base.get("atr_p90") or atr_med
    range_p90 = base.get("range_p90") or 0.0

    sl_alvo_pts = mult_sl * atr_med                        # o que o executor tipicamente usaria
    sl_min_pts = _arredonda_cima(max(atr_med, range_p90), tick)
    sl_max_pts = _arredonda_cima(max(mult_sl * atr_p90 * folga_teto, sl_alvo_pts * 1.2), tick)

    def pips(v):
        if v is None or not tick:
            return None
        return int(round(v / tick))

    spread_p90 = base.get("spread_p90")
    return {
        "suficiente": True,
        "tf_base": tf_base,
        "mult_sl": mult_sl,
        "tick": tick,
        "n": base["n"],
        # em PONTOS de preço (a leitura física)
        "sl_alvo_pts": round(sl_alvo_pts, 6),
        "sl_min_pts": round(sl_min_pts, 6) if sl_min_pts is not None else None,
        "sl_max_pts": round(sl_max_pts, 6) if sl_max_pts is not None else None,
        # prontos para o PARAMS_SIMBOLO (unidade pip = tick), como no forex/ouro
        "tamanho_pip": tick,
        "sl_min_pips": pips(sl_min_pts),
        "sl_max_pips": pips(sl_max_pts),
        # spread no campo bruto do candle (pontos do broker) — reconciliar régua ao fiar o executor
        "spread_max_pontos": math.ceil(spread_p90) if spread_p90 is not None else None,
    }


# --------------------------------------------------------------------------- #
# Leitura do banco
# --------------------------------------------------------------------------- #
def _ler_tf(conn, par: str, tf: str, limite: int):
    """Últimos `limite` candles de (par, tf): candles-dict, spreads e janela [de, ate] utc."""
    rows = conn.execute(
        "SELECT time_utc, open, high, low, close, spread FROM candles WHERE par=? AND tf=? "
        "ORDER BY time_utc DESC LIMIT ?", (par, tf, limite)).fetchall()
    rows = list(reversed(rows))
    candles = {
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
    }
    spreads = [r["spread"] for r in rows if r["spread"] is not None]
    de = rows[0]["time_utc"] if rows else None
    ate = rows[-1]["time_utc"] if rows else None
    return candles, spreads, de, ate


def calibrar_par(conn, par: str, tfs=None, tf_base=None, janela=None, mult_sl=None,
                 periodo_atr=None) -> dict:
    """Calibra UM par B3: estatísticas por TF + sugestão de parâmetros de escala."""
    tfs = tfs or config_b3.TFS_COLETA_B3
    tf_base = tf_base or config_b3.CALIB_TF_BASE
    janela = janela or config_b3.CALIB_JANELA
    mult_sl = config_b3.CALIB_SL_MULT if mult_sl is None else mult_sl
    periodo_atr = periodo_atr or config.ATR_PERIODO
    por_tf = {}
    for tf in tfs:
        candles, spreads, de, ate = _ler_tf(conn, par, tf, janela)
        if not candles["close"]:
            continue
        est = estatisticas_tf(candles, spreads, periodo_atr)
        est["de_utc"], est["ate_utc"] = de, ate
        por_tf[tf] = est
    return {
        "par": par,
        "tf_base": tf_base,
        "por_tf": por_tf,
        "sugestao": sugerir_params(por_tf, tf_base, mult_sl),
        "valor_ponto_contrato": config_b3.valor_ponto(par),
    }


def calibrar(conn, pares=None, com_broker: bool = False, **kw) -> dict:
    """Calibra todos os pares B3. Se `com_broker`, confirma o valor-por-ponto via symbol_info."""
    pares = pares if pares is not None else config_b3.pares_ativos() or list(config_b3.PARES_B3)
    out = {"gerado_utc": None, "pares": {}}
    for par in pares:
        res = calibrar_par(conn, par, **kw)
        if com_broker:
            res["broker"] = _info_broker(par)
        out["pares"][par] = res
    return out


def _info_broker(par: str):
    """Especificações do contrato via ponte B3 (data-only). None se indisponível (tolerante)."""
    try:
        from . import mt5_bridge_b3
        nome = mt5_bridge_b3.resolver_simbolo(par)
        return mt5_bridge_b3.info_simbolo(nome)
    except Exception as e:  # noqa: BLE001 - a calibração funciona sem o broker (só candles)
        log.debug("info_broker(%s) indisponível: %s", par, e)
        return None


# --------------------------------------------------------------------------- #
# Dossiê de texto (o dono cola no chat da IA)
# --------------------------------------------------------------------------- #
def _hora(utc):
    if not utc:
        return "—"
    return datetime.fromtimestamp(utc, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt(v, casas=2):
    return "—" if v is None else f"{v:,.{casas}f}"


def dossie_texto(resultado: dict) -> str:
    """Bloco Markdown com a calibração de escala de cada par B3 — pronto para revisar."""
    linhas = ["# Calibração de escala — B3 (WIN/WDO)", ""]
    linhas.append("Derivada DOS CANDLES coletados (sem chute). Objetivo: dimensionar o SL para "
                  "que o ATR mande sem insta-estopar (a lição do ouro) antes de ligar a sombra da B3.")
    linhas.append("")
    for par, res in resultado.get("pares", {}).items():
        linhas.append(f"## {par}")
        vp = res.get("valor_ponto_contrato")
        linhas.append(f"- Valor-por-ponto (contrato, default): "
                      f"{('R$ ' + _fmt(vp)) if vp is not None else '—'} por ponto")
        bk = res.get("broker")
        if bk:
            linhas.append(f"- Broker (symbol_info): tick {bk.get('trade_tick_size')} · "
                          f"valor-por-ponto R$ {_fmt(bk.get('valor_ponto'))} · "
                          f"contrato {bk.get('trade_contract_size')}")
        # Tabela de volatilidade por TF
        linhas.append("")
        linhas.append("| TF | n | tick | range med | range p90 | ATR med | ATR p90 | spread p90 |")
        linhas.append("|----|---|------|-----------|-----------|---------|---------|------------|")
        for tf, e in res.get("por_tf", {}).items():
            linhas.append(
                f"| {tf} | {e['n']} | {_fmt(e['tick'], 4)} | {_fmt(e['range_med'])} | "
                f"{_fmt(e['range_p90'])} | {_fmt(e['atr_med'])} | {_fmt(e['atr_p90'])} | "
                f"{_fmt(e['spread_p90'], 0)} |")
        linhas.append("")
        s = res.get("sugestao", {})
        if not s.get("suficiente"):
            linhas.append(f"> ⚠️ Amostra insuficiente para sugerir escala: {s.get('motivo')}")
            linhas.append("")
            continue
        linhas.append(f"**Sugestão (TF base {s['tf_base']}, SL = ATR×{s['mult_sl']:g}, "
                      f"n={s['n']}):**")
        linhas.append(f"- SL típico do executor ≈ {_fmt(s['sl_alvo_pts'])} pts · "
                      f"piso {_fmt(s['sl_min_pts'])} pts · teto {_fmt(s['sl_max_pts'])} pts")
        linhas.append("- `PARAMS_SIMBOLO` proposto (unidade pip = tick):")
        linhas.append("  ```python")
        linhas.append(f'  "{par}": {{"tamanho_pip": {s["tamanho_pip"]}, '
                      f'"sl_min_pips": {s["sl_min_pips"]}, "sl_max_pips": {s["sl_max_pips"]}, '
                      f'"spread_max_pontos": {s["spread_max_pontos"]}}},')
        linhas.append("  ```")
        linhas.append("")
    linhas.append("---")
    linhas.append("Notas: (1) `sl_min` nunca fica abaixo de uma vela p90 — regra do ouro; "
                  "(2) confirmar o valor-por-ponto no `symbol_info` (`--broker`) antes de ligar o "
                  "P&L real da sombra; (3) o spread está no campo bruto do candle (pontos do broker) "
                  "— reconciliar a régua ao fiar o executor da B3.")
    return "\n".join(linhas)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    args = sys.argv[1:]
    como_json = "--json" in args
    com_broker = "--broker" in args
    pares = [a for a in args if not a.startswith("--")] or None
    with db.sessao() as conn:
        res = calibrar(conn, pares=pares, com_broker=com_broker)
    print(json.dumps(res, ensure_ascii=False, indent=2) if como_json else dossie_texto(res))


if __name__ == "__main__":
    main()
