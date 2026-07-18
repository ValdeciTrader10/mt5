"""Painel web (FastAPI) — acompanhamento do sistema com login/senha.

Rotas:
  GET  /login              formulário de login
  POST /login              autentica e cria sessão
  GET  /logout             encerra sessão
  GET  /                   painel (exige login)
  GET  /grafico/{par}/{tf} gráfico de candles (exige login)
  GET  /api/status         JSON com o estado do sistema (exige login)
  GET  /health             healthcheck do Docker (sem login)

Nesta fundação o painel mostra o estado do coletor (candles, spread), o estado da
ponte MT5 e as tabelas de decisões/trades (vazias até as Fases 4/5).
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import (analise, calibracao_b3, config, config_b3, db, fuzzy_score, indicadores,
                mt5_bridge, mt5_bridge_b3)
from . import auth

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Painel Forex M5", docs_url=None, redoc_url=None)
if config.SECRET_KEY == "troque-esta-chave-em-producao":
    # O fallback está no GitHub: com ele, qualquer um FORJA o cookie de sessão e passa por todas
    # as rotas (inclusive o reset). O compose do Dokploy exige a env; este alarme cobre os
    # outros caminhos de subida.
    log.critical("SECRET_KEY é o valor DEFAULT (público no repositório) — defina a env "
                 "SECRET_KEY já; qualquer pessoa consegue forjar o login do painel!")
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    https_only=False,          # atrás do Caddy (TLS termina lá); cookie httponly abaixo
    max_age=config.SESSAO_HORAS * 3600,
    same_site="lax",
)


@app.on_event("startup")
def _startup() -> None:
    # Garante que o schema existe (o coletor também cria, mas o painel pode subir antes).
    try:
        db.init_db()
    except Exception:  # noqa: BLE001
        log.exception("Falha ao inicializar o banco no startup do painel")


# --------------------------------------------------------------------------- #
# Coleta de estado
# --------------------------------------------------------------------------- #
def _status_dados() -> dict:
    """Resumo do estado do sistema para painel e /api/status."""
    from datetime import datetime

    pares_tf = []
    decisoes = trades = 0
    with db.sessao() as conn:
        for par in config.PARES:
            for tf in config.TFS_COLETA:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n, MAX(time_utc) AS ultimo,
                           AVG(spread) AS spread_medio
                    FROM candles WHERE par = ? AND tf = ?
                    """,
                    (par, tf),
                ).fetchone()
                ultimo = row["ultimo"]
                pares_tf.append(
                    {
                        "par": par,
                        "tf": tf,
                        "candles": row["n"] or 0,
                        "ultimo": (
                            datetime.utcfromtimestamp(ultimo).strftime("%Y-%m-%d %H:%M")
                            if ultimo else "—"
                        ),
                        "spread_medio": round(row["spread_medio"], 1) if row["spread_medio"] else 0,
                    }
                )
        # Dashboard do FOREX: isola o livro forex (legado NULL=forex) — a B3 tem página própria
        # (/b3, BRL) e vazar WIN/WDO aqui somava BRL no "flutuante USD" e inflava contadores.
        _FX = "(mercado IS NULL OR mercado='forex')"
        decisoes = conn.execute(f"SELECT COUNT(*) AS n FROM decisoes WHERE {_FX}").fetchone()["n"]
        trades = conn.execute(
            f"SELECT COUNT(*) AS n FROM trades WHERE fechamento_utc IS NOT NULL AND {_FX}"
        ).fetchone()["n"]
        analise_pares = [analise.resumo_par(conn, par) for par in config.PARES]
        posicoes_abertas = [
            {
                "par": r["par"], "direcao": r["direcao"],
                "estrategia": config.nome_estrategia(r["estrategia"]),
                "entrada": round(r["preco_entrada"], 5),
                "sl": round(r["sl_servidor"], 5) if r["sl_servidor"] else None,
                "desde": datetime.utcfromtimestamp(r["abertura_utc"]).strftime("%m-%d %H:%M"),
                "simulado": bool(r["simulado"]),
                "r_atual": r["r_atual"], "lucro_atual": r["lucro_atual"],  # P&L flutuante ao vivo
            }
            for r in conn.execute(
                f"SELECT par, direcao, estrategia, preco_entrada, sl_servidor, abertura_utc, simulado, "
                f"r_atual, lucro_atual "
                f"FROM trades WHERE fechamento_utc IS NULL AND {_FX} ORDER BY abertura_utc DESC"
            ).fetchall()
        ]
        flutuante_usd = round(sum(p["lucro_atual"] or 0 for p in posicoes_abertas), 2)
        trades_recentes = [
            {
                "par": r["par"], "direcao": r["direcao"],
                "pips": r["pips"], "lucro": r["lucro_usd"], "motivo": r["motivo_saida"],
                "quando": datetime.utcfromtimestamp(r["fechamento_utc"]).strftime("%m-%d %H:%M"),
                "simulado": bool(r["simulado"]),
            }
            for r in conn.execute(
                f"SELECT par, direcao, pips, lucro_usd, motivo_saida, fechamento_utc, simulado "
                f"FROM trades WHERE fechamento_utc IS NOT NULL AND {_FX} "
                f"ORDER BY fechamento_utc DESC LIMIT 15"
            ).fetchall()
        ]
        decisoes_recentes = [
            {
                "par": r["par"],
                "hora": datetime.utcfromtimestamp(r["time_utc"]).strftime("%m-%d %H:%M"),
                "estrategia": config.nome_estrategia(r["estrategia"]),
                "direcao": r["direcao"] or "—",
                "resultado": r["resultado"],
                "motivo": r["motivo"],
            }
            for r in conn.execute(
                f"SELECT par, time_utc, estrategia, direcao, resultado, motivo "
                f"FROM decisoes WHERE {_FX} ORDER BY time_utc DESC, id DESC LIMIT 15"
            ).fetchall()
        ]

    mt5_info = None
    mt5_ok = False
    try:
        mt5_info = mt5_bridge.ping()
        mt5_ok = True
    except Exception as e:  # noqa: BLE001
        log.debug("ping MT5 falhou no painel: %s", e)

    return {
        "pares_tf": pares_tf,
        "analise_pares": analise_pares,
        "decisoes_recentes": decisoes_recentes,
        "decisoes": decisoes,
        "trades": trades,
        "posicoes_abertas": posicoes_abertas,
        "flutuante_usd": flutuante_usd,
        "trades_recentes": trades_recentes,
        "execucao_ativa": config.EXECUCAO_ATIVA,
        "mt5_ok": mt5_ok,
        "mt5": mt5_info,
        "pares": config.PARES,
        "tfs": config.TFS_COLETA,
        "painel_refresh_s": config.PAINEL_REFRESH_S,
    }


# --------------------------------------------------------------------------- #
# Analítico de trades (ganhadoras/perdedoras, por estratégia, filtro de datas)
# --------------------------------------------------------------------------- #
def _epoch(data_iso: str, fim: bool = False):
    """Converte 'YYYY-MM-DD' (UTC) em epoch. `fim=True` → 23:59:59 do dia."""
    if not data_iso:
        return None
    from datetime import datetime, timezone

    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if fim:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _media(valores: list):
    vals = [v for v in valores if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _agregar(trades: list) -> dict:
    """KPIs de um conjunto de trades fechados (inclui excursão MAE/MFE em R)."""
    n = len(trades)
    ganhos = [t for t in trades if (t["lucro_usd"] or 0) > 0]
    perdas = [t for t in trades if (t["lucro_usd"] or 0) < 0]
    zerados = n - len(ganhos) - len(perdas)
    bruto_ganho = sum(t["lucro_usd"] or 0 for t in ganhos)
    bruto_perda = sum(t["lucro_usd"] or 0 for t in perdas)  # negativo
    usd = sum(t["lucro_usd"] or 0 for t in trades)
    pips = sum(t["pips"] or 0 for t in trades)
    pf = round(bruto_ganho / abs(bruto_perda), 2) if bruto_perda else None  # None = sem perdas
    return {
        "n": n, "ganhos": len(ganhos), "perdas": len(perdas), "zerados": zerados,
        "winrate": round(100 * len(ganhos) / n, 1) if n else 0.0,
        "usd": round(usd, 2), "pips": round(pips, 1),
        "profit_factor": pf,
        "media_ganho": round(bruto_ganho / len(ganhos), 2) if ganhos else 0.0,
        "media_perda": round(bruto_perda / len(perdas), 2) if perdas else 0.0,
        "expectativa": round(usd / n, 2) if n else 0.0,
        # Excursão (R) — a ferramenta para calibrar stop/alvo com dado:
        "mae_medio": _media([t["mae_r"] for t in trades]),      # pior R contra, médio
        "mfe_medio": _media([t["mfe_r"] for t in trades]),      # melhor R a favor, médio
        "mae_ganhos": _media([t["mae_r"] for t in ganhos]),     # "calor" que os vencedores aguentaram
        "mfe_perdas": _media([t["mfe_r"] for t in perdas]),     # lucro a favor que os perdedores devolveram
    }


def _por(trades: list, chave: str) -> list:
    """Agrupa por uma coluna e agrega; ordena por USD total (pior → melhor no fim)."""
    grupos: dict = {}
    for t in trades:
        grupos.setdefault(t[chave] or "—", []).append(t)
    linhas = [{"chave": k, **_agregar(v)} for k, v in grupos.items()]
    linhas.sort(key=lambda x: x["usd"])
    return linhas


def _por_estrategia_tf(trades: list) -> list:
    """Cruzamento ESTRATÉGIA × TIMEFRAME — responde direto "qual estratégia funciona melhor
    em qual timeframe" (o objetivo da sombra). A chave junta o nome amigável e o TF; só
    grupos com trades aparecem. Ordena por USD (pior → melhor)."""
    grupos: dict = {}
    for t in trades:
        nome = config.nome_estrategia(t["estrategia"])
        tf = t["tf"] or "M5"
        grupos.setdefault((nome, tf), []).append(t)
    linhas = [{"chave": f"{nome} · {tf}", "estrategia": nome, "tf": tf, **_agregar(v)}
              for (nome, tf), v in grupos.items()]
    linhas.sort(key=lambda x: x["usd"])
    return linhas


def _exec_custo(reais: list) -> dict:
    """Custo de execução REAL médio (derrapagem/spread/delay) — só existe em trades reais."""
    return {
        "n": len(reais),
        "derrapagem": _media([t.get("derrapagem_pips") for t in reais]),
        "spread": _media([t.get("spread_entrada") for t in reais]),
        "delay": _media([t.get("delay_s") for t in reais]),
    }


def _sombra_vs_real(sombra: list, real: list) -> list:
    """Compara, por (estratégia × TF), o livro VIRTUAL (sombra) com o REAL (demo) — só as
    combinações que já têm trade real. Mostra a expectância dos dois lados, o gap (real − sombra)
    e o custo de execução (derrapagem/spread/delay) medido no real. É a validação: quanto o
    mundo real cobra em cima do que a sombra calculou."""
    chaves: dict = {}
    for t in sombra:
        chaves.setdefault((t["estrategia"], t["tf"]), {"sombra": [], "real": []})["sombra"].append(t)
    for t in real:
        chaves.setdefault((t["estrategia"], t["tf"]), {"sombra": [], "real": []})["real"].append(t)
    linhas = []
    for (est, tf), livros in chaves.items():
        reais = livros["real"]
        if not reais:                       # só compara o que tem par real
            continue
        av, ar, custo = _agregar(livros["sombra"]), _agregar(reais), _exec_custo(reais)
        linhas.append({
            "chave": f"{config.nome_estrategia(est)} · {tf}",
            "n_sombra": av["n"], "exp_sombra": av["expectativa"], "usd_sombra": av["usd"],
            "n_real": ar["n"], "exp_real": ar["expectativa"], "usd_real": ar["usd"],
            "delta_exp": round(ar["expectativa"] - av["expectativa"], 2),   # real − sombra
            "derrapagem": custo["derrapagem"], "spread": custo["spread"], "delay": custo["delay"],
        })
    linhas.sort(key=lambda x: x["delta_exp"])   # pior gap (real abaixo da sombra) primeiro
    return linhas


def _normalizar_motivo(motivo: str) -> str:
    """Colapsa o motivo de saída para um rótulo ESTÁVEL, agrupável.

    Os motivos embutem números/direção que variam a cada trade ("CHOCH alta (r=1.3)",
    "cedeu 0.7R do pico (1.4R → 0.7R)", "tempo máximo (1.2h ≥ 8h)"), o que fragmentava o
    /analitico em dezenas de linhas quase iguais. Aqui removemos os parênteses, a direção e
    os valores de R para agrupar por CAUSA (ex.: todos os "reversão confirmada: CHOCH")."""
    if not motivo:
        return "—"
    import re
    m = re.sub(r"\s*\([^)]*\)", "", motivo)          # remove "(...)"
    m = re.sub(r"\s+(alta|baixa)\b", "", m)          # remove direção
    m = re.sub(r"\d+\.?\d*\s*R", "R", m)             # normaliza valores de R
    return m.strip() or "—"


def _sessao(hora_srv: int) -> str:
    """Sessão de mercado pela hora do SERVIDOR (UTC+3) da abertura do trade.

    Desde o fix de fuso, `abertura_utc` é hora do servidor — os buckets antigos rotulados em
    UTC deslocavam a análise "por sessão" em 3h (trade do coração de Londres caía no bucket
    errado). Limites = UTC clássicos + 3 (Londres 07–11 UTC = 10–14 servidor)."""
    if 10 <= hora_srv <= 14:
        return "Londres (10–14 srv)"
    if 15 <= hora_srv <= 18:
        return "Londres/NY (15–18 srv)"
    if 19 <= hora_srv <= 23:
        return "Nova York (19–23 srv)"
    if 3 <= hora_srv <= 9:
        return "Ásia (03–09 srv)"
    return "Fora de sessão (00–02 srv)"


def _curva_capital(trades: list, base: float) -> dict:
    """Curva de capital (ordem cronológica de fechamento) + drawdown máximo.

    `base` = saldo inicial de referência (equity acumulada parte dele). Retorna a série
    para plotar, o DD máximo em USD/%, e o recovery factor (lucro/|maxDD|)."""
    from datetime import datetime, timezone

    ordenados = sorted([t for t in trades if t["fechamento_utc"]], key=lambda t: t["fechamento_utc"])
    eq = base
    pico = base
    max_dd = 0.0
    pico_no_dd = pico   # pico VIGENTE no momento do pior DD (não o pico final da série)
    serie = []
    for t in ordenados:
        eq += t["lucro_usd"] or 0
        pico = max(pico, eq)
        dd = eq - pico  # ≤ 0
        if dd < max_dd:
            max_dd = dd
            pico_no_dd = pico
        serie.append({
            "t": datetime.fromtimestamp(t["fechamento_utc"], timezone.utc).strftime("%m-%d %H:%M"),
            "eq": round(eq, 2), "dd": round(dd, 2),
        })
    lucro = eq - base
    return {
        "serie": serie,
        "base": round(base, 2),
        "final": round(eq, 2),
        "max_dd": round(max_dd, 2),
        # % sobre o pico vigente QUANDO o DD aconteceu — usar o pico final subestimava o DD
        # sempre que a equity crescia depois do vale.
        "max_dd_pct": round(100 * max_dd / pico_no_dd, 2) if pico_no_dd else 0.0,
        "recovery_factor": round(lucro / abs(max_dd), 2) if max_dd < 0 else None,
    }


def _analitico(de: str = "", ate: str = "", mercado: str = "forex") -> dict:
    """Estatísticas dos trades fechados no intervalo [de, ate] (datas ISO, opcionais).

    `mercado` ISOLA os livros: 'forex' (default) exclui a B3 (legado NULL conta como forex);
    'b3' traz só WIN/WDO. Assim o /analitico do forex não mistura o mercado brasileiro."""
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    cond, args = ["fechamento_utc IS NOT NULL"], []
    if mercado == "b3":
        cond.append("mercado='b3'")
    else:
        cond.append("(mercado IS NULL OR mercado='forex')")
    if de_e:
        cond.append("fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("fechamento_utc <= ?"); args.append(ate_e)
    where = " AND ".join(cond)
    with db.sessao() as conn:
        rows = conn.execute(
            f"SELECT id, par, tf, estrategia, direcao, pips, lucro_usd, motivo_saida, simulado, "
            f"mae_r, mfe_r, regime_entrada, abertura_utc, fechamento_utc, "
            f"derrapagem_pips, spread_entrada, delay_s FROM trades "
            f"WHERE {where} ORDER BY fechamento_utc DESC",
            args,
        ).fetchall()
    from datetime import datetime, timezone

    trades = [dict(r) for r in rows]
    for t in trades:  # rótulos derivados para os agrupamentos
        t["regime"] = t["regime_entrada"] or "—"
        h = datetime.fromtimestamp(t["abertura_utc"], timezone.utc).hour if t["abertura_utc"] else 0
        t["sessao"] = _sessao(h)
        t["motivo_norm"] = _normalizar_motivo(t["motivo_saida"])

    def _hora(ep):
        return datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%d %H:%M") if ep else "—"

    lista = [
        {
            "id": t["id"],
            "quando": _hora(t["fechamento_utc"]), "par": t["par"], "tf": t["tf"] or "M5",
            "estrategia": config.nome_estrategia(t["estrategia"]),
            "direcao": t["direcao"], "pips": t["pips"], "lucro": t["lucro_usd"],
            "motivo": t["motivo_saida"], "simulado": bool(t["simulado"]), "real": not t["simulado"],
            "mae_r": t["mae_r"], "mfe_r": t["mfe_r"], "regime": t["regime"], "sessao": t["sessao"],
        }
        for t in trades[:300]
    ]
    for t in trades:  # rótulo estável do TF de operação (default M5 p/ trades antigos)
        t["tf"] = t["tf"] or "M5"
    # O estudo (catálogo) é o livro SOMBRA; o livro REAL (demo curado) é a validação à parte,
    # comparada em "Sombra vs Real". Enquanto não houver trade real, sombra == todos os trades.
    sombra = [t for t in trades if t["simulado"]]
    real = [t for t in trades if not t["simulado"]]
    por_estrategia = _por(sombra, "estrategia")
    for r in por_estrategia:  # rótulo amigável (agrupa pelo código, exibe o nome bonito)
        r["chave"] = config.nome_estrategia(r["chave"])
    return {
        "de": de, "ate": ate,
        "geral": _agregar(sombra),
        "curva": _curva_capital(sombra, config.SALDO_SIMULADO),
        "por_estrategia": por_estrategia,
        "por_estrategia_tf": _por_estrategia_tf(sombra),
        "por_timeframe": _por(sombra, "tf"),
        "por_regime": _por(sombra, "regime"),
        "por_sessao": _por(sombra, "sessao"),
        "por_par": _por(sombra, "par"),
        "por_motivo": _por(sombra, "motivo_norm"),
        "sombra_vs_real": _sombra_vs_real(sombra, real),
        "custo_real": _exec_custo(real),
        "trades": lista,
    }


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
@app.get("/export/candles")
def export_candles(request: Request, tf: str = "H1", mercado: str = "forex"):
    """Exporta os candles (OHLC + volume) em CSV para DOWNLOAD — p/ estudo histórico offline (ex.:
    catalogar um padrão retroativamente). `tf` default H1 (com o H1 reconstrói-se o H4); `mercado`
    forex (config.PARES) ou b3 (WIN/WDO). Só o dono logado; dado de mercado (OHLC), nada sensível."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    pares = list(config_b3.PARES_B3) if mercado == "b3" else list(config.PARES)
    cols = ("par", "tf", "time_utc", "open", "high", "low", "close",
            "tick_volume", "real_volume", "spread")
    linhas = [",".join(cols)]
    with db.sessao() as conn:
        ph = ",".join("?" * len(pares))
        q = (f"SELECT {','.join(cols)} FROM candles WHERE tf=? AND par IN ({ph}) "
             "ORDER BY par, time_utc")
        for r in conn.execute(q, [tf, *pares]):
            linhas.append(",".join("" if r[c] is None else str(r[c]) for c in cols))
    csv = "\n".join(linhas)
    return Response(csv, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="candles_{mercado}_{tf}.csv"'})


@app.get("/api/export/estrategias")
def export_estrategias(request: Request, mercado: str = "forex"):
    """Estratégias com trades FECHADOS no livro sombra do mercado + a contagem — alimenta o
    seletor do botão de exportação (o dono escolhe a estratégia e baixa o zip)."""
    if not auth.esta_logado(request):
        return JSONResponse({"erro": "nao_logado"}, status_code=401)
    filtro = "mercado='b3'" if mercado == "b3" else "(mercado IS NULL OR mercado='forex')"
    with db.sessao() as conn:
        rows = conn.execute(
            f"SELECT estrategia, COUNT(*) n FROM trades WHERE fechamento_utc IS NOT NULL "
            f"AND simulado=1 AND {filtro} GROUP BY estrategia ORDER BY n DESC").fetchall()
    return JSONResponse({"estrategias": [
        {"estrategia": r["estrategia"], "nome": config.nome_estrategia(r["estrategia"]), "n": r["n"]}
        for r in rows]})


@app.get("/export/raiox")
def export_raiox(request: Request, estrategia: str, mercado: str = "forex",
                 de: str = "", ate: str = "", limite: int = None):
    """Exporta um ZIP com o RAIO-X de cada trade (fechado, sombra) de UMA estratégia no período —
    1 HTML por trade (gráfico visual via CDN + fatos + 'por que entrou' + raio-X textual em pips).
    O dono baixa por estratégia e reenvia p/ a IA auditar o ponto de entrada em lote. Só o dono
    logado; conteúdo = dados de mercado + métricas do próprio robô, nada sensível."""
    import io
    import zipfile
    from ..grafico import grafico_trade_html

    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    limite = min(limite or config.RAIOX_EXPORT_MAX, config.RAIOX_EXPORT_MAX)
    filtro = "mercado='b3'" if mercado == "b3" else "(mercado IS NULL OR mercado='forex')"
    cond, args = ["fechamento_utc IS NOT NULL", "simulado=1", "estrategia=?", filtro], [estrategia]
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    if de_e:
        cond.append("fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("fechamento_utc <= ?"); args.append(ate_e)
    with db.sessao() as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM trades WHERE {' AND '.join(cond)} ORDER BY fechamento_utc DESC LIMIT ?",
            [*args, limite]).fetchall()]
    if not ids:
        return Response("Nenhum trade fechado dessa estratégia no período.",
                        media_type="text/plain", status_code=404)

    buf = io.BytesIO()
    gerados = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for tid in ids:
            try:
                html = grafico_trade_html(tid, plotly_cdn=True, incluir_raiox=True)
            except Exception:  # noqa: BLE001 - um trade problemático não invalida o lote inteiro
                log.exception("Falha ao gerar raio-X do trade %s no export", tid)
                continue
            z.writestr(f"trade_{tid}.html", html)
            gerados += 1
        # Índice do lote (o que foi exportado + o teto aplicado), p/ o dono e a IA se situarem.
        z.writestr("_INDICE.txt",
                   f"Estratégia: {config.nome_estrategia(estrategia)} ({estrategia})\n"
                   f"Mercado: {mercado}\nPeríodo: {de or 'início'} → {ate or 'hoje'}\n"
                   f"Trades exportados: {gerados} (teto {config.RAIOX_EXPORT_MAX}; "
                   f"os mais RECENTES primeiro)\nIDs: {', '.join(map(str, ids[:gerados]))}\n")
    buf.seek(0)
    nome = f"raiox_{mercado}_{estrategia}_{gerados}trades.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, erro: str = ""):
    if auth.esta_logado(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"erro": erro})


@app.post("/login")
def login_post(request: Request, usuario: str = Form(...), senha: str = Form(...)):
    if auth.verificar_credenciais(usuario, senha):
        request.session["usuario"] = usuario
        log.info("Login OK: %s", usuario)
        return RedirectResponse(url="/", status_code=303)
    log.warning("Login negado para usuário '%s'", usuario)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"erro": "Usuário ou senha inválidos."},
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def painel(request: Request):
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    dados = _status_dados()
    return templates.TemplateResponse(request, "dashboard.html", {"status": dados, **dados})


@app.get("/api/status")
def api_status(request: Request):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return JSONResponse(_status_dados())


@app.get("/analitico", response_class=HTMLResponse)
def analitico(request: Request, de: str = "", ate: str = ""):
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    dados = _analitico(de, ate)
    return templates.TemplateResponse(
        request, "analitico.html", {"dados": dados, "execucao_ativa": config.EXECUCAO_ATIVA}
    )


@app.get("/api/analitico")
def api_analitico(request: Request, de: str = "", ate: str = ""):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return JSONResponse(_analitico(de, ate))


@app.get("/auditoria", response_class=HTMLResponse)
def auditoria_page(request: Request, de: str = "", ate: str = ""):
    """Dossiê de calibração das PERDEDORAS: bloco de texto pronto para colar na IA + tabelas.
    É a 'forma de a IA auditar as operações perdedoras' (o banco vive na VPS; aqui exportamos)."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from .. import auditoria as aud

    with db.sessao() as conn:
        d = aud.dossie_perdedores(conn, de, ate)
    texto = aud.dossie_texto(d)
    return templates.TemplateResponse(
        request, "auditoria.html",
        {"dados": d, "texto": texto, "de": de, "ate": ate, "mercado": "forex"}
    )


@app.get("/api/auditoria")
def api_auditoria(request: Request, de: str = "", ate: str = "", formato: str = "json"):
    """JSON (default) ou texto puro (`?formato=texto`) do dossiê — para automação/copiar."""
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    from .. import auditoria as aud

    with db.sessao() as conn:
        d = aud.dossie_perdedores(conn, de, ate)
    if formato == "texto":
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(aud.dossie_texto(d))
    return JSONResponse(d)


def _dados_b3() -> dict:
    """Dados do painel SEPARADO da B3 (WIN/WDO) — mercado distinto do forex (P&L em BRL, outra
    escala), por isso página própria. Mostra a conexão da ponte da Genial, a saúde da COLETA
    (candles por TF), a última cotação, a análise do MOTOR (regime + níveis), a calibração de
    escala e os RESULTADOS da sombra (por estratégia × TF, em BRL) + as posições abertas."""
    from datetime import datetime
    pares = list(config_b3.PARES_B3)
    with db.sessao() as conn:
        simbolos = []
        for par in pares:
            resumo = analise.resumo_par(conn, par)
            tfs, total = [], 0
            for tf in config_b3.TFS_COLETA_B3:
                r = conn.execute("SELECT COUNT(*) n, MAX(time_utc) t FROM candles WHERE par=? AND tf=?",
                                 (par, tf)).fetchone()
                total += r["n"] or 0
                tfs.append({"tf": tf, "n": r["n"] or 0, "ultimo_utc": r["t"]})
            lc = conn.execute("SELECT close, time_utc FROM candles WHERE par=? ORDER BY time_utc DESC "
                              "LIMIT 1", (par,)).fetchone()
            simbolos.append({
                "par": par, "resumo": resumo, "tfs": tfs, "total_candles": total,
                "ultimo_preco": lc["close"] if lc else None,
                "ultimo_utc": lc["time_utc"] if lc else None,
            })
        # Livro de SOMBRA da B3 (mercado='b3'): decisões, trades fechados/abertos e P&L em BRL.
        n_dec = conn.execute("SELECT COUNT(*) n FROM decisoes WHERE mercado='b3'").fetchone()["n"]
        n_trades = conn.execute("SELECT COUNT(*) n FROM trades WHERE mercado='b3'").fetchone()["n"]
        fechados = conn.execute(
            "SELECT estrategia, tf, direcao, pips, lucro_usd, mae_r, mfe_r, variante "
            "FROM trades WHERE mercado='b3' AND fechamento_utc IS NOT NULL").fetchall()
        fechados = [dict(r) for r in fechados]
        pnl_brl = round(sum(t["lucro_usd"] or 0 for t in fechados), 2)
        resumo_geral = _agregar(fechados) if fechados else None
        por_estrategia_tf = _por_estrategia_tf(fechados) if fechados else []
        # Posições abertas da sombra (o "quantidades" ao vivo, como no forex).
        abertas = [dict(r) for r in conn.execute(
            "SELECT par, tf, estrategia, direcao, preco_entrada, sl_servidor, abertura_utc, variante, "
            "r_atual, lucro_atual "
            "FROM trades WHERE mercado='b3' AND fechamento_utc IS NULL ORDER BY abertura_utc DESC").fetchall()]
        posicoes_abertas = [{
            "par": r["par"], "tf": r["tf"], "estrategia": config.nome_estrategia(r["estrategia"]),
            "direcao": r["direcao"], "entrada": r["preco_entrada"], "sl": r["sl_servidor"],
            "variante": config.nome_variante(r["variante"]),
            "r_atual": r["r_atual"], "lucro_atual": r["lucro_atual"],  # P&L flutuante ao vivo (BRL)
            "hora": datetime.utcfromtimestamp(r["abertura_utc"]).strftime("%m-%d %H:%M")
            if r["abertura_utc"] else "—",
        } for r in abertas]
        flutuante_brl = round(sum(p["lucro_atual"] or 0 for p in posicoes_abertas), 2)
        # Últimas decisões da B3 (entrou/não), como o feed do painel do forex.
        decisoes_recentes = [{
            "par": r["par"],
            "hora": datetime.utcfromtimestamp(r["time_utc"]).strftime("%m-%d %H:%M"),
            "tf": r["tf"], "estrategia": config.nome_estrategia(r["estrategia"]),
            "direcao": r["direcao"] or "—", "resultado": r["resultado"], "motivo": r["motivo"],
        } for r in conn.execute(
            "SELECT par, time_utc, tf, estrategia, direcao, resultado, motivo FROM decisoes "
            "WHERE mercado='b3' ORDER BY time_utc DESC, id DESC LIMIT 15").fetchall()]
        # Calibração de escala (Etapa 8b): derivada dos candles já coletados. Guardada — se
        # faltar dado/erro, o painel segue mostrando o resto (não quebra por causa da calibração).
        calibracao = None
        try:
            calibracao = calibracao_b3.calibrar(conn, pares=pares)
        except Exception:  # noqa: BLE001 - painel tolerante; a calibração é informativa
            log.exception("Falha ao calibrar escala da B3 no painel")

    # Conexão da ponte da Genial (data-only) — o "MT5 conectado" da B3.
    mt5_info = None
    mt5_ok = False
    try:
        mt5_info = mt5_bridge_b3.ping()
        mt5_ok = True
    except Exception as e:  # noqa: BLE001 - painel tolerante (terminal pode estar reiniciando)
        log.debug("ping MT5 B3 falhou no painel: %s", e)

    return {
        "habilitado": config_b3.B3_HABILITADO,
        "pares": pares,
        "simbolos": simbolos,
        "mt5_ok": mt5_ok,
        "mt5": mt5_info,
        "n_decisoes": n_dec,
        "n_trades": n_trades,
        "n_abertas": len(posicoes_abertas),
        "pnl_brl": pnl_brl,
        "flutuante_brl": flutuante_brl,
        "resumo": resumo_geral,
        "por_estrategia_tf": por_estrategia_tf,
        "posicoes_abertas": posicoes_abertas,
        "decisoes_recentes": decisoes_recentes,
        # Sombra da B3 fiada (ETAPA 8b): estrategista + executor de sombra ligados. A flag
        # reflete a configuração (aguardando o pregão formar candles quando n_dec ainda é 0).
        "estrategias_ligadas": config_b3.B3_HABILITADO and config_b3.B3_SOMBRA_HABILITADA,
        "calibracao": calibracao,
    }


@app.get("/b3", response_class=HTMLResponse)
def b3_page(request: Request):
    """Painel SEPARADO da B3 (WIN/WDO) — análise isolada do forex."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    return templates.TemplateResponse(request, "b3.html", {"dados": _dados_b3()})


@app.get("/api/b3")
def api_b3(request: Request):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return JSONResponse(_dados_b3())


# --- Análise e Auditoria da B3 (mesma riqueza do forex, isolada e em BRL) --------------- #
@app.get("/b3/analitico", response_class=HTMLResponse)
def b3_analitico(request: Request, de: str = "", ate: str = ""):
    """Analítico completo da SOMBRA da B3 (WIN/WDO) — mesma página do forex, escopada a
    mercado='b3' e rotulada em BRL. Curva, por estratégia/TF/regime/sessão/par/motivo, MAE/MFE."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    dados = _analitico(de, ate, mercado="b3")
    return templates.TemplateResponse(
        request, "analitico.html",
        {"dados": dados, "execucao_ativa": False, "b3": True, "moeda": "BRL",
         "titulo": "Análise B3", "sub": "· WIN / WDO (sombra)", "api_url": "/api/b3/analitico"},
    )


@app.get("/api/b3/analitico")
def api_b3_analitico(request: Request, de: str = "", ate: str = ""):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return JSONResponse(_analitico(de, ate, mercado="b3"))


@app.get("/b3/auditoria", response_class=HTMLResponse)
def b3_auditoria(request: Request, de: str = "", ate: str = ""):
    """Auditoria IA das perdedoras da B3 — mesmo dossiê (classificação por falha + raio-x em
    pips), escopado a mercado='b3'. É a 'riqueza de detalhes das operações' pedida, para WIN/WDO."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from .. import auditoria as aud

    with db.sessao() as conn:
        d = aud.dossie_perdedores(conn, de, ate, mercado="b3")
    texto = aud.dossie_texto(d)
    return templates.TemplateResponse(
        request, "auditoria.html",
        {"dados": d, "texto": texto, "de": de, "ate": ate, "b3": True, "moeda": "BRL",
         "titulo": "Auditoria IA · B3", "base": "/b3/auditoria", "api_base": "/api/b3/auditoria",
         "mercado": "b3"},
    )


@app.get("/api/b3/auditoria")
def api_b3_auditoria(request: Request, de: str = "", ate: str = "", formato: str = "json"):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    from .. import auditoria as aud

    with db.sessao() as conn:
        d = aud.dossie_perdedores(conn, de, ate, mercado="b3")
    if formato == "texto":
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(aud.dossie_texto(d))
    return JSONResponse(d)


@app.get("/relatorio", response_class=HTMLResponse)
def relatorio_page(request: Request, de: str = "", ate: str = ""):
    """Relatório sombra multi-variante (ETAPA 7): ranking por expectância R por célula
    (variante × estratégia × TF × par), A vs C (filtro fuzzy), split-half e curva por variante."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from .. import relatorio as rel
    from .. import auditoria_estatistica as ae

    with db.sessao() as conn:
        d = rel.montar_relatorio(conn, de, ate)
        apv = ae.auditar(conn, de, ate)          # ETAPA 9: gate de aprovação por célula
    texto = rel.relatorio_texto(d)
    return templates.TemplateResponse(
        request, "relatorio.html",
        {"dados": d, "texto": texto, "aprovacao": apv, "de": de, "ate": ate},
    )


@app.get("/api/relatorio")
def api_relatorio(request: Request, de: str = "", ate: str = "", formato: str = "json"):
    """JSON (default) ou texto puro (`?formato=texto`) do relatório multi-variante."""
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    from .. import relatorio as rel

    with db.sessao() as conn:
        d = rel.montar_relatorio(conn, de, ate)
    if formato == "texto":
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(rel.relatorio_texto(d))
    return JSONResponse(d)


@app.get("/api/raiox/{trade_id}")
def api_raiox(request: Request, trade_id: int, formato: str = "texto",
              antes: int = None, depois: int = None):
    """Raio-X TEXTUAL de um trade (candles em pips + fatos) para a IA ler o price action.
    `?formato=texto` (default, pronto para colar) ou `?formato=json`."""
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    from .. import auditoria as aud

    with db.sessao() as conn:
        dados = aud.raiox_de_id(conn, trade_id, antes, depois)
    if dados is None:
        raise HTTPException(status_code=404, detail=f"trade #{trade_id} não encontrado")
    if formato == "json":
        return JSONResponse(dados)
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(aud.raiox_texto(dados))


def _html_reset(msg: str, ok: bool = True) -> str:
    cor = "#3fb950" if ok else "#f85149"
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>Manutenção</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{background:#0d1117;color:#e6e6e6;font-family:system-ui,Arial;max-width:640px;
margin:3rem auto;padding:0 1rem;line-height:1.6}}a{{color:#2f81f7}}
.card{{border:1px solid #30363d;border-radius:12px;padding:1.4rem;border-left:4px solid {cor}}}
code{{background:#161b22;padding:.1rem .3rem;border-radius:4px;font-size:.85em}}</style></head>
<body><div class="card"><h2 style="margin-top:0">Manutenção — reset de dados</h2>
<p>{msg}</p></div><p><a href="/auditoria">← voltar à Auditoria IA</a></p></body></html>"""


@app.post("/manutencao/reset", response_class=HTMLResponse)
def manutencao_reset(request: Request, confirmacao: str = Form(""), mercado: str = Form("forex")):
    """Zera trades/decisões SÓ do mercado da página que pediu (forex OU b3) — NUNCA os dois. O
    botão do /auditoria manda mercado=forex; o do /b3/auditoria manda mercado=b3. BACKUP automático
    antes. Destrutivo: guardado por login + confirmação digitada 'LIMPAR'."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from .. import manutencao as manut

    if confirmacao.strip().upper() != "LIMPAR":
        return HTMLResponse(_html_reset(
            'Confirmação incorreta — digite <b>LIMPAR</b>. <b>Nada foi apagado.</b>', ok=False),
            status_code=400)
    mercado = "b3" if mercado == "b3" else "forex"      # default seguro; só b3 quando explícito
    bak = manut._backup(config.DB_PATH)                 # backup do banco INTEIRO antes de qualquer delete
    if mercado == "b3":
        # B3 é data-only (nenhuma posição real) → NÃO fecha posição no broker do forex.
        with db.sessao() as conn:
            apagados = manut.resetar_b3(conn)
        corpo = (f"✅ Limpeza da <b>B3</b> concluída — o <b>forex NÃO foi tocado</b>.<br>"
                 f"Backup: <code>{bak}</code><br>"
                 f"Apagados (só mercado='b3'): trades <b>{apagados.get('trades', 0)}</b>, "
                 f"decisões <b>{apagados.get('decisoes', 0)}</b>. <b>Candles preservados.</b><br><br>"
                 f"⚠️ Faça um <b>REDEPLOY no Dokploy</b> para o executor da B3 reiniciar limpo.")
    else:
        fechadas = manut.fechar_posicoes_robo()         # só o forex tem posições no broker
        with db.sessao() as conn:
            apagados = manut.resetar_forex(conn)
        corpo = (f"✅ Limpeza do <b>FOREX</b> concluída — a <b>B3 NÃO foi tocada</b>.<br>"
                 f"Posições do robô fechadas: <b>{fechadas}</b>.<br>"
                 f"Backup: <code>{bak}</code><br>"
                 f"Apagados (só forex): trades <b>{apagados.get('trades', 0)}</b>, "
                 f"decisões <b>{apagados.get('decisoes', 0)}</b>. <b>Candles preservados.</b><br><br>"
                 f"⚠️ Agora faça um <b>REDEPLOY no Dokploy</b> para o executor reiniciar limpo.")
    return HTMLResponse(_html_reset(corpo, ok=True))


@app.post("/manutencao/restaurar", response_class=HTMLResponse)
def manutencao_restaurar(request: Request, confirmacao: str = Form(""), mercado: str = Form("forex")):
    """Restaura trades/decisões de UM mercado do ÚLTIMO backup — desfaz uma limpeza indevida (ex.:
    o forex apagado por engano). Guardado por login + confirmação 'RESTAURAR'. Não toca no outro
    mercado nem duplica (INSERT OR IGNORE por id)."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from .. import manutencao as manut

    if confirmacao.strip().upper() != "RESTAURAR":
        return HTMLResponse(_html_reset(
            'Confirmação incorreta — digite <b>RESTAURAR</b>. <b>Nada foi restaurado.</b>', ok=False),
            status_code=400)
    mercado = "b3" if mercado == "b3" else "forex"
    bak = manut.ultimo_backup()
    if not bak:
        return HTMLResponse(_html_reset(
            'Nenhum backup (.bak) encontrado no servidor — nada a restaurar.', ok=False),
            status_code=400)
    with db.sessao() as conn:
        restaurados = manut.restaurar_de_backup(conn, bak, mercado=mercado)
    nome = "B3" if mercado == "b3" else "Forex"
    corpo = (f"✅ Restauração do <b>{nome}</b> concluída a partir do backup <code>{bak}</code>.<br>"
             f"Restaurados: trades <b>{restaurados.get('trades', 0)}</b>, "
             f"decisões <b>{restaurados.get('decisoes', 0)}</b> (o outro mercado não foi tocado).<br><br>"
             f"⚠️ Faça um <b>REDEPLOY no Dokploy</b> para o executor recarregar do banco.")
    return HTMLResponse(_html_reset(corpo, ok=True))


# Cor de cada estado fuzzy (para pintar as velas do gráfico — ETAPA 4).
_COR_ESTADO = {
    "lima": "#7ee787", "verde": "#3fb950", "branco": "#8b949e",
    "fucsia": "#db61a2", "vermelho": "#f85149",
}
# Cor da Sync Line / semáforo de alinhamento.
_COR_SYNC = {"verde": "#3fb950", "vermelho": "#f85149", "amarelo": "#d29922"}


def _fuzzy_por_candle(conn, par: str, tf: str) -> dict:
    """{time_utc: estado} do fuzzy do (par, tf) — para colorir cada vela pelo estado de fluxo."""
    rows = conn.execute(
        "SELECT time_utc, estado FROM fuzzy_scores WHERE par=? AND tf=?", (par, tf)).fetchall()
    return {r["time_utc"]: r["estado"] for r in rows}


def _series_scores(conn, par: str, lim: int) -> dict:
    """Séries de score fuzzy por TF (M1/M5/M15/H1) para as linhas do painel de scores.
    {tf: [{time, value}]} — cronológicas, limitadas às `lim` velas mais recentes de cada TF."""
    saida = {}
    for tf in config.FUZZY_TFS:
        rows = conn.execute(
            "SELECT time_utc, score FROM fuzzy_scores WHERE par=? AND tf=? "
            "ORDER BY time_utc DESC LIMIT ?", (par, tf, lim)).fetchall()
        if rows:
            saida[tf] = [{"time": r["time_utc"], "value": r["score"]} for r in reversed(rows)]
    return saida


def _serie_forca_linha(conn, par: str, tf: str, lim: int) -> list:
    """Linha de FORÇA contínua (E_SENTINELA) na régua de scores (0-100), no TF do gráfico — asof dos
    scores M1/M5/M15/H1 (`fuzzy_score.forca_serie`). É a 5ª linha p/ comparar com as 4 linhas de TF."""
    rows = conn.execute(
        "SELECT time_utc FROM candles WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf, lim)).fetchall()
    tempos = [r["time_utc"] for r in reversed(rows)]
    serie = fuzzy_score.forca_serie(conn, par, tempos, decay=config.SENT_FORCA_DECAY,
                                    escala=config.SENT_FORCA_ESCALA) if tempos else []
    return [{"time": s["time"], "value": s["forca"]} for s in serie]


def _sync_atual(conn, par: str) -> dict:
    """Última Sync Line do par (micro/macro/estado + cores) para o rodapé do gráfico."""
    r = conn.execute(
        "SELECT time_utc, micro, macro, estado, micro_score, macro_score FROM sync_line "
        "WHERE par=? ORDER BY time_utc DESC LIMIT 1", (par,)).fetchone()
    if not r:
        return {}
    return {"time": r["time_utc"], "micro": r["micro"], "macro": r["macro"],
            "estado": r["estado"], "micro_score": r["micro_score"],
            "macro_score": r["macro_score"], "cor": _COR_SYNC.get(r["estado"], "#8b949e")}


def _pares_validos() -> set:
    """Pares aceitos pelo gráfico: forex (config.PARES) + B3 (WIN/WDO) quando o módulo B3
    está ligado. Sem isto os símbolos da B3 (WIN$N/WDO$N) caíam no 'par/tf inválido'."""
    return set(config.PARES) | set(config_b3.pares_ativos())


def _tfs_validos() -> set:
    """Timeframes aceitos pelo gráfico: os do forex + os da B3 (mesmo conjunto base)."""
    return set(config.TFS_COLETA) | set(config_b3.TFS_COLETA_B3)


@app.get("/api/candles/{par}/{tf}")
def api_candles(request: Request, par: str, tf: str, n: int = 500):
    """OHLC + níveis S/R de (par, tf) em JSON, para o gráfico interativo (lightweight-charts).
    `time` é o time_utc do candle (hora do servidor/MetaTrader), em segundos."""
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    if par not in _pares_validos() or tf not in _tfs_validos():
        raise HTTPException(status_code=404, detail="par/tf inválido")
    from ..grafico import _buscar_candles

    lim = max(20, min(n, 5000))
    with db.sessao() as conn:
        rows = _buscar_candles(conn, par, tf, lim)
        niveis = analise.niveis_ativos(conn, par)
        # Estado fuzzy por candle do TF do gráfico (para pintar as velas) + séries de score.
        fuzzy_tf = _fuzzy_por_candle(conn, par, tf)
        scores = _series_scores(conn, par, lim)
        forca_linha = _serie_forca_linha(conn, par, tf, lim)   # 5ª linha: FORÇA contínua (E_SENTINELA)
        if forca_linha:
            scores["FORCA"] = forca_linha
        sync = _sync_atual(conn, par)
    # Cor da vela pelo estado fuzzy (lima/verde = alta; fúcsia/vermelho = baixa; branco = neutro).
    # Volume por candle (histograma no rodapé): usa o VOLUME REAL (contratos) quando existe — na
    # B3 é o volume financeiro/Wyckoff de verdade; no forex real_volume é NULL e cai no tick_volume.
    candles, volume = [], []
    for r in rows:
        c = {"time": r["time_utc"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"]}
        est = fuzzy_tf.get(r["time_utc"])
        if est:
            cor = _COR_ESTADO.get(est)
            if cor:
                c.update({"color": cor, "borderColor": cor, "wickColor": cor})
        candles.append(c)
        vol = r["real_volume"] or r["tick_volume"] or 0
        if vol:
            # Verde translúcido em candle de alta, vermelho em baixa (leitura de esforço Wyckoff).
            cor_vol = "rgba(63,185,80,0.5)" if r["close"] >= r["open"] else "rgba(248,81,73,0.5)"
            volume.append({"time": r["time_utc"], "value": vol, "color": cor_vol})
    # VWAP como CURVA que se desenvolve na sessão (reset na âncora — meia-noite no forex, abertura
    # do pregão na B3) + bandas ±1σ/±2σ, no lugar de uma única linha horizontal (item do manual fuzzy).
    vwap = {}
    if config.VWAP_HABILITADO and rows:
        chaves = [analise._inicio_sessao_vwap(par, r["time_utc"]) for r in rows]
        vols = [r["real_volume"] or r["tick_volume"] or 0 for r in rows]
        serie = indicadores.vwap_serie(
            [r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows],
            vols, chaves, config.VWAP_K1, config.VWAP_K2)
        vwap = {k: [] for k in ("vwap", "sup1", "inf1", "sup2", "inf2")}
        for r, v in zip(rows, serie):
            if not v:
                continue
            for k in vwap:
                vwap[k].append({"time": r["time_utc"], "value": v[k]})
    # S/R do motor + liquidez por período (pivots, máx/mín ásia/semana/mês). A VWAP e bandas saem
    # daqui (viram curvas, acima) para não duplicar como linha horizontal chapada.
    LINHAS = ("suporte", "resistencia", "pivot_pp", "pivot_r1", "pivot_r2", "pivot_r3",
              "pivot_s1", "pivot_s2", "pivot_s3", "max_asia", "min_asia", "max_semana",
              "min_semana", "max_mes", "min_mes")
    sr = [{"preco": nv["preco"], "tipo": nv["tipo"], "forca": nv.get("forca") or 1}
          for nv in niveis if nv["tipo"] in LINHAS]
    return JSONResponse({"par": par, "tf": tf, "candles": candles, "niveis": sr,
                         "scores": scores, "sync": sync, "vwap": vwap, "volume": volume})


@app.get("/grafico/{par}/{tf}", response_class=HTMLResponse)
def grafico(request: Request, par: str, tf: str):
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    if par not in _pares_validos() or tf not in _tfs_validos():
        raise HTTPException(status_code=404, detail="par/tf inválido")
    # Um par da B3 troca só entre símbolos/TFs da B3 (mercado separado); o forex, entre os seus.
    ehb3 = par in set(config_b3.pares_ativos())
    pares = list(config_b3.PARES_B3) if ehb3 else list(config.PARES)
    tfs = list(config_b3.TFS_COLETA_B3) if ehb3 else list(config.TFS_COLETA)
    return templates.TemplateResponse(
        request, "grafico.html",
        {"par": par, "tf": tf, "pares": pares, "tfs": tfs},
    )


@app.get("/trade/{trade_id}", response_class=HTMLResponse)
def raio_x_trade(request: Request, trade_id: int, antes: int = None, depois: int = None):
    """Raio-X de um trade: contexto (candles antes/depois), entrada/SL/saída, MAE/MFE e o
    'porquê entrou' — para auditar POR QUE perdeu e se cabe calibração ou ajuste técnico."""
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from ..grafico import grafico_trade_html

    return HTMLResponse(grafico_trade_html(trade_id, antes=antes, depois=depois))


# --------------------------------------------------------------------------- #
# Handler: 401 em rota HTML → redireciona ao login
# --------------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return auth.redirecionar_login()
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
