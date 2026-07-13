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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import analise, config, db, mt5_bridge
from . import auth

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Painel Forex M5", docs_url=None, redoc_url=None)
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
        decisoes = conn.execute("SELECT COUNT(*) AS n FROM decisoes").fetchone()["n"]
        trades = conn.execute(
            "SELECT COUNT(*) AS n FROM trades WHERE fechamento_utc IS NOT NULL"
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
            }
            for r in conn.execute(
                "SELECT par, direcao, estrategia, preco_entrada, sl_servidor, abertura_utc, simulado "
                "FROM trades WHERE fechamento_utc IS NULL ORDER BY abertura_utc DESC"
            ).fetchall()
        ]
        trades_recentes = [
            {
                "par": r["par"], "direcao": r["direcao"],
                "pips": r["pips"], "lucro": r["lucro_usd"], "motivo": r["motivo_saida"],
                "quando": datetime.utcfromtimestamp(r["fechamento_utc"]).strftime("%m-%d %H:%M"),
                "simulado": bool(r["simulado"]),
            }
            for r in conn.execute(
                "SELECT par, direcao, pips, lucro_usd, motivo_saida, fechamento_utc, simulado "
                "FROM trades WHERE fechamento_utc IS NOT NULL ORDER BY fechamento_utc DESC LIMIT 15"
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
                "SELECT par, time_utc, estrategia, direcao, resultado, motivo "
                "FROM decisoes ORDER BY time_utc DESC, id DESC LIMIT 15"
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


def _sessao(hora_utc: int) -> str:
    """Sessão de mercado pela hora UTC de abertura (foco Londres/NY, onde há liquidez)."""
    if 7 <= hora_utc <= 11:
        return "Londres (07–11)"
    if 12 <= hora_utc <= 15:
        return "Londres/NY (12–15)"
    if 16 <= hora_utc <= 20:
        return "Nova York (16–20)"
    if 0 <= hora_utc <= 6:
        return "Ásia (00–06)"
    return "Fora de sessão (21–23)"


def _curva_capital(trades: list, base: float) -> dict:
    """Curva de capital (ordem cronológica de fechamento) + drawdown máximo.

    `base` = saldo inicial de referência (equity acumulada parte dele). Retorna a série
    para plotar, o DD máximo em USD/%, e o recovery factor (lucro/|maxDD|)."""
    from datetime import datetime, timezone

    ordenados = sorted([t for t in trades if t["fechamento_utc"]], key=lambda t: t["fechamento_utc"])
    eq = base
    pico = base
    max_dd = 0.0
    serie = []
    for t in ordenados:
        eq += t["lucro_usd"] or 0
        pico = max(pico, eq)
        dd = eq - pico  # ≤ 0
        max_dd = min(max_dd, dd)
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
        "max_dd_pct": round(100 * max_dd / pico, 2) if pico else 0.0,
        "recovery_factor": round(lucro / abs(max_dd), 2) if max_dd < 0 else None,
    }


def _analitico(de: str = "", ate: str = "") -> dict:
    """Estatísticas dos trades fechados no intervalo [de, ate] (datas ISO, opcionais)."""
    de_e, ate_e = _epoch(de), _epoch(ate, fim=True)
    cond, args = ["fechamento_utc IS NOT NULL"], []
    if de_e:
        cond.append("fechamento_utc >= ?"); args.append(de_e)
    if ate_e:
        cond.append("fechamento_utc <= ?"); args.append(ate_e)
    where = " AND ".join(cond)
    with db.sessao() as conn:
        rows = conn.execute(
            f"SELECT id, par, tf, estrategia, direcao, pips, lucro_usd, motivo_saida, simulado, "
            f"mae_r, mfe_r, regime_entrada, abertura_utc, fechamento_utc FROM trades "
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
            "motivo": t["motivo_saida"], "simulado": bool(t["simulado"]),
            "mae_r": t["mae_r"], "mfe_r": t["mfe_r"], "regime": t["regime"], "sessao": t["sessao"],
        }
        for t in trades[:300]
    ]
    for t in trades:  # rótulo estável do TF de operação (default M5 p/ trades antigos)
        t["tf"] = t["tf"] or "M5"
    por_estrategia = _por(trades, "estrategia")
    for r in por_estrategia:  # rótulo amigável (agrupa pelo código, exibe o nome bonito)
        r["chave"] = config.nome_estrategia(r["chave"])
    return {
        "de": de, "ate": ate,
        "geral": _agregar(trades),
        "curva": _curva_capital(trades, config.SALDO_SIMULADO),
        "por_estrategia": por_estrategia,
        "por_estrategia_tf": _por_estrategia_tf(trades),
        "por_timeframe": _por(trades, "tf"),
        "por_regime": _por(trades, "regime"),
        "por_sessao": _por(trades, "sessao"),
        "por_par": _por(trades, "par"),
        "por_motivo": _por(trades, "motivo_norm"),
        "trades": lista,
    }


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
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
        request, "auditoria.html", {"dados": d, "texto": texto, "de": de, "ate": ate}
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


@app.get("/grafico/{par}/{tf}", response_class=HTMLResponse)
def grafico(request: Request, par: str, tf: str):
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from ..grafico import grafico_html

    if par not in config.PARES or tf not in config.TFS_COLETA:
        raise HTTPException(status_code=404, detail="par/tf inválido")
    return HTMLResponse(grafico_html(par, tf))


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
