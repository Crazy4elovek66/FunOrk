"""HTML-отчет FunOrk с группировкой по вердиктам."""

from __future__ import annotations

from html import escape
from pathlib import Path

from app.config import config
from app.reports.common import HTML_BLOCKS, REPORT_LABELS, load_report_rows, report_filename


def export_to_html() -> Path:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = config.REPORTS_DIR / report_filename("html")
    rows = load_report_rows()

    blocks = []
    for title, verdict in HTML_BLOCKS:
        block_rows = [row for row in rows if row.get("verdict") == verdict]
        blocks.append(_render_block(title, block_rows))

    report_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="ru">',
                "<head>",
                '<meta charset="utf-8">',
                "<title>Отчет FunOrk</title>",
                "<style>",
                "body{font-family:Arial,sans-serif;margin:32px;color:#1f2933;background:#f7f7f4}",
                "h1{font-size:28px;margin:0 0 24px}",
                "section{margin:0 0 32px}",
                "h2{font-size:20px;margin:0 0 12px}",
                "table{width:100%;border-collapse:collapse;background:#fff}",
                "th,td{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:13px}",
                "th{background:#333;color:#fff;text-align:left}",
                ".empty{background:#fff;padding:16px;border:1px solid #ddd}",
                "a{color:#0b5cad}",
                "</style>",
                "</head>",
                "<body>",
                "<h1>Отчет FunOrk</h1>",
                *blocks,
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )
    return report_path


def _render_block(title: str, rows: list[dict[str, object]]) -> str:
    if not rows:
        return f"<section><h2>{escape(title)}</h2><div class=\"empty\">Подходящих направлений пока нет.</div></section>"

    headers = (
        "possible_kwork_service_title",
        "source_category",
        "buy_price",
        "sell_price",
        "estimated_margin_percent",
        "opportunity_score",
        "risk_reason",
        "safe_wording",
        "forbidden_buyer_requests",
        "source_url",
    )
    header_html = "".join(f"<th>{escape(REPORT_LABELS[key])}</th>" for key in headers)
    rows_html = []
    for row in rows:
        cells = []
        for key in headers:
            value = "" if row.get(key) is None else str(row.get(key))
            if key == "source_url" and value:
                value = f'<a href="{escape(value)}">{escape(value)}</a>'
            else:
                value = escape(value)
            cells.append(f"<td>{value}</td>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    return (
        f"<section><h2>{escape(title)}</h2><table><thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></section>"
    )
