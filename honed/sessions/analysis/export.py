"""Write inspectable WorkGraph bundles from sessions in the local index."""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..session import Session
from ..sessions import Sessions
from .decisiontrace import DecisionTrace
from .workgraph import WorkGraph


def _page(title: str, diagram: str, details: str) -> str:
    escaped_title = html.escape(title)
    escaped_details = html.escape(details)
    escaped_diagram = html.escape(diagram, quote=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title} · WorkGraph</title>
  <style>
    body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; color: #18212b; background: #f5f7fa; }}
    header {{ padding: 1rem 2rem; background: #fff; border-bottom: 1px solid #d9e0e8; }}
    main {{ padding: 1rem 2rem 3rem; }}
    .diagram {{ background: #fff; border: 1px solid #d9e0e8; border-radius: 8px; padding: 1.5rem; overflow: auto; }}
    .mermaid {{ min-width: max-content; }}
    a {{ color: #1456a0; }}
    pre {{ white-space: pre-wrap; }}
  </style>
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{ startOnLoad: true, securityLevel: "strict", theme: "neutral" }});
  </script>
</head>
<body>
  <header><a href="index.html">← All graphs</a><h1>{escaped_title}</h1><p>{escaped_details}</p></header>
  <main><div class="diagram"><pre class="mermaid">{escaped_diagram}</pre></div></main>
</body>
</html>
"""


def session_graph(history: Any, representation: str = "workgraph") -> WorkGraph | DecisionTrace:
    """The WorkGraph or DecisionTrace of a ``Session`` or a ``Thread``."""
    if representation not in {"workgraph", "decision-trace"}:
        raise ValueError("representation must be workgraph or decision-trace")
    session_id = getattr(history, "session_id", None) or history.id
    if representation == "workgraph":
        return WorkGraph.from_events(history.selected_events, session_id=session_id)
    return DecisionTrace.from_events(history.selected_events, session_id=session_id)


def export_workgraphs(
    store: Sessions | None,
    output_dir: str | Path,
    *,
    histories: Sequence[Any] | None = None,
    graphs: Sequence[WorkGraph | DecisionTrace] | None = None,
    references: Sequence[str] = (),
    harnesses: Sequence[str] = (),
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    png: bool = False,
    representation: str = "workgraph",
) -> list[dict[str, Any]]:
    """Export one graph per session (or per thread of ``histories``, ``Session`` or ``Thread`` objects) plus an
    HTML/Markdown index; session reads are immutable. Without ``histories``, ``store`` selects sessions by
    ``references`` or by the filters."""
    if representation not in {"workgraph", "decision-trace"}:
        raise ValueError("representation must be workgraph or decision-trace")
    output = Path(output_dir).expanduser().resolve()
    if histories is None:
        if store is None:
            raise ValueError("give histories, or a store to select sessions from")
        if references:
            histories = [store.get(reference) for reference in references]
        else:
            histories = [store.get(record.id) for record in
                         store.list(harness=list(harnesses) or None, since=since, until=until)[:limit]]  # fmt: skip
    if png and histories:
        import shutil

        if shutil.which("mmdc") is None:
            raise RuntimeError(
                "PNG export requires Mermaid CLI (`mmdc`) on PATH; install it with "
                "`npm install --global @mermaid-js/mermaid-cli`."
            )

    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for number, history in enumerate(histories):
        record = history.metadata
        graph = graphs[number] if graphs is not None else session_graph(history, representation)
        source_graph = graph if isinstance(graph, WorkGraph) else graph.source_graph
        thread = history.id if not isinstance(history, Session) else None
        stem = f"{representation}-session-{(getattr(history, 'session_id', None) or history.id)[:16]}"
        stem += f"-thread-{thread[:12]}" if thread else ""
        title = record.title or record.native_id or record.id
        diagram = graph.to_mermaid()
        details = (
            f"{record.harness} · {record.started_at or 'time unknown'} · "
            f"{source_graph.source_event_count} source events · "
            f"{source_graph.omitted_event_count} omitted · {len(graph.nodes)} trace nodes"
        )
        (output / f"{stem}.mmd").write_text(diagram, encoding="utf-8")
        (output / f"{stem}.html").write_text(_page(title, diagram, details), encoding="utf-8")
        if png:
            graph.render_png(output / f"{stem}.png")
        records.append({
            "session_id": getattr(history, "session_id", None) or history.id,
            "thread_id": thread,
            "native_id": record.native_id,
            "title": title,
            "harness": record.harness,
            "started_at": record.started_at,
            "source_events": source_graph.source_event_count,
            "omitted_events": source_graph.omitted_event_count,
            "visible_events": len(graph.nodes),
            "signals": sum(node.kind == "signal" for node in graph.nodes),
            "stem": stem,
        })

    rows = [
        f"# {representation} exports", "", f"Generated {len(records)} session graph(s).", "",
        ("Open `index.html` in a browser to inspect the diagrams. It loads Mermaid from jsDelivr; "
         "the `.mmd` files are standalone Mermaid source."), "",
        "| Started | Harness | Session | Events | Omitted | Signals | Graph |", "|---|---|---|---:|---:|---:|---|",
    ]
    for item in records:
        rows.append(
            f"| {item['started_at'] or ''} | {item['harness']} | `{item['native_id']}` | "
            f"{item['source_events']} | {item['omitted_events']} | {item['signals']} | "
            f"[{html.escape(str(item['title']))}]({item['stem']}.html) |"
        )
    (output / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    html_rows_list = []
    for item in records:
        png_link = f' · <a href="{item["stem"]}.png">PNG</a>' if png else ""
        html_rows_list.append(
            f'<tr><td>{html.escape(item["started_at"] or "")}</td>'
            f'<td>{html.escape(item["harness"])}</td><td>{html.escape(str(item["native_id"]))}</td>'
            f'<td>{item["source_events"]}</td><td>{item["omitted_events"]}</td><td>{item["signals"]}</td>'
            f'<td><a href="{item["stem"]}.html">{html.escape(str(item["title"]))}</a>{png_link}</td></tr>'
        )
    html_rows = "\n".join(html_rows_list)
    (output / "index.html").write_text(
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        f"<title>{html.escape(representation)} exports</title><style>"
        "body{font:15px system-ui;margin:2rem;color:#18212b}"
        "table{border-collapse:collapse}th,td{padding:.55rem .8rem;border-bottom:1px solid #d9e0e8;text-align:left}"
        f"th{{background:#f5f7fa}}</style><h1>{html.escape(representation)} exports</h1>"
        f"<p>{len(records)} session graph(s). Open a graph to inspect its Mermaid visualization.</p>"
        f"<table><thead><tr><th>Started</th><th>Harness</th><th>Session</th><th>Events</th>"
        f"<th>Omitted</th><th>Signals</th><th>Diagram</th></tr></thead><tbody>{html_rows}</tbody></table></html>\n",
        encoding="utf-8",
    )
    return records
