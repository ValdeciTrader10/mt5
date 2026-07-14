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

    python -m sistema_forex.manutencao status    # só conta as linhas (não apaga)
    python -m sistema_forex.manutencao reset      # fecha posições do robô + BACKUP + limpa
    python -m sistema_forex.manutencao reset-b3   # BACKUP + limpa SÓ o livro de sombra da B3

`reset` faz tudo em ordem segura: (1) FECHA as posições do robô no broker (magic, p/ não
ficarem órfãs); (2) BACKUP do banco; (3) apaga trades/decisões/derivados. Depois, REDEPLOY
no Dokploy para o executor reiniciar com estado limpo e recomeçar a catalogar.
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


def fechar_posicoes_robo() -> int:
    """Fecha no broker as posições do robô (magic), para não ficarem órfãs após o reset.
    Precisa do MT5 (roda dentro do stack). Sem MT5/erro → não derruba o reset, só avisa."""
    try:
        from . import mt5_bridge
        posic = mt5_bridge.posicoes(magic=config.MAGIC)
    except Exception as e:  # noqa: BLE001 - sem MT5 aqui: siga o reset e feche manualmente no VNC
        print(f"  (MT5 indisponível para fechar posições: {e} — feche manualmente no MT5 se houver)")
        return 0
    fechadas = 0
    for p in posic:
        try:
            mt5_bridge.fechar(p["ticket"], config.MAGIC)
            fechadas += 1
            print(f"  fechada posição {p['ticket']} {p['simbolo']} {p['direcao']}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! falha ao fechar {p['ticket']}: {e}")
    return fechadas


def resetar(conn) -> dict:
    """Apaga as tabelas de operação + derivadas; PRESERVA `candles`. Retorna o nº apagado
    por tabela. (Função separada do backup para ser testável isoladamente.)"""
    apagados = {}
    for t in TABELAS_LIMPAR:
        apagados[t] = conn.execute(f"DELETE FROM {t}").rowcount
    conn.commit()
    return apagados


def resetar_forex(conn) -> dict:
    """Apaga SÓ o livro do FOREX (`trades`/`decisoes` com mercado='forex' ou legado NULL);
    PRESERVA a B3 (mercado='b3'), os `candles` e as derivadas (o motor regenera). Simétrico ao
    `resetar_b3` — usado pelo botão da página do FOREX para não encostar na B3."""
    apagados = {}
    for t in TABELAS_OPERACAO:
        apagados[t] = conn.execute(
            f"DELETE FROM {t} WHERE mercado='forex' OR mercado IS NULL").rowcount
    conn.commit()
    return apagados


def resetar_b3(conn) -> dict:
    """Apaga SÓ o livro de sombra da B3 (`trades`/`decisoes` com mercado='b3'); PRESERVA o
    forex e os `candles`. Serve para recomeçar a sombra WIN/WDO limpa após um fix — ex.: a
    correção do tick-fantasma de leilão que inflava as vencedoras vendidas. Retorna o nº
    apagado por tabela."""
    apagados = {}
    for t in TABELAS_OPERACAO:
        apagados[t] = conn.execute(f"DELETE FROM {t} WHERE mercado='b3'").rowcount
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
        print("Fechando posições do robô no broker (magic %d)…" % config.MAGIC)
        n_fechadas = fechar_posicoes_robo()
        print(f"Posições fechadas: {n_fechadas}")
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
        print("\n⚠️ Agora: REDEPLOY no Dokploy para o executor reiniciar com estado limpo "
              "(a memória de posições zera e ele recomeça a catalogar).")
        return 0

    if cmd == "reset-b3":
        bak = _backup(config.DB_PATH)
        print(f"Backup criado: {bak}")
        with db.sessao() as conn:
            antes = contar(conn)
            apagados = resetar_b3(conn)
            depois = contar(conn)
        print("Apagados (só mercado='b3'):", {k: v for k, v in apagados.items() if v})
        print(f"trades {antes['trades']}→{depois['trades']} · "
              f"decisoes {antes['decisoes']}→{depois['decisoes']} · "
              f"candles PRESERVADOS: {depois['candles']}")
        print("\n⚠️ Agora: REDEPLOY no Dokploy para o executor_b3 reiniciar sem as posições "
              "corrompidas em memória (ele recomeça a catalogar WIN/WDO já com o tick-fantasma "
              "de leilão corrigido).")
        return 0

    print("uso: python -m sistema_forex.manutencao [status|reset|reset-b3]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
