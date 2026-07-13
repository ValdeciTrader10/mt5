"""Manutenção do banco — RESET dos dados de operação, com BACKUP automático.

Pedido do dono (13/07): depois dos ajustes de fuso/auditoria, zerar os dados de operação
para começar a coletar dados CORRETOS daqui para frente.

O que apaga (dados de operação — o que alimenta auditoria/painel):
  - `trades`   : operações (sombra e real)
  - `decisoes` : log de decisões do estrategista
  - `niveis`, `estrutura`, `regime_log` : DERIVADOS — o motor (analise) regenera sozinho.

O que PRESERVA:
  - `candles` : dados de MERCADO (corretos, na hora do servidor) — apagar só forçaria um
    re-backfill lento sem ganho.

Antes de apagar, faz um BACKUP consistente do banco (API de backup do SQLite, segura em WAL).

    python -m sistema_forex.manutencao status   # só conta as linhas (não apaga)
    python -m sistema_forex.manutencao reset     # BACKUP + apaga trades/decisões/derivados

⚠️ SEQUÊNCIA SEGURA (para não deixar posição órfã nem estado velho na memória):
  1) No MT5 (VNC), feche as posições do robô abertas (magic 500250), se houver.
  2) Rode `reset` (faz backup e limpa).
  3) Redeploy no Dokploy — o executor reinicia com estado limpo e recomeça a catalogar.
"""

import sqlite3
import sys
from datetime import datetime

from . import config, db

# Tabelas de OPERAÇÃO (o que o dono quer zerar) e DERIVADAS (regeneram sozinhas). `candles` fica.
TABELAS_OPERACAO = ["trades", "decisoes"]
TABELAS_DERIVADAS = ["niveis", "estrutura", "regime_log"]
TABELAS_LIMPAR = TABELAS_OPERACAO + TABELAS_DERIVADAS


def contar(conn) -> dict:
    """Contagem de linhas por tabela (inclui `candles`, que é preservada)."""
    saida = {}
    for t in TABELAS_LIMPAR + ["candles"]:
        try:
            saida[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        except sqlite3.Error:
            saida[t] = None
    return saida


def _backup(db_path) -> str:
    """Backup consistente do banco (API de backup do SQLite — segura mesmo com WAL/serviços
    escrevendo). Retorna o caminho do arquivo de backup."""
    src = str(db_path)
    dst = f"{src}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    with sqlite3.connect(src) as origem, sqlite3.connect(dst) as destino:
        origem.backup(destino)
    return dst


def resetar(conn) -> dict:
    """Apaga as tabelas de operação + derivadas; PRESERVA `candles`. Retorna o nº apagado
    por tabela. (Função separada do backup para ser testável isoladamente.)"""
    apagados = {}
    for t in TABELAS_LIMPAR:
        apagados[t] = conn.execute(f"DELETE FROM {t}").rowcount
    conn.commit()
    return apagados


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        with db.sessao() as conn:
            for t, n in contar(conn).items():
                print(f"  {t:12} {n}")
        return 0

    if cmd == "reset":
        bak = _backup(config.DB_PATH)
        print(f"Backup criado: {bak}")
        with db.sessao() as conn:
            antes = contar(conn)
            apagados = resetar(conn)
            depois = contar(conn)
        print("Apagados:", {k: v for k, v in apagados.items() if v})
        print(f"trades {antes['trades']}→{depois['trades']} · "
              f"decisoes {antes['decisoes']}→{depois['decisoes']} · "
              f"candles PRESERVADOS: {depois['candles']}")
        print("\n⚠️ Agora: (1) feche no MT5 as posições do robô (magic %d), se houver; "
              "(2) redeploy no Dokploy para o executor reiniciar limpo." % config.MAGIC)
        return 0

    print("uso: python -m sistema_forex.manutencao [status|reset]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
