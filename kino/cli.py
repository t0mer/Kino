"""Command line entry point (commands are added in later tasks)."""

import typer

from kino import __version__

app = typer.Typer(help="Kino", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)
