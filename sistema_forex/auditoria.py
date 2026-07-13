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

from . import analise, config, db, gestao

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
        f"preco_entrada, preco_saida, sl_servidor, risco_inicial, mae_r, mfe_r, regime_entrada, "
        f"abertura_utc, fechamento_utc, decisao_id FROM trades WHERE {where} ORDER BY fechamento_utc DESC",
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
    """Recupera score/confluências da decisão que abriu o trade. Casa DIRETO pela FK
    `decisao_id` quando o trade a tem (exato); senão cai na heurística por (par, tf,
    estratégia, direção) + janela de tempo (trades antigos sem FK)."""
    r = None
    if t.get("decisao_id"):
        r = conn.execute("SELECT motivo, dados_json FROM decisoes WHERE id=?",
                         (t["decisao_id"],)).fetchone()
    if r is None:
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


def dossie_perdedores(conn, de: str = "", ate: str = "", limite_detalhe: int = 60,
                      raiox_trades: int = None) -> dict:
    """Monta o dossiê completo de auditoria das perdedoras no intervalo [de, ate].

    `raiox_trades` = quantas das perdedoras mais recentes recebem o RAIO-X TEXTUAL embutido
    (candles em pips) para a IA ler o price action; o resto sai sob demanda em /api/raiox/{id}."""
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    trades, perdedores = _buscar_perdedores(conn, de_e, ate_e, limite_detalhe)
    raiox_trades = raiox_trades if raiox_trades is not None else config.AUDITORIA_RAIOX_TRADES

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

    # Raio-x textual (candles em pips) das K perdedoras mais recentes — a leitura de gráfico.
    raiox = [raiox_dados(conn, t) for t in perdedores[:raiox_trades]]

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
        "raiox": raiox,
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

    # Raio-x textual (candles em pips) — a "visão do gráfico" para a IA ler o price action.
    raiox = d.get("raiox") or []
    if raiox:
        L.append("## Raio-X das perdedoras (gráfico em texto — leia o price action)")
        L.append(f"As {len(raiox)} perdedoras mais recentes com os candles em pips relativos à "
                 "entrada. Use para julgar o STOP REAL (furou por ruído?), se o padrão confirmou "
                 "antes da entrada, se a entrada foi adiantada e o que o preço fez após a saída.")
        L.append("")
        for r in raiox:
            L.append(raiox_texto(r))
            L.append("")

    L.append("— fim do dossiê. Peça à IA: 'analise cada perdedora pelo raio-x (candles) e, por "
             "(estratégia × TF), diga o que manter, o que calibrar (stop / ponto de entrada / "
             "saída) e o que retirar, justificando pelo price action e pelos padrões de falha'.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Raio-X TEXTUAL — os candles do gráfico em pips, para a IA LER o price action
# --------------------------------------------------------------------------- #
# É o que transforma a auditoria de "números" em "leitura de gráfico": para cada perdedora,
# serializa a janela de candles (antes/durante/depois) em PIPS relativos à entrada, marca
# SL/saída e recomputa dos próprios candles os fatos que decidem a análise — furou o stop e
# por quanto (stop no ruído?), quanto andou a favor antes de virar (alvo curto/saída cedo?),
# e o que o preço fez DEPOIS da saída (continuou a favor = stop apertado; seguiu contra =
# entrada/estratégia errada). A IA lê os candles e conclui; aqui só entregamos o gráfico em
# texto + fatos objetivos verificáveis contra os candles.

def _pip_de_trade(t) -> float:
    """Tamanho exato de 1 pip que o sistema usou no trade (back-out de |saída−entrada|/|pips|).

    Usa os pips GRAVADOS (respeitam params por símbolo — JPY, ouro); cai na heurística por
    nº de casas só se faltar pips/saída (ex.: trade aberto)."""
    ent, sai, p = t.get("preco_entrada"), t.get("preco_saida"), t.get("pips")
    if ent is not None and sai is not None and p:
        d = abs(sai - ent) / abs(p)
        if d > 0:
            return d
    return analise._tamanho_pip(ent or 1.0)


def _niveis_perto(niveis, entrada, pip, tf, max_pips=50.0, limite=10):
    """S/R, FVG e OB ativos a até `max_pips` da entrada, com distância em pips (contexto do setup)."""
    perto = []
    for nv in niveis:
        tipo = nv["tipo"]
        preco = nv.get("preco")
        if preco is None:
            continue
        # FVG/OB só do TF do trade (outros são ruído aqui); S/R valem de qualquer TF forte.
        if (tipo.startswith("fvg") or tipo.startswith("ob")) and nv.get("tf_origem") != tf:
            continue
        dist = (preco - entrada) / pip
        if abs(dist) <= max_pips:
            m = nv.get("meta") or {}
            perto.append({"tipo": tipo, "dist_pips": round(dist, 1),
                          "tf": m.get("tf") or nv.get("tf_origem") or "—",
                          "forca": nv.get("forca")})
    perto.sort(key=lambda x: abs(x["dist_pips"]))
    return perto[:limite]


def raiox_dados(conn, t: dict, antes: int = None, depois: int = None) -> dict:
    """Dados estruturados do raio-x textual de UM trade: candles em pips + fatos recomputados.

    Reaproveita a MESMA janela e os MESMOS níveis do gráfico visual (grafico._janela_trade,
    analise.niveis_ativos) para o texto e o gráfico contarem a mesma história."""
    from .grafico import _janela_trade

    antes = antes if antes is not None else config.AUDITORIA_RAIOX_ANTES
    depois = depois if depois is not None else config.AUDITORIA_RAIOX_DEPOIS
    par, tf = t["par"], t.get("tf") or "M5"
    ent, sl, sai = t["preco_entrada"], t.get("sl_servidor"), t.get("preco_saida")
    direcao = t["direcao"]
    compra = direcao == "compra"
    pip = _pip_de_trade(t)
    ab, fe = t.get("abertura_utc"), t.get("fechamento_utc")

    candles = _janela_trade(conn, par, tf, ab, fe, antes, depois)
    niveis = analise.niveis_ativos(conn, par)

    def em_pips(preco):
        return round((preco - ent) / pip, 1)  # + = acima da entrada (sinal cru, vs. entrada)

    def favor(preco):
        # Excursão a FAVOR em pips (compra: subir; venda: descer). ≥0 é bom.
        return round(((preco - ent) if compra else (ent - preco)) / pip, 1)

    linhas, mfe_fav, mfe_off, mae_adv, mae_off = [], 0.0, None, 0.0, None
    furou_sl, pico_pos_saida, fundo_pos_saida = 0.0, 0.0, 0.0
    idx_ent = idx_sai = None
    off = 0
    for i, c in enumerate(candles):
        tempo = c["time_utc"]
        # candle de entrada = primeiro com time >= abertura; saída = primeiro com time >= fechamento
        if idx_ent is None and ab is not None and tempo >= ab:
            idx_ent = i
        if idx_sai is None and fe is not None and tempo >= fe:
            idx_sai = i
    base_ent = idx_ent if idx_ent is not None else 0
    for i, c in enumerate(candles):
        off = i - base_ent
        durante = (idx_ent is not None and i >= idx_ent and
                   (idx_sai is None or i <= idx_sai))
        pos_saida = idx_sai is not None and i > idx_sai
        fav_h, fav_l = favor(c["high"]), favor(c["low"])
        if durante:
            mfe_i = max(fav_h, fav_l)      # melhor a favor tocado no candle
            mae_i = min(fav_h, fav_l)      # pior contra (≤0)
            if mfe_i > mfe_fav:
                mfe_fav, mfe_off = mfe_i, off
            if mae_i < mae_adv:
                mae_adv, mae_off = mae_i, off
            if sl is not None:  # furou o stop dentro da vida do trade?
                estourou = (c["low"] <= sl) if compra else (c["high"] >= sl)
                if estourou:
                    prof = ((sl - c["low"]) if compra else (c["high"] - sl)) / pip
                    furou_sl = max(furou_sl, round(prof, 1))
        if pos_saida:
            pico_pos_saida = max(pico_pos_saida, max(fav_h, fav_l))
            fundo_pos_saida = min(fundo_pos_saida, min(fav_h, fav_l))
        marca = ("E" if i == idx_ent else "") + ("X" if i == idx_sai else "")
        linhas.append({
            "off": off, "hora": datetime.utcfromtimestamp(tempo).strftime("%m-%d %H:%M"),
            "o": em_pips(c["open"]), "h": em_pips(c["high"]),
            "l": em_pips(c["low"]), "c": em_pips(c["close"]), "marca": marca,
        })

    sl_pips = em_pips(sl) if sl is not None else None
    sai_pips = em_pips(sai) if sai is not None else None
    return {
        "id": t.get("id"), "par": par, "tf": tf, "direcao": direcao,
        "estrategia": config.nome_estrategia(t.get("estrategia")),
        "pip": pip, "entrada": ent, "sl": sl, "saida": sai,
        "sl_pips": sl_pips, "saida_pips": sai_pips,
        "pips_gravado": t.get("pips"), "R": t.get("r_result") or _res_r(t),
        "mae_r": t.get("mae_r"), "mfe_r": t.get("mfe_r"),
        "regime": t.get("regime_entrada") or "—",
        "motivo_saida": t.get("motivo_saida"),
        "flag": t.get("flag") or classificar_perda(t.get("mae_r"), t.get("mfe_r")),
        # Fatos recomputados dos candles (verificáveis na tabela):
        "mfe_pips": round(mfe_fav, 1), "mfe_offset": mfe_off,
        "mae_pips": round(mae_adv, 1), "mae_offset": mae_off,
        "furou_sl_pips": furou_sl,
        "pos_saida_favor_pips": round(pico_pos_saida, 1),
        "pos_saida_contra_pips": round(fundo_pos_saida, 1),
        "niveis": _niveis_perto(niveis, ent, pip, tf),
        "candles": linhas, "antes": antes, "depois": depois,
        "ctx": t.get("ctx") or _contexto_decisao(conn, t),
    }


def raiox_texto(dados: dict) -> str:
    """Bloco de texto do raio-x de um trade: fatos + níveis + candles em pips (a IA lê e analisa)."""
    d = dados
    L = []
    seta = "↑ compra (a favor = ACIMA da entrada, +)" if d["direcao"] == "compra" \
        else "↓ venda (a favor = ABAIXO da entrada, −)"
    L.append(f"### RAIO-X #{d['id']} · {d['par']} {d['tf']} · {d['estrategia']} · {seta}")
    L.append(f"Entrada=0 (preço {d['entrada']}). SL={d['sl_pips']} pips · "
             f"Saída={d['saida_pips']} pips · pips gravado={d['pips_gravado']} · R={d['R']} · "
             f"regime={d['regime']} · flag={d['flag']}")
    L.append(f"Motivo da saída: {d['motivo_saida']}")
    # Contexto da decisão (por que entrou)
    if d.get("ctx"):
        confl = "; ".join(str(c) for c in (d["ctx"].get("confluencias") or [])) or "—"
        L.append(f"Por que entrou: score={d['ctx'].get('score')} · confluências: {confl}")
    if not d["candles"]:
        L.append("(sem candles na janela ainda — o coletor não gravou o contexto deste par/TF; "
                 "reabra depois que o 'futuro' se preencher.)")
        return "\n".join(L)

    def _off(v):
        return "—" if v is None else f"{v:+}"

    # Fatos recomputados dos candles
    L.append("FATOS (recomputados dos candles abaixo):")
    L.append(f"  • Andou a favor até +{d['mfe_pips']} pips (no candle {_off(d['mfe_offset'])}) "
             f"antes de virar — MFE.")
    L.append(f"  • Pior contra: {d['mae_pips']} pips (candle {_off(d['mae_offset'])}) — MAE.")
    if d["furou_sl_pips"] > 0:
        L.append(f"  • O preço FUROU o SL em {d['furou_sl_pips']} pips (o stop foi tocado/"
                 "ultrapassado dentro da vida do trade).")
    else:
        L.append("  • O preço NÃO furou o SL na janela durante o trade "
                 "(saída por outro motivo, não stop de preço).")
    L.append(f"  • DEPOIS da saída: foi até +{d['pos_saida_favor_pips']} pips a favor e "
             f"{d['pos_saida_contra_pips']} pips contra (na direção do trade). "
             "Muito a favor após sair = stop apertado/saída cedo; muito contra = "
             "entrada/estratégia errada.")
    # Níveis do motor perto da entrada
    if d["niveis"]:
        nv = " · ".join(f"{n['tipo']}({n['tf']}) {n['dist_pips']:+}p" for n in d["niveis"])
        L.append(f"Níveis do motor perto da entrada (dist em pips): {nv}")
    # Tabela de candles (pips vs entrada)
    L.append(f"CANDLES (janela {d['antes']} antes / {d['depois']} depois; pips vs entrada; "
             "off=candles desde a entrada; E=entrada, X=saída):")
    L.append("off | hora | O | H | L | C | marca")
    for c in d["candles"]:
        L.append(f"{c['off']:+4d} | {c['hora']} | {c['o']:+6.1f} | {c['h']:+6.1f} | "
                 f"{c['l']:+6.1f} | {c['c']:+6.1f} | {c['marca']}")
    return "\n".join(L)


def raiox_de_id(conn, trade_id: int, antes: int = None, depois: int = None) -> dict:
    """Raio-x textual de um trade por id (para /api/raiox/{id} e o botão no /trade/{id})."""
    r = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not r:
        return None
    t = dict(r)
    t["tf"] = t["tf"] or "M5"
    return raiox_dados(conn, t, antes, depois)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    args = [a for a in sys.argv[1:]]
    como_json = "--json" in args
    args = [a for a in args if not a.startswith("--")]
    # `raiox <id>` → raio-x textual de UM trade; senão, dossiê do período [de, ate].
    if args and args[0] == "raiox":
        trade_id = int(args[1])
        with db.sessao() as conn:
            dados = raiox_de_id(conn, trade_id)
        if dados is None:
            print(f"Trade #{trade_id} não encontrado.")
            return
        print(json.dumps(dados, ensure_ascii=False, indent=2) if como_json else raiox_texto(dados))
        return
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
