import typer
from codeguard.impact.analyzer import analyze_impact
from codeguard.impact.report import render_report
from codeguard.storage.db import Storage
from pathlib import Path


app = typer.Typer()

@app.command()
def hello():
    """Sanity check that the CLI works."""
    print("codeguard is alive")
    
@app.command()
def impact(ref: str = typer.Argument(..., help="Git ref to compare against, e.g. HEAD~1 or main")):
    """Show the blast radius of signature changes since `ref`."""
    project_root = Path.cwd()
    storage = Storage(project_root / ".codeguard")
    report = analyze_impact(project_root, storage, ref)
    typer.echo(render_report(report))

if __name__ == "__main__":
    app()