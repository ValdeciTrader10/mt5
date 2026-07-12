"""Fase 3 (versão mínima) — Gráfico de candles offline.

Gera HTML com Plotly EMBUTIDO (sem CDN — padrão dos sistemas Empenho), lendo os
candles do banco. Nesta fundação desenha apenas os candles; os níveis calculados
(S/R, OB, FVG, swings, labels SMC) entram quando a Fase 2 (motor) estiver pronta.

Uso:
    python -m sistema_forex.grafico EURUSD# M5
    # ou importado: html = grafico_html("EURUSD#", "M5", limite=500)
"""

import logging
import sys

from . import config, db

log = logging.getLogger("grafico")


def _buscar_candles(conn, par: str, tf: str, limite: int):
    rows = conn.execute(
        """
        SELECT time_utc, open, high, low, close
        FROM candles
        WHERE par = ? AND tf = ?
        ORDER BY time_utc DESC
        LIMIT ?
        """,
        (par, tf, limite),
    ).fetchall()
    return list(reversed(rows))  # cronológico


def grafico_html(par: str, tf: str, limite: int = 500) -> str:
    """Retorna o HTML completo (offline) do gráfico de candles par/tf."""
    import plotly.graph_objects as go
    from datetime import datetime

    with db.sessao() as conn:
        candles = _buscar_candles(conn, par, tf, limite)

    if not candles:
        return (
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<h3>Sem candles para {par} {tf}</h3>"
            "<p>O coletor ainda não gravou dados para este par/timeframe.</p>"
            "</body></html>"
        )

    tempos = [datetime.utcfromtimestamp(c["time_utc"]) for c in candles]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=tempos,
                open=[c["open"] for c in candles],
                high=[c["high"] for c in candles],
                low=[c["low"] for c in candles],
                close=[c["close"] for c in candles],
                name=par,
            )
        ]
    )
    fig.update_layout(
        title=f"{par} — {tf} (últimos {len(candles)} candles)",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        margin=dict(l=40, r=20, t=50, b=40),
        height=640,
    )
    # include_plotlyjs=True embute a lib inteira → funciona offline, sem CDN.
    return fig.to_html(include_plotlyjs=True, full_html=True)


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    par = sys.argv[1] if len(sys.argv) > 1 else config.PARES[0]
    tf = sys.argv[2] if len(sys.argv) > 2 else config.TF_OPERACAO
    html = grafico_html(par, tf)
    saida = config.DADOS_DIR / f"grafico_{par.replace('#','')}_{tf}.html"
    config.DADOS_DIR.mkdir(parents=True, exist_ok=True)
    saida.write_text(html, encoding="utf-8")
    print(f"Gráfico salvo em {saida}")


if __name__ == "__main__":
    main()
