import typer

app = typer.Typer()

@app.command()
def hello():
    """Sanity check that the CLI works."""
    print("codeguard is alive")

if __name__ == "__main__":
    app()