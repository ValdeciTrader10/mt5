"""ETAPA 7 — Relatório sombra multi-variante.

O laboratório roda VÁRIAS variantes em sombra ao mesmo tempo (A_ORIGINAL = controle,
B_FUZZY_PURO = fuzzy puro, C_HIBRIDA = as 9 estratégias + filtro fuzzy). Este módulo é a
LEITURA estatística que responde, por DADOS, qual (variante × estratégia × TF × par) tem edge
real — o insumo para, ao fim das 4–8 semanas, aprovar células para o executor demo (ETAPA 9).

Entrega (tudo PURO/testável; o I/O fica nas bordas — lê trades/decisões FECHADOS, sem
look-ahead):
  - `vw_performance` / `ranking_celulas`: ranking por EXPECTÂNCIA em R por célula, marcando as
    que já têm sinais suficientes (config.RELATORIO_MIN_SINAIS);
  - `por_variante`: KPIs + curva de equity (final/maxDD) por variante — A vs B vs C num olhar;
  - `heatmap_estrategia_tf`: expectância R por (estratégia × TF) dentro de cada variante;
  - `a_vs_c`: o efeito do filtro fuzzy — dos setups que a Variante A tomou, quantos a C BLOQUEOU
    e, desses, quantos eram PERDEDORES (prejuízo evitado) vs VENCEDORES (lucro perdido);
  - `distribuicao_bloqueio`: por que a C bloqueou (motivos dos vetos fuzzy);
  - `split_half`: a expectância se mantém nas DUAS metades do período? (edge estável × sorte).

O estudo é o livro SOMBRA (simulado=1); o livro REAL (demo curado) é validação à parte (ETAPA 5,
aba "Sombra vs Real" do /analitico). Resumo semanal via Telegram e CLI:

    python -m sistema_forex.relatorio                      # período todo (texto)
    python -m sistema_forex.relatorio 2026-07-01 2026-07-31
    python -m sistema_forex.relatorio --json
    python -m sistema_forex.relatorio semanal              # últimos 7 dias + envia ao Telegram
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import config, db, telegram_notif
from .auditoria import _epoch, _media, _normalizar_motivo, _res_r

log = logging.getLogger("relatorio")


# --------------------------------------------------------------------------- #
# Carga e agregação
# --------------------------------------------------------------------------- #
def _filtro_mercado(mercado: str, alias: str = "") -> str:
    """Fragmento WHERE que isola o livro de UM mercado (legado NULL = forex). Sem ele o
    relatório/gate somava BRL da B3 com USD do forex e podia promover célula da B3 p/ o
    executor do forex — nunca misturar mercados numa métrica."""
    p = f"{alias}." if alias else ""
    return f"{p}mercado='b3'" if mercado == "b3" else f"({p}mercado IS NULL OR {p}mercado='forex')"


def _carregar_trades(conn, de_e, ate_e, simulado=1, mercado="forex") -> list:
    """Trades FECHADOS do livro `simulado` (1=sombra, 0=real) de UM mercado, já com R por trade."""
    cond, args = ["fechamento_utc IS NOT NULL", "simulado=?", _filtro_mercado(mercado)], [simulado]
    if de_e:
        cond.append("fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("fechamento_utc <= ?"); args.append(ate_e)
    where = " AND ".join(cond)
    rows = conn.execute(
        f"SELECT id, par, tf, estrategia, direcao, pips, lucro_usd, risco_inicial, preco_entrada, "
        f"preco_saida, mae_r, mfe_r, variante, abertura_utc, fechamento_utc, decisao_id "
        f"FROM trades WHERE {where} ORDER BY fechamento_utc",
        args,
    ).fetchall()
    trades = []
    for r in rows:
        t = dict(r)
        t["tf"] = t["tf"] or "M5"
        t["variante"] = t["variante"] or "A_ORIGINAL"
        t["r"] = _res_r(t)
        trades.append(t)
    return trades


def _agg(trades: list) -> dict:
    """KPIs de um grupo: N, expectância em R e USD, PF, winrate, MAE/MFE médios."""
    n = len(trades)
    ganhos = [t for t in trades if (t["lucro_usd"] or 0) > 0]
    perdas = [t for t in trades if (t["lucro_usd"] or 0) < 0]
    bruto_g = sum(t["lucro_usd"] or 0 for t in ganhos)
    bruto_p = sum(t["lucro_usd"] or 0 for t in perdas)  # ≤ 0
    usd = sum(t["lucro_usd"] or 0 for t in trades)
    rs = [t["r"] for t in trades if t["r"] is not None]
    return {
        "n": n,
        "n_com_r": len(rs),   # trades que ENTRAM na exp_r (r calculável) — o N honesto do gate
        "winrate": round(100 * len(ganhos) / n, 1) if n else 0.0,
        "usd": round(usd, 2),
        "exp_usd": round(usd / n, 2) if n else 0.0,
        "exp_r": round(sum(rs) / len(rs), 3) if rs else None,   # expectância em R (a métrica-mãe)
        "profit_factor": round(bruto_g / abs(bruto_p), 2) if bruto_p else None,
        "mae_medio": _media([t.get("mae_r") for t in trades]),
        "mfe_medio": _media([t.get("mfe_r") for t in trades]),
    }


# --------------------------------------------------------------------------- #
# Ranking por célula (variante × estratégia × TF × par) — o coração do relatório
# --------------------------------------------------------------------------- #
def ranking_celulas(trades: list, min_sinais: int) -> list:
    """Uma linha por CÉLULA (variante × estratégia × TF × par), ordenada por expectância R
    (desc). `suficiente=True` quando N ≥ `min_sinais` (a conclusão só vale com amostra)."""
    grupos: dict = {}
    for t in trades:
        chave = (t["variante"], t["estrategia"], t["tf"], t["par"])
        grupos.setdefault(chave, []).append(t)
    linhas = []
    for (var, est, tf, par), ts in grupos.items():
        kpi = _agg(ts)
        linhas.append({
            "variante": var, "variante_nome": config.nome_variante(var),
            "estrategia": est, "estrategia_nome": config.nome_estrategia(est),
            "tf": tf, "par": par,
            "chave": f"{config.nome_variante(var)} · {config.nome_estrategia(est)} · {tf} · {par}",
            "suficiente": kpi["n"] >= min_sinais, **kpi,
        })
    # ordena: primeiro as com amostra suficiente, depois por expectância R desc.
    linhas.sort(key=lambda x: (not x["suficiente"], -(x["exp_r"] if x["exp_r"] is not None else -9)))
    return linhas


def por_variante(trades: list) -> list:
    """KPIs + curva de equity (final/maxDD) por VARIANTE — A vs B vs C num olhar."""
    grupos: dict = {}
    for t in trades:
        grupos.setdefault(t["variante"], []).append(t)
    linhas = []
    for var, ts in grupos.items():
        kpi = _agg(ts)
        eq = _equity(ts)
        linhas.append({"variante": var, "variante_nome": config.nome_variante(var),
                       **kpi, "usd_final": eq["final"], "max_dd": eq["max_dd"]})
    linhas.sort(key=lambda x: x["variante"])   # A, B, C em ordem
    return linhas


def _equity(trades: list, base: float = 0.0) -> dict:
    """Curva de capital cumulativa (ordem de fechamento) + drawdown máximo (USD)."""
    ordenados = sorted([t for t in trades if t["fechamento_utc"]], key=lambda t: t["fechamento_utc"])
    eq = pico = base
    max_dd = 0.0
    serie = []
    for t in ordenados:
        eq += t["lucro_usd"] or 0
        pico = max(pico, eq)
        max_dd = min(max_dd, eq - pico)
        serie.append({"t": t["fechamento_utc"], "eq": round(eq, 2)})
    return {"final": round(eq - base, 2), "max_dd": round(max_dd, 2), "serie": serie}


def heatmap_estrategia_tf(trades: list) -> dict:
    """{variante: [{estrategia, tf, exp_r, n}]} — a expectância R por (estratégia × TF) dentro de
    cada variante (agregando os pares). Responde 'qual estratégia rende em qual TF' por variante."""
    grupos: dict = {}
    for t in trades:
        grupos.setdefault((t["variante"], t["estrategia"], t["tf"]), []).append(t)
    saida: dict = {}
    for (var, est, tf), ts in grupos.items():
        kpi = _agg(ts)
        saida.setdefault(var, []).append({
            "estrategia": est, "estrategia_nome": config.nome_estrategia(est), "tf": tf,
            "exp_r": kpi["exp_r"], "n": kpi["n"], "usd": kpi["usd"],
        })
    for var in saida:
        saida[var].sort(key=lambda x: -(x["exp_r"] if x["exp_r"] is not None else -9))
    return saida


# --------------------------------------------------------------------------- #
# A vs C — o efeito do filtro fuzzy (trades ruins evitados vs bons perdidos)
# --------------------------------------------------------------------------- #
def a_vs_c(conn, de_e, ate_e, mercado="forex") -> dict:
    """Mede o impacto do filtro fuzzy da Variante C. Dos setups que a A tomou (cada decisão A
    'entrou' que virou trade), a C ou os MANTEVE ('entrou') ou os BLOQUEOU ('nao_entrou'). Para os
    bloqueados, olhamos o DESFECHO do trade A correspondente: se foi PERDA → prejuízo EVITADO (bom
    veto); se foi GANHO → lucro PERDIDO (veto ruim). Casamento por (par, tf, estratégia, time_utc)
    entre as decisões A e C (mesmo setup, mesmo candle)."""
    # Desfecho do trade A por (par, tf, estrategia, time_utc) da sua decisão de origem.
    cond, args = ["t.simulado=1", "t.variante='A_ORIGINAL'", "t.fechamento_utc IS NOT NULL",
                  _filtro_mercado(mercado, "t")], []
    if de_e:
        cond.append("t.fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("t.fechamento_utc <= ?"); args.append(ate_e)
    where = " AND ".join(cond)
    desfecho: dict = {}
    for r in conn.execute(
        f"SELECT d.par par, d.tf tf, d.estrategia est, d.time_utc t, t.lucro_usd usd "
        f"FROM trades t JOIN decisoes d ON t.decisao_id = d.id WHERE {where}", args):
        desfecho[(r["par"], r["tf"] or "M5", r["est"], r["t"])] = r["usd"] or 0.0

    # Decisões da Variante C no período (por time_utc do candle = hora do servidor).
    dcond, dargs = ["variante='C_HIBRIDA'", _filtro_mercado(mercado)], []
    if de_e:
        dcond.append("time_utc >= ?"); dargs.append(de_e)
    if ate_e:
        dcond.append("time_utc <= ?"); dargs.append(ate_e)
    dwhere = " AND ".join(dcond)
    cdecs = conn.execute(
        f"SELECT par, tf, estrategia, time_utc, resultado FROM decisoes WHERE {dwhere}", dargs
    ).fetchall()

    manteve = bloqueou = 0
    ruins_evitados = bons_perdidos = sem_desfecho = 0
    usd_evitado = usd_perdido = 0.0
    for c in cdecs:
        chave = (c["par"], c["tf"] or "M5", c["estrategia"], c["time_utc"])
        if c["resultado"] == "entrou":
            manteve += 1
            continue
        bloqueou += 1
        if chave not in desfecho:
            sem_desfecho += 1        # trade A ainda aberto / não fechou no período
            continue
        usd = desfecho[chave]
        if usd < 0:
            ruins_evitados += 1
            usd_evitado += -usd      # prejuízo que o filtro evitou (positivo)
        elif usd > 0:
            bons_perdidos += 1
            usd_perdido += usd       # lucro que o filtro deixou na mesa
    return {
        "c_manteve": manteve,
        "c_bloqueou": bloqueou,
        "ruins_evitados": ruins_evitados,
        "bons_perdidos": bons_perdidos,
        "bloqueio_sem_desfecho": sem_desfecho,
        "usd_evitado": round(usd_evitado, 2),
        "usd_perdido": round(usd_perdido, 2),
        "beneficio_liquido": round(usd_evitado - usd_perdido, 2),   # >0 = o filtro ajudou
    }


def distribuicao_bloqueio(conn, de_e, ate_e, mercado="forex") -> list:
    """Por que a Variante C bloqueou: contagem dos motivos (vetos fuzzy) das decisões C
    'nao_entrou' no período, normalizados e ordenados por frequência."""
    dcond, dargs = ["variante='C_HIBRIDA'", "resultado='nao_entrou'", _filtro_mercado(mercado)], []
    if de_e:
        dcond.append("time_utc >= ?"); dargs.append(de_e)
    if ate_e:
        dcond.append("time_utc <= ?"); dargs.append(ate_e)
    dwhere = " AND ".join(dcond)
    cont: dict = {}
    for r in conn.execute(f"SELECT motivo FROM decisoes WHERE {dwhere}", dargs):
        cont[_normalizar_motivo(r["motivo"])] = cont.get(_normalizar_motivo(r["motivo"]), 0) + 1
    linhas = [{"motivo": k, "n": v} for k, v in cont.items()]
    linhas.sort(key=lambda x: -x["n"])
    return linhas


# --------------------------------------------------------------------------- #
# Split-half — a expectância é ESTÁVEL nas duas metades do período?
# --------------------------------------------------------------------------- #
def split_half(trades: list, min_sinais: int) -> list:
    """Divide o período ao meio (pela mediana de fechamento) e compara a expectância R de cada
    (variante × estratégia × TF) nas DUAS metades. `estavel=True` se ambas têm o MESMO sinal de
    expectância e amostra mínima em cada metade (edge robusto × sorte de uma metade)."""
    fechados = sorted([t for t in trades if t["fechamento_utc"]], key=lambda t: t["fechamento_utc"])
    if len(fechados) < 4:
        return []
    corte = fechados[len(fechados) // 2]["fechamento_utc"]
    meia = max(5, min_sinais // 2)      # amostra mínima POR metade
    grupos: dict = {}
    for t in fechados:
        chave = (t["variante"], t["estrategia"], t["tf"])
        metade = 0 if t["fechamento_utc"] < corte else 1
        grupos.setdefault(chave, ([], []))[metade].append(t)
    linhas = []
    for (var, est, tf), (h1, h2) in grupos.items():
        a1, a2 = _agg(h1), _agg(h2)
        e1, e2 = a1["exp_r"], a2["exp_r"]
        # "Estável" = EDGE estável: positiva nas DUAS metades (mesmo critério do gate). O
        # "mesmo sinal" antigo marcava ✅ até célula consistentemente PERDEDORA (ambas < 0).
        estavel = (e1 is not None and e2 is not None and a1["n"] >= meia and a2["n"] >= meia
                   and e1 > 0 and e2 > 0)
        linhas.append({
            "variante": var, "variante_nome": config.nome_variante(var),
            "estrategia_nome": config.nome_estrategia(est), "tf": tf,
            "chave": f"{config.nome_variante(var)} · {config.nome_estrategia(est)} · {tf}",
            "n1": a1["n"], "exp_r1": e1, "n2": a2["n"], "exp_r2": e2, "estavel": estavel,
        })
    # relevantes primeiro: as com amostra nas duas metades, estáveis no topo.
    linhas.sort(key=lambda x: (x["n1"] < meia or x["n2"] < meia, not x["estavel"]))
    return linhas


# --------------------------------------------------------------------------- #
# Montagem
# --------------------------------------------------------------------------- #
def montar_relatorio(conn, de: str = "", ate: str = "", min_sinais: int = None,
                     mercado: str = "forex") -> dict:
    """Relatório sombra multi-variante completo do intervalo [de, ate], de UM mercado
    (default forex — B3 tem P&L em BRL e nunca se mistura ao livro USD)."""
    min_sinais = min_sinais if min_sinais is not None else config.RELATORIO_MIN_SINAIS
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    trades = _carregar_trades(conn, de_e, ate_e, simulado=1, mercado=mercado)
    return {
        "periodo": {"de": de or "início", "ate": ate or "hoje"},
        "min_sinais": min_sinais,
        "mercado": mercado,
        "geral": _agg(trades),
        "por_variante": por_variante(trades),
        "ranking": ranking_celulas(trades, min_sinais),
        "heatmap": heatmap_estrategia_tf(trades),
        "a_vs_c": a_vs_c(conn, de_e, ate_e, mercado=mercado),
        "distribuicao_bloqueio": distribuicao_bloqueio(conn, de_e, ate_e, mercado=mercado),
        "split_half": split_half(trades, min_sinais),
    }


# --------------------------------------------------------------------------- #
# Renderização em texto (copiar / Telegram)
# --------------------------------------------------------------------------- #
def _r(v):
    return "—" if v is None else f"{v:+.3f}"


def relatorio_texto(d: dict) -> str:
    """Bloco Markdown do relatório — auditável, pronto para copiar ou revisar."""
    L = []
    per = d["periodo"]
    g = d["geral"]
    L.append("# RELATÓRIO SOMBRA MULTI-VARIANTE")
    L.append(f"Período: {per['de']} → {per['ate']} · mín. {d['min_sinais']} sinais/célula")
    L.append(f"Sombra (estudo): {g['n']} trades · winrate {g['winrate']}% · exp {_r(g['exp_r'])} R "
             f"({g['exp_usd']} USD/trade) · PF {g['profit_factor']} · USD {g['usd']}")
    L.append("")
    L.append("## Por variante (A controle · B fuzzy puro · C híbrida)")
    L.append("variante | n | winrate | exp R | exp USD | PF | USD | maxDD")
    for v in d["por_variante"]:
        L.append(f"{v['variante_nome']} | {v['n']} | {v['winrate']}% | {_r(v['exp_r'])} | "
                 f"{v['exp_usd']} | {v['profit_factor']} | {v['usd']} | {v['max_dd']}")
    L.append("")
    L.append("## Efeito do filtro fuzzy (A vs C)")
    ac = d["a_vs_c"]
    L.append(f"A tomou; a C manteve {ac['c_manteve']} e BLOQUEOU {ac['c_bloqueou']} setups.")
    L.append(f"Dos bloqueados: {ac['ruins_evitados']} eram PERDEDORES (prejuízo evitado "
             f"{ac['usd_evitado']} USD) e {ac['bons_perdidos']} eram VENCEDORES (lucro perdido "
             f"{ac['usd_perdido']} USD); {ac['bloqueio_sem_desfecho']} sem desfecho ainda.")
    L.append(f"Benefício líquido do filtro: {ac['beneficio_liquido']} USD "
             f"({'ajudou' if ac['beneficio_liquido'] > 0 else 'atrapalhou/neutro'}).")
    if d["distribuicao_bloqueio"]:
        motivos = " · ".join(f"{x['motivo']} ({x['n']})" for x in d["distribuicao_bloqueio"][:6])
        L.append(f"Motivos de bloqueio: {motivos}")
    L.append("")
    L.append("## Ranking de células (variante × estratégia × TF × par) — por expectância R")
    L.append("Só as com amostra suficiente contam como conclusão (as demais = 'amostra pequena').")
    L.append("célula | n | exp R | PF | winrate | USD | amostra")
    for x in d["ranking"][:25]:
        amostra = "≥mín ✅" if x["suficiente"] else "pequena"
        L.append(f"{x['chave']} | {x['n']} | {_r(x['exp_r'])} | {x['profit_factor']} | "
                 f"{x['winrate']}% | {x['usd']} | {amostra}")
    L.append("")
    L.append("## Split-half (a expectância se mantém nas duas metades do período?)")
    L.append("chave | n1 | exp R (1ª) | n2 | exp R (2ª) | estável")
    for x in d["split_half"][:20]:
        L.append(f"{x['chave']} | {x['n1']} | {_r(x['exp_r1'])} | {x['n2']} | {_r(x['exp_r2'])} | "
                 f"{'SIM ✅' if x['estavel'] else 'não'}")
    L.append("")
    L.append("— fim. Critério de aprovação para demo (ETAPA 9): exp R > 0 com N ≥ 50, PF ≥ 1,3 e "
             "ESTÁVEL no split-half. Nunca calibrar e validar no mesmo período.")
    return "\n".join(L)


def resumo_curto(d: dict) -> str:
    """Resumo de 1 bloco (Telegram) — o essencial do relatório da semana."""
    g, ac = d["geral"], d["a_vs_c"]
    L = [f"📊 <b>Relatório sombra</b> ({d['periodo']['de']} → {d['periodo']['ate']})",
         f"Sombra: {g['n']} trades · exp {_r(g['exp_r'])} R · PF {g['profit_factor']} · USD {g['usd']}"]
    for v in d["por_variante"]:
        L.append(f"• {v['variante_nome']}: n {v['n']} · exp {_r(v['exp_r'])} R · USD {v['usd']}")
    L.append(f"Filtro fuzzy (A→C): bloqueou {ac['c_bloqueou']} · evitou {ac['ruins_evitados']} "
             f"perdas / perdeu {ac['bons_perdidos']} ganhos · líquido {ac['beneficio_liquido']} USD")
    top = next((x for x in d["ranking"] if x["suficiente"]), None)
    if top:
        L.append(f"🏆 Melhor célula (amostra ok): {top['chave']} · exp {_r(top['exp_r'])} R (n {top['n']})")
    return "\n".join(L)


def resumo_semanal(conn) -> dict:
    """Relatório dos últimos 7 dias e envio do resumo curto ao Telegram (resumo semanal do doc)."""
    hoje = datetime.now(timezone.utc).date()
    de = (hoje - timedelta(days=7)).isoformat()
    d = montar_relatorio(conn, de, hoje.isoformat())
    telegram_notif.enviar(resumo_curto(d), chave_antispam="relatorio_semanal")
    return d


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    args = [a for a in sys.argv[1:]]
    como_json = "--json" in args
    args = [a for a in args if not a.startswith("--")]
    if args and args[0] == "semanal":
        with db.sessao() as conn:
            d = resumo_semanal(conn)
        print(json.dumps(d, ensure_ascii=False, indent=2) if como_json else resumo_curto(d))
        return
    de = args[0] if len(args) > 0 else ""
    ate = args[1] if len(args) > 1 else ""
    with db.sessao() as conn:
        d = montar_relatorio(conn, de, ate)
    print(json.dumps(d, ensure_ascii=False, indent=2) if como_json else relatorio_texto(d))


if __name__ == "__main__":
    main()
