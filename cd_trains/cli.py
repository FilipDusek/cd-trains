"""`cd-trains` CLI: a typer-based wrapper around `core.search_connections`."""
from __future__ import annotations

import json as _json
import sys
from typing import Optional
from urllib.parse import urlencode

try:
    import typer
except ImportError:
    sys.stderr.write(
        "cd-trains CLI requires typer. Install with:\n"
        "  uv tool install 'cd-trains @ git+https://github.com/FilipDusek/cd-trains.git@main'\n"
    )
    raise SystemExit(1)

from .core import BASE, LANG_PREFIX, ConnectionRow, search_connections


def _render(row: ConnectionRow) -> dict:
    """Common dict shape mirrored across idos / cd-trains / flights CLIs."""
    return {
        "from": row.from_station,
        "to": row.to_station,
        "departure": row.departure,
        "arrival": row.arrival,
        "duration": row.duration,
        "transfers": row.changes,
        "price": row.price_label,
        "share_url": row.details_url,
        "legs": [
            {
                "name": leg.name,
                "number": leg.number,
                "carrier": "ČD",  # cd.cz only surfaces ČD-operated trains
                "from": leg.from_station,
                "to": leg.to_station,
                "dep_time": leg.dep_time,
                "arr_time": leg.arr_time,
                "dep_date": leg.dep_date,
                "arr_date": leg.arr_date,
                "delay_min": leg.delay_min,
                "duration": leg.duration,
                "distance_km": leg.distance_km,
            }
            for leg in row.trains
        ],
        # cd.cz-specific extras (kept for compat / power-users):
        "distance_km": row.distance_km,
        "train_types": row.train_types,
    }


def _build_search_url(from_station: str, to_station: str,
                      display_date: str, display_time: str, lang: str) -> str:
    """Canonical user-facing cd.cz search URL for the same query.

    cd.cz's HTML-page search form lives at /spojeni-a-jizdenka/ and accepts
    free-text from/to + DD.MM.YYYY date + HH:MM time as query params.
    """
    params = {
        "from": from_station,
        "to": to_station,
        "date": display_date,
        "time": display_time,
    }
    return f"{BASE}{LANG_PREFIX[lang]}/spojeni-a-jizdenka/?{urlencode(params)}"


def _print_table(rows: list[dict], *, from_label: str, to_label: str,
                 date: str, time: str, lang: str, search_url: str) -> None:
    try:
        from rich import box
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        # Plain fallback when rich isn't installed
        print(f"\n{from_label} → {to_label}  ·  {date} {time}  ·  lang={lang}\n")
        for i, r in enumerate(rows, 1):
            print(f"[{i}] {r['departure']} → {r['arrival']}  {r['duration']}  "
                  f"{r['transfers']} transfers  ·  {r['price']}")
            print(f"    legs: {', '.join(l['name'] for l in r['legs'])}")
            if r["share_url"]:
                print(f"    details: {r['share_url']}")
            print()
        print(f"Search: {search_url}\n")
        return

    console = Console()
    console.print(
        f"\n[bold]{from_label} → {to_label}[/bold]  [dim]·[/dim]  "
        f"{date} {time}  [dim]·[/dim]  [dim]lang={lang}[/dim]\n"
    )
    if not rows:
        console.print("[dim]no connections returned[/dim]\n")
        console.print(f"[dim][link={search_url}]Search ↗[/link][/dim]\n")
        return

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1), expand=False)
    table.add_column("price", justify="right", style="bold green", no_wrap=True)
    table.add_column("dep", no_wrap=True)
    table.add_column("arr", no_wrap=True)
    table.add_column("duration", no_wrap=True)
    table.add_column("transfers", justify="right", no_wrap=True)
    table.add_column("legs", overflow="ellipsis")

    for r in rows:
        # extract just the time portion of "DD.MM.YYYY HH:MM"
        dep_time = r["departure"].split(" ", 1)[-1] if r["departure"] else ""
        arr_time = r["arrival"].split(" ", 1)[-1] if r["arrival"] else ""
        leg_names = " → ".join(l["name"] for l in r["legs"])
        if r["share_url"]:
            leg_names = f"[link={r['share_url']}]{leg_names}[/link]"
        table.add_row(
            r["price"], dep_time, arr_time, r["duration"],
            str(r["transfers"]), leg_names,
        )

    console.print(table)
    console.print(
        f"[dim]· {len(rows)} result{'s' if len(rows) != 1 else ''} · "
        f"click legs for share URL · --json for machine-readable · "
        f"[link={search_url}]Search ↗[/link][/dim]\n"
    )


def main(
    from_station: str = typer.Argument(..., metavar="FROM",
        help="Origin station/town (e.g. 'Brno hl.n.')"),
    to_station: str = typer.Argument(..., metavar="TO",
        help="Destination station/town (e.g. 'Ostrava hl.n.')"),
    date: Optional[str] = typer.Argument(None, metavar="[DATE]",
        help="Travel date YYYY-MM-DD or D.M.YYYY (defaults to today)"),
    time: Optional[str] = typer.Option(None, "--time", "-t",
        help="Departure time HH:MM (defaults to now)"),
    seat_class: str = typer.Option("2", "--class",
        help="1, 2, or business"),
    adults: int = typer.Option(1, "--adults", "-a", min=1, max=9,
        help="Adult passengers (no discount card)"),
    lang: str = typer.Option("en", "--lang",
        help="Site language for the request: en | cs | de"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50,
        help="Max connections to show"),
    json_output: bool = typer.Option(False, "--json",
        help="Emit JSON instead of a table"),
    no_rate_limit: bool = typer.Option(False, "--no-rate-limit",
        help="Disable the SQLite-backed rate limiter"),
) -> None:
    """Search Czech Railways (ČD / cd.cz) train connections."""
    from .core import _parse_date, _to_display_date, _parse_time
    import datetime as _dt
    if date is None:
        display_date = _dt.date.today().strftime("%d.%m.%Y")
    else:
        try:
            display_date = _to_display_date(date)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2)
    display_time = _parse_time(time)

    try:
        rows = search_connections(
            from_station, to_station, display_date,
            time=display_time, seat_class=seat_class, adults=adults,
            lang=lang, limit=limit,
            rate_limit=not no_rate_limit,
        )
    except (ValueError, LookupError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2)
    except Exception as e:  # network or parse failure
        typer.echo(f"search failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1)

    rendered = [_render(r) for r in rows]
    search_url = _build_search_url(
        from_station, to_station, display_date, display_time, lang,
    )

    if json_output:
        typer.echo(_json.dumps(
            {
                "query": {
                    "from": from_station, "to": to_station,
                    "date": display_date, "time": display_time,
                    "lang": lang,
                    "class": seat_class, "adults": adults,
                    "url": search_url,
                },
                "results": rendered,
            },
            indent=2, ensure_ascii=False, default=str,
        ))
    else:
        from_label = rows[0].from_station if rows else from_station
        to_label = rows[0].to_station if rows else to_station
        _print_table(
            rendered, from_label=from_label, to_label=to_label,
            date=display_date, time=display_time, lang=lang,
            search_url=search_url,
        )


def _entrypoint() -> None:
    typer.run(main)


if __name__ == "__main__":
    _entrypoint()
