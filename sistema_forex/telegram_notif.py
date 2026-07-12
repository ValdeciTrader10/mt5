"""Notificações Telegram.

Stub da fundação — a integração completa (abertura/saída/trailing/DD/resumo diário
com anti-spam por flags booleanas) entra na Fase 5. Aqui fica só o envio básico,
já com proteção anti-spam para reuso posterior.
"""

import logging
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("telegram")

_ultimo_por_chave: dict[str, str] = {}


def enviar(texto: str, chave_antispam: str | None = None) -> bool:
    """Envia mensagem ao Telegram. Retorna True se enviada.

    Se `chave_antispam` for dada, não reenvia a mesma mensagem para a mesma chave
    (evita o spam por variável flutuante — lição do MASMC).
    """
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT:
        log.debug("Telegram não configurado — mensagem ignorada: %s", texto[:60])
        return False
    if chave_antispam is not None and _ultimo_por_chave.get(chave_antispam) == texto:
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    dados = urllib.parse.urlencode(
        {"chat_id": config.TELEGRAM_CHAT, "text": texto, "parse_mode": "HTML"}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=dados, timeout=10) as resp:
            ok = resp.status == 200
        if ok and chave_antispam is not None:
            _ultimo_por_chave[chave_antispam] = texto
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("Falha ao enviar Telegram: %s", e)
        return False
