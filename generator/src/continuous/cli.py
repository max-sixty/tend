"""CLI for generating continuous workflow files."""

from __future__ import annotations

from pathlib import Path

import click

from continuous.config import Config
from continuous.workflows import (
    extract_setup_section,
    generate_all,
    inject_setup_section,
)


@click.group()
def main() -> None:
    """Generate Claude-powered CI workflows from .config/continuous.toml."""


@main.command()
@click.option("--config", "-c", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--force", is_flag=True, help="Overwrite existing workflow files")
@click.option("--dry-run", is_flag=True, help="Print generated files without writing")
def init(config_path: Path | None, force: bool, dry_run: bool) -> None:
    """Generate workflow files from config."""
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

        if path.exists() and not force:
            raise click.ClickException(
                f"{path} already exists. Use --force to overwrite, "
                "or `continuous update` to preserve project setup sections."
            )
        path.write_text(wf.content)
        click.echo(f"  wrote {path}")

    if not dry_run:
        click.echo(f"\nGenerated {len(workflows)} workflow files.")
        click.echo("Add your project setup steps between the marker comments:")
        click.echo("  # --- project setup (edit this section) ---")
        click.echo("  # --- end project setup ---")


@main.command()
@click.option("--config", "-c", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Print what would change without writing")
def update(config_path: Path | None, dry_run: bool) -> None:
    """Regenerate workflows, preserving project setup sections."""
    cfg = Config.load(config_path)
    outdir = Path(".github/workflows")

    workflows = generate_all(cfg)
    for wf in workflows:
        path = outdir / wf.filename
        setup: str | None = None
        if path.exists():
            setup = extract_setup_section(path.read_text())

        new_content = wf.content
        if setup is not None:
            new_content = inject_setup_section(new_content, setup)

        if dry_run:
            if path.exists():
                old = path.read_text()
                if old == new_content:
                    click.echo(f"  {wf.filename}: no changes")
                else:
                    click.echo(f"  {wf.filename}: would update")
            else:
                click.echo(f"  {wf.filename}: would create")
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content)
        click.echo(f"  updated {path}")

    if not dry_run:
        click.echo(f"\nUpdated {len(workflows)} workflow files.")
