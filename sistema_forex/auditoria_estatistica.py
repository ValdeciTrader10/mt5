"""ETAPA 9 — Auditoria estatística: gate de aprovação por célula (sombra → demo).

Culminação do laboratório. Lê o livro SOMBRA fechado e decide, POR DADOS, quais células
(variante × estratégia × par × TF) têm edge REAL o bastante para serem promovidas ao executor
DEMO. É um gate deliberadamente CONSERVADOR — o custo de um falso-positivo é arriscar capital
numa combinação que só teve sorte.

Critério de aprovação (doc-mestre Parte 8/10 + skill `trading-quant-expert` §5):
  1. AMOSTRA:       N ≥ `APROVACAO_MIN_SINAIS` (default 50). Winrate de 5 trades não é winrate.
  2. EXPECTÂNCIA:   exp R > 0 — a métrica-mãe (ganha em múltiplos de R, já líquida do spread na
                    simulação de resultado).
  3. PROFIT FACTOR: PF ≥ `APROVACAO_PF_MIN` (default 1,3) = ganho bruto / |perda bruta|.
  4. SPLIT-HALF:    exp R POSITIVA nas DUAS metades do período, com amostra em cada — o guardião
                    anti-sorte (edge estável × uma metade sortuda).

ARMADILHA DE MÚLTIPLOS TESTES (skill §5 — Deflated Sharpe / data-snooping): o laboratório testa
CENTENAS de células ao mesmo tempo (3 variantes × 9 estratégias × 3 TFs × N pares). Ao acaso, ~5%
das células com amostra "passariam" num teste único. Por isso (a) o split-half é OBRIGATÓRIO — uma
célula sortuda raramente repete o sinal nas DUAS metades — e (b) o relatório mostra
`falsos_esperados ≈ testadas × APROVACAO_PROB_ACASO`; se as aprovadas não superam FOLGADAMENTE esse
número, o conjunto é suspeito.

Nada aqui LIGA execução sozinho. A saída é a LISTA de células aprovadas + a config sugerida; promover
para ordens no demo é passo MANUAL do dono (env no Dokploy). "Demo/sombra primeiro; nunca real antes
de demo auditada."

    python -m sistema_forex.auditoria_estatistica [de] [ate] [--json]
"""

import json
import logging
import sys

from . import config, db
from .auditoria import _epoch
from .relatorio import _agg, _carregar_trades

log = logging.getLogger("auditoria_estatistica")


# --------------------------------------------------------------------------- #
# Split-half de UMA célula (o guardião anti-sorte)
# --------------------------------------------------------------------------- #
def _split_half_celula(trades: list, min_por_metade: int) -> dict:
    """Divide os trades FECHADOS da célula pela mediana do fechamento e mede a expectância R de
    cada metade. `estavel=True` se AMBAS são positivas com amostra mínima em cada. `avaliavel=False`
    quando não há trades suficientes para dividir (não dá para concluir estabilidade)."""
    fechados = sorted([t for t in trades if t.get("fechamento_utc")], key=lambda t: t["fechamento_utc"])
    if len(fechados) < 2 * min_por_metade:
        return {"n1": 0, "exp_r1": None, "n2": 0, "exp_r2": None, "estavel": False, "avaliavel": False}
    corte = fechados[len(fechados) // 2]["fechamento_utc"]
    h1 = [t for t in fechados if t["fechamento_utc"] < corte]
    h2 = [t for t in fechados if t["fechamento_utc"] >= corte]
    a1, a2 = _agg(h1), _agg(h2)
    e1, e2 = a1["exp_r"], a2["exp_r"]
    estavel = (e1 is not None and e2 is not None and a1["n"] >= min_por_metade
               and a2["n"] >= min_por_metade and e1 > 0 and e2 > 0)
    return {"n1": a1["n"], "exp_r1": e1, "n2": a2["n"], "exp_r2": e2,
            "estavel": estavel, "avaliavel": True}


def _pf_ok(pf, exp_r, pf_min: float) -> bool:
    """PF passa se ≥ mínimo. Sem trades perdedores (`pf is None`) → PF "infinito": passa desde que
    a expectância seja positiva (uma célula só de ganhos é excelente, não suspeita por PF)."""
    if pf is None:
        return (exp_r or 0) > 0
    return pf >= pf_min


# --------------------------------------------------------------------------- #
# Gate por célula (PURO — testável com trades conhecidos)
# --------------------------------------------------------------------------- #
def avaliar_celula(trades: list, *, min_sinais: int, pf_min: float, exige_split: bool,
                   min_por_metade: int = None) -> dict:
    """Aplica o gate de aprovação a UMA célula (lista de trades da mesma variante×estratégia×TF×par).
    Devolve os KPIs, os 4 critérios (aprovou/não), o veredito e a confiança. Função PURA."""
    min_por_metade = min_por_metade if min_por_metade is not None else max(10, min_sinais // 4)
    kpi = _agg(trades)
    split = _split_half_celula(trades, min_por_metade)

    # N honesto = trades COM r calculável (n_com_r): a exp_r é a média só deles — contar
    # trades sem r no critério de amostra deixaria uma célula passar o N≥50 com a expectância
    # calculada sobre menos observações do que o gate exige.
    c_amostra = kpi["n_com_r"] >= min_sinais
    c_exp = kpi["exp_r"] is not None and kpi["exp_r"] > 0
    c_pf = _pf_ok(kpi["profit_factor"], kpi["exp_r"], pf_min)
    c_split = split["estavel"] if exige_split else True
    criterios = {"amostra": c_amostra, "expectancia": c_exp,
                 "profit_factor": c_pf, "split_half": c_split}
    aprovada = all(criterios.values())

    motivos = []
    if not c_amostra:
        motivos.append(f"amostra {kpi['n_com_r']} < {min_sinais}")
    if not c_exp:
        motivos.append("expectância R ≤ 0")
    if not c_pf:
        motivos.append(f"PF {kpi['profit_factor']} < {pf_min}")
    if exige_split and not c_split:
        motivos.append("instável no split-half" if split["avaliavel"] else "sem amostra p/ split-half")

    # Confiança: 'alta' só quando a amostra é robusta (≥2×mín) E o split-half segura; senão 'média'.
    confianca = None
    if aprovada:
        confianca = "alta" if (kpi["n"] >= 2 * min_sinais and split["estavel"]) else "média"

    return {**kpi, "split": split, "criterios": criterios, "aprovada": aprovada,
            "motivos_reprova": motivos, "confianca": confianca}


# --------------------------------------------------------------------------- #
# Config sugerida para promoção (NÃO liga nada — sugestão para o dono)
# --------------------------------------------------------------------------- #
def _config_sugerida(aprovadas: list) -> dict:
    """Env sugerida para o livro DEMO curado a partir das células aprovadas. Hoje o executor só
    dispara gêmeos reais da Variante A (controle) — logo a config cobre as células A aprovadas; as
    aprovadas em B/C ficam listadas à parte (promovê-las exige fiar o executor por variante — futuro).
    ⚠️ O livro curado combina estratégias×TFs em PRODUTO (`combo_real`), então o env pode cobrir
    pares (estratégia,TF) além dos aprovados — a lista exata está em `promovivel_agora`."""
    a = [c for c in aprovadas if c["variante"] == "A_ORIGINAL"]
    estrategias = sorted({c["estrategia"] for c in a})
    tfs = sorted({c["tf"] for c in a})
    outras = [c["chave"] for c in aprovadas if c["variante"] != "A_ORIGINAL"]
    return {
        "promovivel_agora": [c["chave"] for c in a],
        "EXEC_REAL_ESTRATEGIAS": ",".join(estrategias),
        "EXEC_REAL_TFS": ",".join(tfs),
        "aprovadas_outras_variantes": outras,
    }


# --------------------------------------------------------------------------- #
# Auditoria completa do intervalo
# --------------------------------------------------------------------------- #
def auditar(conn, de: str = "", ate: str = "", *, min_sinais: int = None, pf_min: float = None,
            exige_split: bool = None, mercado: str = "forex") -> dict:
    """Roda o gate sobre TODAS as células do livro sombra fechado no intervalo e monta o veredito
    (aprovadas, testadas, nota de múltiplos testes, config sugerida). SÓ um mercado por vez:
    a config sugerida alimenta o executor curado do FOREX — uma célula da B3 (BRL, pregão
    próprio) jamais pode promover estratégia/TF para o livro real do forex."""
    min_sinais = config.APROVACAO_MIN_SINAIS if min_sinais is None else min_sinais
    pf_min = config.APROVACAO_PF_MIN if pf_min is None else pf_min
    exige_split = config.APROVACAO_EXIGE_SPLIT_HALF if exige_split is None else exige_split
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    trades = _carregar_trades(conn, de_e, ate_e, simulado=1, mercado=mercado)

    grupos: dict = {}
    for t in trades:
        grupos.setdefault((t["variante"], t["estrategia"], t["tf"], t["par"]), []).append(t)

    celulas = []
    for (var, est, tf, par), ts in grupos.items():
        v = avaliar_celula(ts, min_sinais=min_sinais, pf_min=pf_min, exige_split=exige_split)
        celulas.append({
            "variante": var, "variante_nome": config.nome_variante(var),
            "estrategia": est, "estrategia_nome": config.nome_estrategia(est),
            "tf": tf, "par": par,
            "chave": f"{config.nome_variante(var)} · {config.nome_estrategia(est)} · {tf} · {par}",
            **v,
        })

    # "Testadas" = as que têm amostra suficiente (as únicas onde uma aprovação faz sentido).
    testadas = [c for c in celulas if c["criterios"]["amostra"]]
    aprovadas = [c for c in celulas if c["aprovada"]]
    falsos_esperados = round(len(testadas) * config.APROVACAO_PROB_ACASO, 1)

    ordem_conf = {"alta": 0, "média": 1, None: 2}
    celulas.sort(key=lambda c: (not c["aprovada"], ordem_conf[c["confianca"]],
                                -(c["exp_r"] if c["exp_r"] is not None else -9)))
    aprovadas.sort(key=lambda c: (ordem_conf[c["confianca"]],
                                  -(c["exp_r"] if c["exp_r"] is not None else -9)))

    return {
        "periodo": {"de": de or "início", "ate": ate or "hoje"},
        "criterio": {"min_sinais": min_sinais, "pf_min": pf_min, "exige_split": exige_split,
                     "prob_acaso": config.APROVACAO_PROB_ACASO},
        "n_celulas": len(celulas),
        "n_testadas": len(testadas),
        "n_aprovadas": len(aprovadas),
        "falsos_esperados": falsos_esperados,
        # Alerta quando as aprovadas não superam o que passaria por puro acaso.
        "multiple_testing_alerta": len(aprovadas) <= falsos_esperados and len(aprovadas) > 0,
        "celulas": celulas,
        "aprovadas": aprovadas,
        "config_sugerida": _config_sugerida(aprovadas),
    }


# --------------------------------------------------------------------------- #
# Renderização em texto (copiar / CLI)
# --------------------------------------------------------------------------- #
def _r(v):
    return "—" if v is None else f"{v:+.3f}"


def auditoria_texto(d: dict) -> str:
    """Bloco Markdown do veredito — auditável, pronto para copiar."""
    cr = d["criterio"]
    L = ["# AUDITORIA ESTATÍSTICA — GATE DE APROVAÇÃO (SOMBRA → DEMO)"]
    L.append(f"Período: {d['periodo']['de']} → {d['periodo']['ate']}")
    L.append(f"Critério: exp R > 0 · N ≥ {cr['min_sinais']} · PF ≥ {cr['pf_min']}"
             + (" · ESTÁVEL no split-half" if cr["exige_split"] else ""))
    L.append(f"Células: {d['n_celulas']} no total · {d['n_testadas']} com amostra · "
             f"**{d['n_aprovadas']} APROVADAS**")
    L.append(f"⚠️ Múltiplos testes: ~{d['falsos_esperados']} célula(s) passariam por ACASO "
             f"({d['n_testadas']} testadas × {cr['prob_acaso']}). "
             + ("As aprovadas NÃO superam o acaso — desconfie do conjunto e deixe rodar mais."
                if d["multiple_testing_alerta"] else
                "As aprovadas superam o esperado por acaso." if d["n_aprovadas"] else
                "Nada aprovado ainda."))
    L.append("")
    L.append("## Células aprovadas")
    if d["aprovadas"]:
        L.append("célula | n | exp R | PF | split R (1ª/2ª) | confiança")
        for c in d["aprovadas"]:
            s = c["split"]
            L.append(f"{c['chave']} | {c['n']} | {_r(c['exp_r'])} | {c['profit_factor']} | "
                     f"{_r(s['exp_r1'])}/{_r(s['exp_r2'])} | {c['confianca']}")
    else:
        L.append(f"Nenhuma célula aprovada ainda — deixe a sombra acumular sinais "
                 f"(N ≥ {cr['min_sinais']} por célula) e reaudite.")
    L.append("")
    cs = d["config_sugerida"]
    L.append("## Promoção sugerida ao livro DEMO curado (passo MANUAL do dono — env no Dokploy)")
    if cs["promovivel_agora"]:
        L.append(f"Células A promovíveis agora: {', '.join(cs['promovivel_agora'])}")
        L.append(f"EXEC_REAL_ESTRATEGIAS={cs['EXEC_REAL_ESTRATEGIAS']}")
        L.append(f"EXEC_REAL_TFS={cs['EXEC_REAL_TFS']}")
        L.append("(o livro curado combina estratégias×TFs em PRODUTO — confira que só as pares "
                 "acima interessam; ligar exige EXECUCAO_REAL_CURADA=true em conta DEMO.)")
    else:
        L.append("Nada a promover ainda.")
    if cs["aprovadas_outras_variantes"]:
        L.append(f"Aprovadas em B/C (promoção requer fiar o executor por variante — futuro): "
                 f"{', '.join(cs['aprovadas_outras_variantes'])}")
    L.append("")
    L.append("— Nada é ligado automaticamente. 'Demo/sombra primeiro'; nunca real antes de demo "
             "auditada. Nunca calibrar e validar no mesmo período.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    como_json = "--json" in sys.argv[1:]
    de = args[0] if len(args) > 0 else ""
    ate = args[1] if len(args) > 1 else ""
    with db.sessao() as conn:
        d = auditar(conn, de, ate)
    print(json.dumps(d, ensure_ascii=False, indent=2) if como_json else auditoria_texto(d))


if __name__ == "__main__":
    main()
