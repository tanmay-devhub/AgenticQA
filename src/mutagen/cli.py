"""Typer CLI: `mutagen run <target>`.

Phase 2: multi-round generate -> pytest -> mutmut -> classify -> plan loop.
Round 1 is Tier-1 happy-path generation. Rounds 2..N are Tier-2, driven by
classified real_gap survivors from the previous round.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import json

from mutagen.agent.llm import LLM
from mutagen.agent.loop import run_loop
from mutagen.config import AppConfig
from mutagen.eval.benchmark import discover_targets, run_ablation, run_benchmark

# Windows: rich renders survivor diffs that may contain unicode (e.g. Greek
# letters in LLM-generated test data). cp1252 stdout blows up on those --
# reconfigure our own I/O to UTF-8 and replace anything unencodable.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _default_workdir(target: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    root = Path.cwd() / "runs" / f"{target.stem}-{ts}"
    return root


@app.command("run")
def run_cmd(
    target: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    workdir: Path | None = typer.Option(None, "--workdir", "-w", help="Working directory"),
    max_rounds: int | None = typer.Option(
        None, "--max-rounds", "-r",
        help="Override loop.max_rounds. 1 = Phase 1 one-shot behavior.",
    ),
    strings_are_behavior: bool = typer.Option(
        False,
        "--strings-are-behavior",
        help="Keep string/fstring mutations enabled (for template/formatter code).",
    ),
    json_out: Path | None = typer.Option(
        None, "--json",
        help="Write the full LoopResult as JSON to this path (in addition to workdir/run.json).",
    ),
) -> None:
    """Generate tests for TARGET, run pytest, then mutmut, iterating until plateau."""
    load_dotenv()
    cfg = AppConfig()
    if strings_are_behavior:
        cfg.mutation.disabled_types = []
    llm = LLM(cfg)
    wd = workdir or _default_workdir(target)
    rounds_to_run = max_rounds if max_rounds is not None else cfg.loop.max_rounds

    console.print(Panel.fit(
        f"[bold]target[/]     {target}\n"
        f"[bold]workdir[/]    {wd}\n"
        f"[bold]codegen[/]    {cfg.llm.codegen.model}\n"
        f"[bold]planner[/]    {cfg.llm.planner.model}\n"
        f"[bold]max_rounds[/] {rounds_to_run}",
        title="mutagen run",
    ))

    with console.status("[cyan]running loop…"):
        result = run_loop(target=target, workdir=wd, cfg=cfg, llm=llm, max_rounds=rounds_to_run)

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")

    # Per-round table.
    if result.rounds:
        t = Table(title="rounds", show_lines=False)
        t.add_column("#")
        t.add_column("tier")
        t.add_column("pytest")
        t.add_column("repaired")
        t.add_column("killed/total")
        t.add_column("survived")
        t.add_column("kill_rate")
        t.add_column("elapsed_s")
        t.add_column("tokens (cg+pl)")
        for r in result.rounds:
            if r.report is not None:
                killed_total = f"{r.report.killed}/{r.report.total}"
                survived = str(r.report.survived)
                kill_rate = f"{r.report.kill_rate * 100:.1f}%"
            else:
                killed_total = survived = kill_rate = "-"
            tokens = f"{r.usage.codegen.total_tokens}+{r.usage.planner.total_tokens}"
            t.add_row(
                str(r.index),
                f"T{r.tier}",
                "ok" if r.pytest_ok else "FAIL",
                "yes" if r.repaired else "-",
                killed_total,
                survived,
                kill_rate,
                f"{r.elapsed_s:.1f}",
                tokens,
            )
        console.print(t)

    total = result.total_usage
    console.print(
        f"[bold]LLM spend[/] codegen={total.codegen.total_tokens} tok "
        f"({total.codegen.calls} calls)  planner={total.planner.total_tokens} tok "
        f"({total.planner.calls} calls)"
    )
    console.print(f"[bold]stopped:[/] {result.stopped_reason}")

    if not result.pytest_ok:
        last = result.rounds[-1] if result.rounds else None
        if last is not None:
            console.print("[red]pytest failed on generated tests.[/]")
            # Raw pytest output can contain '[i+1]'-style code that Rich would
            # try to interpret as markup and eat. Wrap in Text so brackets are
            # rendered literally.
            console.print(Panel(Text(last.pytest_result.stdout or ""), title="pytest stdout"))
            console.print(Panel(Text(last.pytest_result.stderr or ""), title="pytest stderr"))
        raise typer.Exit(code=2)

    report = result.final_report
    if report is None:
        raise typer.Exit(code=2)
    console.print(Panel.fit(report.format_summary(), title="final mutation report"))

    if report.survivors:
        t = Table(title="final survivors", show_lines=False)
        t.add_column("id")
        t.add_column("file")
        t.add_column("line")
        t.add_column("kind")
        t.add_column("diff", overflow="fold")
        for m in report.survivors:
            t.add_row(
                m.id,
                m.file or "-",
                str(m.line) if m.line is not None else "-",
                m.kind,
                (m.diff or "").strip()[:400],
            )
        console.print(t)


@app.command("bench")
def bench_cmd(
    root: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    workdir_root: Path = typer.Option(
        Path.cwd() / "runs" / "bench",
        "--workdir-root", "-w",
        help="Where to place per-target working directories.",
    ),
    max_rounds: int | None = typer.Option(None, "--max-rounds", "-r"),
    strings_are_behavior: bool = typer.Option(False, "--strings-are-behavior"),
    ablation: bool = typer.Option(
        False,
        "--ablation",
        help=(
            "Instead of a single-configuration benchmark, run each target "
            "under T1-only / T1+T2 / full-tier and print the comparison. "
            "Uses 3x the tokens of a normal bench."
        ),
    ),
) -> None:
    """Run the loop against every `target.py` under ROOT and summarize."""
    load_dotenv()
    cfg = AppConfig()
    if strings_are_behavior:
        cfg.mutation.disabled_types = []
    llm = LLM(cfg)
    targets = discover_targets(root)
    console.print(f"[bold]discovered[/] {len(targets)} targets under {root}")

    if ablation:
        with console.status("[cyan]running ablation grid…"):
            ab_report = run_ablation(
                targets=targets, workdir_root=workdir_root, cfg=cfg, llm=llm,
            )
        _print_ablation_report(ab_report)
        return

    with console.status("[cyan]running benchmark…"):
        report = run_benchmark(
            targets=targets, workdir_root=workdir_root, cfg=cfg, llm=llm, max_rounds=max_rounds,
        )

    t = Table(title="benchmark", show_lines=False)
    t.add_column("target")
    t.add_column("stopped")
    t.add_column("kill_rate")
    t.add_column("seeded_bugs")
    t.add_column("rounds")
    t.add_column("tokens")
    t.add_column("wall_s")
    for e in report.entries:
        if e.result and e.result.final_report:
            kr = f"{e.result.final_report.kill_rate * 100:.1f}%"
            rounds = str(len(e.result.rounds))
            total = e.result.total_usage
            tok = str(total.codegen.total_tokens + total.planner.total_tokens)
            stopped = e.result.stopped_reason
        else:
            kr = rounds = tok = "-"
            stopped = e.error or "no result"
        if e.seeded_bugs:
            caught = sum(1 for s in e.seeded_bugs if s.caught)
            seeded = f"{caught}/{len(e.seeded_bugs)}"
        else:
            seeded = "-"
        t.add_row(str(e.target), stopped, kr, seeded, rounds, tok, f"{e.wall_clock_s:.1f}")
    console.print(t)
    console.print(f"[bold]mean kill_rate[/] {report.mean_kill_rate * 100:.1f}%")
    if report.mean_seeded_bug_catch_rate is not None:
        console.print(
            f"[bold]mean seeded-bug catch rate[/] {report.mean_seeded_bug_catch_rate * 100:.1f}%"
        )


@app.command("web")
def web_cmd(
    runs_root: Path = typer.Option(
        Path.cwd() / "runs",
        "--runs-root", "-r",
        help="Folder containing per-run artifacts.",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port", "-p"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in the default browser."),
) -> None:
    """Launch the read-only mutagen dashboard."""
    import uvicorn
    from mutagen.web import create_app

    runs_root.mkdir(parents=True, exist_ok=True)
    fastapi_app = create_app(runs_root)
    url = f"http://{host}:{port}"
    console.print(Panel.fit(
        f"[bold]dashboard[/] {url}\n[bold]runs_root[/]  {runs_root.resolve()}",
        title="mutagen web",
    ))
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")


def _print_ablation_report(report) -> None:
    """Print the (target x config) ablation grid + per-config aggregates."""
    grid = Table(title="ablation grid", show_lines=False)
    grid.add_column("target")
    grid.add_column("config")
    grid.add_column("kill_rate")
    grid.add_column("seeded_bugs")
    grid.add_column("tokens")
    grid.add_column("wall_s")
    for e in report.entries:
        kr = f"{e.kill_rate * 100:.1f}%" if e.kill_rate is not None else "-"
        seeded = f"{e.seeded_bug_catch_rate * 100:.1f}%" if e.seeded_bug_catch_rate is not None else "-"
        grid.add_row(str(e.target), e.config.label, kr, seeded, str(e.tokens), f"{e.wall_clock_s:.1f}")
    console.print(grid)

    agg = Table(title="per-config aggregates", show_lines=False)
    agg.add_column("config")
    agg.add_column("mean_kill_rate")
    agg.add_column("mean_seeded_catch")
    agg.add_column("mean_tokens")
    agg.add_column("n_targets")
    for label, row in report.per_config_summary().items():
        kr = f"{row['mean_kill_rate'] * 100:.1f}%" if row['mean_kill_rate'] is not None else "-"
        seeded = f"{row['mean_seeded_bug_catch_rate'] * 100:.1f}%" if row['mean_seeded_bug_catch_rate'] is not None else "-"
        agg.add_row(label, kr, seeded, f"{row['mean_tokens']:.0f}", str(row['n_targets']))
    console.print(agg)


@app.command("mcp")
def mcp_cmd(
    runs_root: Path = typer.Option(
        Path.cwd() / "runs",
        "--runs-root", "-r",
        help="Where qa_run_loop / qa_mutation_score should place workdirs.",
    ),
    transport: str = typer.Option(
        "stdio", "--transport",
        help="stdio (Claude Desktop / Cursor), sse, or streamable-http.",
    ),
) -> None:
    """Serve mutagen as an MCP tool provider.

    Default stdio transport is what Claude Desktop and Cursor plug into. For
    HTTP-based clients, pass --transport streamable-http.
    """
    load_dotenv()
    runs_root.mkdir(parents=True, exist_ok=True)
    from mutagen.mcp import create_server
    server = create_server(runs_root)
    # FastMCP.run picks up transport by keyword.
    server.run(transport=transport)


@app.command("version")
def version_cmd() -> None:
    """Print mutagen version."""
    from mutagen import __version__
    console.print(__version__)


if __name__ == "__main__":
    app()
