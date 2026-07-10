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

import json

from mutagen.agent.llm import LLM
from mutagen.agent.loop import run_loop
from mutagen.config import AppConfig
from mutagen.eval.benchmark import discover_targets, run_benchmark

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
            console.print(Panel(last.pytest_result.stdout or "", title="pytest stdout"))
            console.print(Panel(last.pytest_result.stderr or "", title="pytest stderr"))
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
) -> None:
    """Run the loop against every `target.py` under ROOT and summarize."""
    load_dotenv()
    cfg = AppConfig()
    if strings_are_behavior:
        cfg.mutation.disabled_types = []
    llm = LLM(cfg)
    targets = discover_targets(root)
    console.print(f"[bold]discovered[/] {len(targets)} targets under {root}")

    with console.status("[cyan]running benchmark…"):
        report = run_benchmark(
            targets=targets, workdir_root=workdir_root, cfg=cfg, llm=llm, max_rounds=max_rounds,
        )

    t = Table(title="benchmark", show_lines=False)
    t.add_column("target")
    t.add_column("stopped")
    t.add_column("kill_rate")
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
        t.add_row(str(e.target), stopped, kr, rounds, tok, f"{e.wall_clock_s:.1f}")
    console.print(t)
    console.print(f"[bold]mean kill_rate[/] {report.mean_kill_rate * 100:.1f}%")


@app.command("version")
def version_cmd() -> None:
    """Print mutagen version."""
    from mutagen import __version__
    console.print(__version__)


if __name__ == "__main__":
    app()
