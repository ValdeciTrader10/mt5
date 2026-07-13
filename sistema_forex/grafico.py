"""Gráfico de candles offline + níveis do motor (Fase 2/3).

Gera HTML com Plotly EMBUTIDO (sem CDN — padrão dos sistemas Empenho), lendo os
candles do banco e desenhando por cima os níveis calculados pelo motor: suportes,
resistências e FVGs (zonas). Mostra os níveis daquele TF de origem.

Uso:
    python -m sistema_forex.grafico EURUSD# M5
    # ou importado: html = grafico_html("EURUSD#", "M5", limite=500)
"""

import logging
import sys

from . import analise, config, db

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
        # S/R agora vem só de TFs fortes (H1/D1/W1). Trazemos TODOS os níveis do par
        # (não só os do TF do gráfico) para os S/R fortes aparecerem também no M5.
        niveis = analise.niveis_ativos(conn, par)

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
    # --- Níveis do motor desenhados por cima ---
    x0, x1 = tempos[0], tempos[-1]
    cores = {"suporte": "#3fb950", "resistencia": "#f85149"}
    for nv in niveis:
        tipo = nv["tipo"]
        if tipo in cores:
            m = nv.get("meta") or {}
            # Linha mais grossa para S/R mais fortes (força maior = zona mais respeitada).
            lw = 1 + min(2, int((nv.get("forca") or 1) / 4))
            det = f"·{m.get('toques', 0)}t·{int((m.get('respeito') or 0) * 100)}%rej" if m else ""
            fig.add_hline(
                y=nv["preco"], line_color=cores[tipo], line_width=lw,
                line_dash="dot", opacity=0.6,
                annotation_text=f"{tipo[:3].upper()} {m.get('tf', '')}·f{nv['forca']}{det}",
                annotation_position="right",
                annotation_font_size=9, annotation_font_color=cores[tipo],
            )
        elif tipo.startswith("fvg") and nv.get("preco2") is not None and nv.get("tf_origem") == tf:
            alta = tipo.endswith("bull")
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=nv["preco"], y1=nv["preco2"],
                line_width=0, layer="below",
                fillcolor="rgba(63,185,80,0.10)" if alta else "rgba(248,81,73,0.10)",
            )

    fvg_n = sum(1 for nv in niveis if nv["tipo"].startswith("fvg"))
    sr_n = sum(1 for nv in niveis if nv["tipo"] in cores)
    fig.update_layout(
        title=f"{par} — {tf} · {len(candles)} candles · {sr_n} níveis S/R · {fvg_n} FVGs",
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
