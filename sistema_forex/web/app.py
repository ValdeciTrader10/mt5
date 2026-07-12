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
        trades = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        analise_pares = [analise.resumo_par(conn, par) for par in config.PARES]

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
        "decisoes": decisoes,
        "trades": trades,
        "mt5_ok": mt5_ok,
        "mt5": mt5_info,
        "pares": config.PARES,
        "tfs": config.TFS_COLETA,
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
    return templates.TemplateResponse(request, "dashboard.html", dados)


@app.get("/api/status")
def api_status(request: Request):
    if not auth.esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return JSONResponse(_status_dados())


@app.get("/grafico/{par}/{tf}", response_class=HTMLResponse)
def grafico(request: Request, par: str, tf: str):
    if not auth.esta_logado(request):
        return auth.redirecionar_login()
    from ..grafico import grafico_html

    if par not in config.PARES or tf not in config.TFS_COLETA:
        raise HTTPException(status_code=404, detail="par/tf inválido")
    return HTMLResponse(grafico_html(par, tf))


# --------------------------------------------------------------------------- #
# Handler: 401 em rota HTML → redireciona ao login
# --------------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return auth.redirecionar_login()
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
