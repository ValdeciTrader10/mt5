"""Auditoria de calibração — dossiê das operações PERDEDORAS para a IA analisar.

Motivação (pedido do dono, 13/07): "criar uma forma de a IA auditar as operações
perdedoras de forma a ajudar a revisar o que mantém, o que muda, o que retira".

O banco (`mercado.db`) vive na VPS em Docker — o assistente NÃO o acessa direto. Este
módulo resolve isso gerando um DOSSIÊ compacto e completo (JSON + texto) que:
  * agrega as perdedoras por (estratégia × timeframe) com expectância/PF/MAE/MFE;
  * CLASSIFICA cada perda por PADRÃO DE FALHA (usando MAE/MFE em R):
      - `alvo_curto`        : andou ≥ 1R a favor e virou perdedora  → edge existia,
                              a SAÍDA devolveu tudo (calibrar alvo/parcial/trailing);
      - `devolveu_parcial`  : andou 0.5–1R a favor e devolveu       → saída no limite;
      - `entrada_adiantada` : foi contra quase de imediato (MFE baixo, MAE fundo)
                              → revisar o GATILHO / ponto de entrada;
      - `perda_ordenada`    : perda normal, sem excursão a favor    → contexto errado;
      - `sem_dados`         : trade antigo sem MAE/MFE registrado.
  * junta o "porquê entrou" (score/confluências gravados em `decisoes`);
  * emite um bloco de TEXTO que o dono copia do painel (/auditoria) e cola no chat da
    IA — é a "forma de a IA acessar os dados analisados das entradas perdedoras".

A leitura por padrão de falha separa as três decisões que o dono quer tomar:
  MANTÉM  → (estratégia, TF) com expectância positiva ou perdas dominadas por
            `alvo_curto` (o edge existe; o conserto é na saída, não na estratégia).
  MUDA    → perdas concentradas em `alvo_curto`/`devolveu_parcial` → calibrar SAÍDA;
            concentradas em `entrada_adiantada` → calibrar o GATILHO de entrada.
  RETIRA  → expectância negativa E perdas em `perda_ordenada` (sem sinal de que um
            ajuste de saída/entrada salvaria) → edge inexistente naquele (estratégia, TF).

Sem viés look-ahead: só lê trades FECHADOS e seus MAE/MFE já consolidados. Funções puras
testadas em `tests/test_auditoria.py`.

Uso (CLI, roda contra o banco padrão):
    python -m sistema_forex.auditoria                 # todas as datas
    python -m sistema_forex.auditoria 2026-07-01 2026-07-13
    python -m sistema_forex.auditoria --json          # imprime o JSON em vez do texto
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone

from . import config, db, gestao

log = logging.getLogger("auditoria")


# --------------------------------------------------------------------------- #
# Classificação da perda por MAE/MFE (o coração da auditoria)
# --------------------------------------------------------------------------- #
def classificar_perda(mae_r, mfe_r) -> str:
    """Rotula UMA operação perdedora pelo seu padrão de falha (MAE/MFE em R).

    Função pura — a mesma régua da "Leitura" do raio-X, mas agrupável. Ver o docstring do
    módulo para o significado de cada rótulo e a decisão (mantém/muda/retira) que ele sugere.
    """
    if mae_r is None or mfe_r is None:
        return "sem_dados"
    if mfe_r >= 1.0:
        return "alvo_curto"
    if mfe_r >= 0.5:
        return "devolveu_parcial"
    if mae_r <= -0.9 and mfe_r < 0.3:
        return "entrada_adiantada"
    return "perda_ordenada"


FLAGS_ORDEM = ["alvo_curto", "devolveu_parcial", "entrada_adiantada", "perda_ordenada", "sem_dados"]

FLAG_ACAO = {
    "alvo_curto": "MUDAR saída (alvo/parcial/trailing) — o edge existiu",
    "devolveu_parcial": "MUDAR saída — devolveu lucro no limite",
    "entrada_adiantada": "MUDAR gatilho de entrada — foi contra de imediato",
    "perda_ordenada": "sem sinal de conserto — pesa para RETIRAR se a expectância for negativa",
    "sem_dados": "trade sem MAE/MFE — auditar manualmente no raio-X",
}


# --------------------------------------------------------------------------- #
# Helpers de rótulo (independentes do painel para não puxar FastAPI no CLI/tests)
# --------------------------------------------------------------------------- #
def _normalizar_motivo(motivo: str) -> str:
    """Colapsa o motivo de saída para um rótulo estável (remove '(...)', direção e R)."""
    if not motivo:
        return "—"
    m = re.sub(r"\s*\([^)]*\)", "", motivo)
    m = re.sub(r"\s+(alta|baixa)\b", "", m)
    m = re.sub(r"\d+\.?\d*\s*R", "R", m)
    return m.strip() or "—"


def _sessao(hora_utc: int) -> str:
    if 7 <= hora_utc <= 11:
        return "Londres (07–11)"
    if 12 <= hora_utc <= 15:
        return "Londres/NY (12–15)"
    if 16 <= hora_utc <= 20:
        return "Nova York (16–20)"
    if 0 <= hora_utc <= 6:
        return "Ásia (00–06)"
    return "Fora de sessão (21–23)"


def _epoch(data_iso: str, fim: bool = False):
    if not data_iso:
        return None
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if fim:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _media(valores):
    vals = [v for v in valores if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _fmt_ts(ep) -> str:
    return datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%d %H:%M") if ep else "—"


# --------------------------------------------------------------------------- #
# Agregação
# --------------------------------------------------------------------------- #
def _agregar(trades: list) -> dict:
    """KPIs de um conjunto de trades (todos, para expectância/PF por grupo)."""
    n = len(trades)
    ganhos = [t for t in trades if (t["lucro_usd"] or 0) > 0]
    perdas = [t for t in trades if (t["lucro_usd"] or 0) < 0]
    bruto_ganho = sum(t["lucro_usd"] or 0 for t in ganhos)
    bruto_perda = sum(t["lucro_usd"] or 0 for t in perdas)  # ≤ 0
    usd = sum(t["lucro_usd"] or 0 for t in trades)
    pf = round(bruto_ganho / abs(bruto_perda), 2) if bruto_perda else None
    return {
        "n": n, "ganhos": len(ganhos), "perdas": len(perdas),
        "winrate": round(100 * len(ganhos) / n, 1) if n else 0.0,
        "usd": round(usd, 2),
        "profit_factor": pf,
        "expectativa": round(usd / n, 2) if n else 0.0,
        "mae_medio": _media([t.get("mae_r") for t in trades]),
        "mfe_medio": _media([t.get("mfe_r") for t in trades]),
        "mfe_perdas": _media([t.get("mfe_r") for t in perdas]),  # lucro que os perdedores devolveram
    }


def _classificacao_grupo(perdedores: list) -> dict:
    """Contagem das flags de falha em um grupo de PERDEDORAS + veredito sugerido."""
    cont = {f: 0 for f in FLAGS_ORDEM}
    for t in perdedores:
        cont[t["flag"]] = cont.get(t["flag"], 0) + 1
    return cont


def _por(perdedores: list, chave: str) -> list:
    """Agrupa PERDEDORAS por uma coluna e conta as flags; ordena por nº de perdas (desc)."""
    grupos: dict = {}
    for t in perdedores:
        grupos.setdefault(t.get(chave) or "—", []).append(t)
    linhas = []
    for k, v in grupos.items():
        linha = {"chave": k, "n_perd": len(v), **_classificacao_grupo(v),
                 "usd_perdido": round(sum(t["lucro_usd"] or 0 for t in v), 2),
                 "mfe_medio": _media([t.get("mfe_r") for t in v])}
        linhas.append(linha)
    linhas.sort(key=lambda x: -x["n_perd"])
    return linhas


def _veredito(kpi: dict, flags: dict) -> str:
    """Sugere MANTÉM / CALIBRA SAÍDA / CALIBRA ENTRADA / RETIRA para um (estratégia, TF)."""
    n_perd = sum(flags.get(f, 0) for f in FLAGS_ORDEM)
    if kpi["n"] < 5:
        return "AMOSTRA PEQUENA — deixar rodar"
    saida = flags.get("alvo_curto", 0) + flags.get("devolveu_parcial", 0)
    entrada = flags.get("entrada_adiantada", 0)
    if kpi["expectativa"] > 0:
        return "MANTÉM — expectância positiva"
    if n_perd and saida >= max(entrada, 1) and saida / n_perd >= 0.4:
        return "CALIBRA SAÍDA — edge existe, devolve lucro"
    if n_perd and entrada > saida and entrada / n_perd >= 0.4:
        return "CALIBRA ENTRADA — gatilho adiantado"
    return "RETIRA? — expectância negativa sem sinal de conserto"


# --------------------------------------------------------------------------- #
# Montagem do dossiê
# --------------------------------------------------------------------------- #
def _buscar_perdedores(conn, de_e, ate_e, limite_detalhe):
    """Lê trades fechados no intervalo e devolve (todos_dict, perdedores_enriquecidos)."""
    cond, args = ["fechamento_utc IS NOT NULL"], []
    if de_e:
        cond.append("fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("fechamento_utc <= ?"); args.append(ate_e)
    where = " AND ".join(cond)
    rows = conn.execute(
        f"SELECT id, par, tf, estrategia, direcao, pips, lucro_usd, motivo_saida, "
        f"preco_entrada, preco_saida, risco_inicial, mae_r, mfe_r, regime_entrada, "
        f"abertura_utc, fechamento_utc FROM trades WHERE {where} ORDER BY fechamento_utc DESC",
        args,
    ).fetchall()
    trades = [dict(r) for r in rows]
    for t in trades:
        t["tf"] = t["tf"] or "M5"

    perdedores = [t for t in trades if (t["lucro_usd"] or 0) < 0]
    for i, t in enumerate(perdedores):
        t["flag"] = classificar_perda(t.get("mae_r"), t.get("mfe_r"))
        t["r_result"] = _res_r(t)
        h = datetime.fromtimestamp(t["abertura_utc"], timezone.utc).hour if t["abertura_utc"] else 0
        t["sessao"] = _sessao(h)
        t["regime"] = t.get("regime_entrada") or "—"
        t["motivo_norm"] = _normalizar_motivo(t.get("motivo_saida"))
        t["par_tf"] = f"{t['par']} {t['tf']}"
        t["est_tf"] = f"{config.nome_estrategia(t['estrategia'])} · {t['tf']}"
        # "porquê entrou" só para os mais recentes (o join é o caro; detalhe é limitado)
        t["ctx"] = _contexto_decisao(conn, t) if i < limite_detalhe else None
    return trades, perdedores


def _res_r(t) -> float:
    risco = t.get("risco_inicial")
    if not risco or t.get("preco_saida") is None:
        return None
    return round(gestao.r_por_risco(t["direcao"], t["preco_entrada"], t["preco_saida"], risco), 2)


def _contexto_decisao(conn, t):
    """Recupera score/confluências da decisão que abriu o trade (mesmo casamento do raio-X)."""
    r = conn.execute(
        "SELECT motivo, dados_json FROM decisoes WHERE par=? AND tf=? AND estrategia=? "
        "AND direcao=? AND resultado='entrou' AND time_utc<=? ORDER BY time_utc DESC LIMIT 1",
        (t["par"], t["tf"], t["estrategia"], t["direcao"], (t["abertura_utc"] or 0) + 120),
    ).fetchone()
    if not r:
        return None
    dados = {}
    try:
        dados = json.loads(r["dados_json"] or "{}")
    except Exception:  # noqa: BLE001
        pass
    return {"score": dados.get("score"), "confluencias": dados.get("confluencias") or [],
            "regime": dados.get("regime"), "motivo": r["motivo"]}


def _por_estrategia_tf(trades: list, perdedores: list) -> list:
    """Cruzamento (estratégia × TF): KPIs de TODOS os trades + flags das perdas + veredito."""
    grupos: dict = {}
    for t in trades:
        chave = (config.nome_estrategia(t["estrategia"]), t["tf"])
        grupos.setdefault(chave, {"todos": [], "perd": []})["todos"].append(t)
    for t in perdedores:
        grupos[(config.nome_estrategia(t["estrategia"]), t["tf"])]["perd"].append(t)
    linhas = []
    for (nome, tf), g in grupos.items():
        kpi = _agregar(g["todos"])
        flags = _classificacao_grupo(g["perd"])
        linhas.append({
            "chave": f"{nome} · {tf}", "estrategia": nome, "tf": tf,
            **kpi, **{f"perd_{k}": v for k, v in flags.items()},
            "veredito": _veredito(kpi, flags),
        })
    linhas.sort(key=lambda x: x["usd"])  # pior → melhor
    return linhas


def dossie_perdedores(conn, de: str = "", ate: str = "", limite_detalhe: int = 60) -> dict:
    """Monta o dossiê completo de auditoria das perdedoras no intervalo [de, ate]."""
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    trades, perdedores = _buscar_perdedores(conn, de_e, ate_e, limite_detalhe)

    flags_totais = _classificacao_grupo(perdedores)
    detalhe = [{
        "id": t["id"], "quando": _fmt_ts(t["fechamento_utc"]),
        "par": t["par"], "tf": t["tf"], "estrategia": config.nome_estrategia(t["estrategia"]),
        "direcao": t["direcao"], "pips": t.get("pips"), "usd": t.get("lucro_usd"),
        "R": t.get("r_result"), "mae_r": t.get("mae_r"), "mfe_r": t.get("mfe_r"),
        "flag": t["flag"], "regime": t["regime"], "sessao": t["sessao"],
        "motivo_saida": t.get("motivo_saida"),
        "score": (t["ctx"] or {}).get("score"),
        "confluencias": (t["ctx"] or {}).get("confluencias") or [],
    } for t in perdedores[:limite_detalhe]]

    return {
        "periodo": {"de": de or "início", "ate": ate or "hoje"},
        "resumo": {**_agregar(trades), "n_perdedoras": len(perdedores)},
        "flags_perdedoras": {"total": len(perdedores),
                             **{f: flags_totais[f] for f in FLAGS_ORDEM}},
        "acao_por_flag": FLAG_ACAO,
        "por_estrategia_tf": _por_estrategia_tf(trades, perdedores),
        "perdas_por_regime": _por(perdedores, "regime"),
        "perdas_por_sessao": _por(perdedores, "sessao"),
        "perdas_por_par_tf": _por(perdedores, "par_tf"),
        "perdas_por_motivo": _por(perdedores, "motivo_norm"),
        "perdedores": detalhe,
        "detalhe_limitado_a": limite_detalhe,
    }


# --------------------------------------------------------------------------- #
# Renderização em TEXTO — o bloco que o dono cola no chat da IA
# --------------------------------------------------------------------------- #
def dossie_texto(d: dict) -> str:
    """Bloco Markdown compacto e auto-explicado do dossiê, pronto para colar na IA."""
    L = []
    per = d["periodo"]
    r = d["resumo"]
    L.append("# DOSSIÊ DE CALIBRAÇÃO — OPERAÇÕES PERDEDORAS (sombra)")
    L.append(f"Período: {per['de']} → {per['ate']}")
    L.append("")
    L.append("## Como ler (para a IA)")
    L.append("Objetivo: decidir por (estratégia × TF) o que MANTÉM, o que MUDA (saída ou "
             "gatilho) e o que RETIRA. Cada perda é classificada pela excursão em R:")
    for f in FLAGS_ORDEM:
        L.append(f"  - `{f}`: {FLAG_ACAO[f]}")
    L.append("MAE = pior R contra durante a vida (≤0). MFE = melhor R a favor (≥0). "
             "R = resultado final em múltiplos do risco inicial.")
    L.append("")
    L.append("## Resumo do período")
    L.append(f"Trades fechados: {r['n']} · ganhos {r['ganhos']} · perdas {r['perdas']} · "
             f"winrate {r['winrate']}% · USD {r['usd']} · PF {r['profit_factor']} · "
             f"expectativa {r['expectativa']}/trade")
    L.append(f"MFE médio das PERDEDORAS: {r['mfe_perdas']} R "
             "(quanto o preço andou a favor antes de a perda se formar — quanto maior, mais "
             "lucro foi devolvido → suspeita de saída mal calibrada)")
    L.append("")
    fp = d["flags_perdedoras"]
    L.append("## Perdedoras por padrão de falha")
    L.append(f"Total perdedoras: {fp['total']}")
    for f in FLAGS_ORDEM:
        if fp[f]:
            L.append(f"  - {f}: {fp[f]}")
    L.append("")
    L.append("## Por estratégia × timeframe (pior → melhor por USD)")
    L.append("chave | n | wr% | PF | exp | USD | MAEméd | MFEméd | perdas(alvo_curto/devolveu/"
             "adiantada/ordenada) | VEREDITO")
    for x in d["por_estrategia_tf"]:
        perdas = (f"{x['perd_alvo_curto']}/{x['perd_devolveu_parcial']}/"
                  f"{x['perd_entrada_adiantada']}/{x['perd_perda_ordenada']}")
        L.append(f"{x['chave']} | {x['n']} | {x['winrate']} | {x['profit_factor']} | "
                 f"{x['expectativa']} | {x['usd']} | {x['mae_medio']} | {x['mfe_medio']} | "
                 f"{perdas} | {x['veredito']}")
    L.append("")

    def _bloco_grupo(titulo, linhas):
        L.append(f"## {titulo}")
        L.append("chave | perdas | alvo_curto | devolveu | adiantada | ordenada | MFEméd | USD perdido")
        for x in linhas:
            L.append(f"{x['chave']} | {x['n_perd']} | {x['alvo_curto']} | "
                     f"{x['devolveu_parcial']} | {x['entrada_adiantada']} | "
                     f"{x['perda_ordenada']} | {x['mfe_medio']} | {x['usd_perdido']}")
        L.append("")

    _bloco_grupo("Perdas por regime na entrada", d["perdas_por_regime"])
    _bloco_grupo("Perdas por sessão", d["perdas_por_sessao"])
    _bloco_grupo("Perdas por par × TF", d["perdas_por_par_tf"])
    _bloco_grupo("Perdas por motivo de saída", d["perdas_por_motivo"])

    L.append(f"## Perdedoras (detalhe — até {d['detalhe_limitado_a']} mais recentes)")
    L.append("id | quando | par | tf | estratégia | dir | pips | USD | R | MAE | MFE | "
             "flag | regime | sessão | score | confluências | motivo saída")
    for t in d["perdedores"]:
        confl = "; ".join(str(c) for c in t["confluencias"]) or "—"
        L.append(f"{t['id']} | {t['quando']} | {t['par']} | {t['tf']} | {t['estrategia']} | "
                 f"{t['direcao']} | {t['pips']} | {t['usd']} | {t['R']} | {t['mae_r']} | "
                 f"{t['mfe_r']} | {t['flag']} | {t['regime']} | {t['sessao']} | "
                 f"{t['score']} | {confl} | {t['motivo_saida']}")
    L.append("")
    L.append("— fim do dossiê. Peça à IA: 'analise e diga, por (estratégia × TF), o que manter, "
             "o que calibrar (saída vs. gatilho) e o que retirar, justificando pelos padrões de falha'.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    args = [a for a in sys.argv[1:]]
    como_json = "--json" in args
    args = [a for a in args if not a.startswith("--")]
    de = args[0] if len(args) > 0 else ""
    ate = args[1] if len(args) > 1 else ""
    with db.sessao() as conn:
        d = dossie_perdedores(conn, de, ate)
    if como_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(dossie_texto(d))


if __name__ == "__main__":
    main()
