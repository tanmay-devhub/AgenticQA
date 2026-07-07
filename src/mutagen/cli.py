"""Typer CLI: `mutagen run <target>`.

Phase 1: one-shot generate -> pytest -> mutmut -> print kill rate.
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

from mutagen.agent.llm import LLM
from mutagen.agent.loop import one_shot
from mutagen.config import AppConfig

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
    strings_are_behavior: bool = typer.Option(
        False,
        "--strings-are-behavior",
        help="Keep string/fstring mutations enabled (for template/formatter code).",
    ),
) -> None:
    """Generate T1 tests for TARGET, run pytest, then mutmut, print kill rate."""
    load_dotenv()
    cfg = AppConfig()
    if strings_are_behavior:
        cfg.mutation.disabled_types = []
    llm = LLM(cfg)
    wd = workdir or _default_workdir(target)

    console.print(Panel.fit(
        f"[bold]target[/]  {target}\n"
        f"[bold]workdir[/] {wd}\n"
        f"[bold]codegen[/] {cfg.llm.codegen.model}\n"
        f"[bold]planner[/] {cfg.llm.planner.model}  [dim](unused in Phase 1)[/]",
        title="mutagen run",
    ))

    with console.status("[cyan]generating tier-1 tests…"):
        result = one_shot(target=target, workdir=wd, cfg=cfg, llm=llm)

    console.print(f"[bold]generated:[/] {result.generated_tests}")

    if not result.pytest_ok:
        console.print("[red]pytest failed on generated tests — stopping.[/]")
        console.print(Panel(result.pytest_result.stdout or "", title="pytest stdout"))
        console.print(Panel(result.pytest_result.stderr or "", title="pytest stderr"))
        raise typer.Exit(code=2)

    report = result.report
    assert report is not None
    console.print(Panel.fit(report.format_summary(), title="mutation report"))

    if report.survivors:
        t = Table(title="survivors", show_lines=False)
        t.add_column("id")
        t.add_column("file")
        t.add_column("diff", overflow="fold")
        for m in report.survivors:
            t.add_row(m.id, m.file or "-", (m.diff or "").strip()[:400])
        console.print(t)


@app.command("version")
def version_cmd() -> None:
    """Print mutagen version."""
    from mutagen import __version__
    console.print(__version__)


if __name__ == "__main__":
    app()
