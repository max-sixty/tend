"""CLI for generating continuous workflow files."""

from __future__ import annotations

from pathlib import Path

import click

from continuous.config import Config
from continuous.workflows import generate_all


@click.group()
def main() -> None:
    """Generate Claude-powered CI workflows from .config/continuous.toml."""


@main.command()
@click.option("--config", "-c", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Print generated files without writing")
def init(config_path: Path | None, dry_run: bool) -> None:
    """Generate workflow files from config. Idempotent — always overwrites."""
    cfg = Config.load(config_path)
    outdir = Path(".github/workflows")

    workflows = generate_all(cfg)
    if not workflows:
        click.echo("No workflows enabled in config.")
        return

    if not dry_run:
        outdir.mkdir(parents=True, exist_ok=True)

    for wf in workflows:
        path = outdir / wf.filename
        if dry_run:
            click.echo(f"--- {wf.filename} ---")
            click.echo(wf.content)
            continue

        path.write_text(wf.content)
        click.echo(f"  wrote {path}")

    if not dry_run:
        click.echo(f"\nGenerated {len(workflows)} workflow files.")
