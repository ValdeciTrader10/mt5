"""Autenticação do painel: senha via bcrypt + sessão por cookie assinado.

A sessão em si é gerida pelo SessionMiddleware do Starlette (cookie assinado com
SECRET_KEY). Aqui ficam a verificação de senha e a dependency que protege as rotas.
"""

import logging

import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException

from .. import config

log = logging.getLogger("web.auth")


def verificar_credenciais(usuario: str, senha: str) -> bool:
    """True se usuário e senha conferem com o configurado no .env."""
    if not config.PAINEL_SENHA_HASH:
        log.error("PAINEL_SENHA_HASH não configurado — login sempre negado.")
        return False
    if usuario != config.PAINEL_USUARIO:
        return False
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), config.PAINEL_SENHA_HASH.encode("utf-8"))
    except ValueError:
        log.error("PAINEL_SENHA_HASH inválido (não é um hash bcrypt).")
        return False


def esta_logado(request: Request) -> bool:
    return bool(request.session.get("usuario"))


def exigir_login(request: Request):
    """Dependency: bloqueia acesso sem sessão.

    Em rota HTML redireciona para /login; a exceção 401 é convertida no handler.
    """
    if not esta_logado(request):
        raise HTTPException(status_code=401, detail="login necessário")
    return request.session["usuario"]


def redirecionar_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)
