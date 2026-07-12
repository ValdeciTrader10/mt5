"""Autenticação do painel: senha via bcrypt + sessão por cookie assinado.

A sessão em si é gerida pelo SessionMiddleware do Starlette (cookie assinado com
SECRET_KEY). Aqui ficam a verificação de senha e a dependency que protege as rotas.
"""

import hmac
import logging

import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException

from .. import config

log = logging.getLogger("web.auth")


def verificar_credenciais(usuario: str, senha: str) -> bool:
    """True se usuário e senha conferem com o configurado no ambiente.

    Prioridade: PAINEL_SENHA_HASH (bcrypt) > PAINEL_SENHA (texto). O texto existe
    para facilitar o setup no Dokploy; o hash continua sendo o modo recomendado.
    """
    if usuario != config.PAINEL_USUARIO:
        return False
    if config.PAINEL_SENHA_HASH:
        try:
            return bcrypt.checkpw(
                senha.encode("utf-8"), config.PAINEL_SENHA_HASH.encode("utf-8")
            )
        except ValueError:
            log.error("PAINEL_SENHA_HASH inválido (não é um hash bcrypt).")
            return False
    if config.PAINEL_SENHA:
        # Comparação em tempo constante (evita timing attack).
        return hmac.compare_digest(senha, config.PAINEL_SENHA)
    log.error("Nem PAINEL_SENHA_HASH nem PAINEL_SENHA configurados — login negado.")
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
