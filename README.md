# cd-trains

Search Czech Railways (ČD / České dráhy) train connections on
[cd.cz](https://www.cd.cz) from the command line. Hits the public
autocomplete + connection-search endpoints, parses the embedded
`var model = {…}` JSON the page ships, and follows up with the
`GetConnListPrice` POST per row to fill in fares — the same two-step
the cd.cz website does.

```
$ cd-trains 'Brno hl.n.' 'Ostrava hl.n.' 2026-05-08 --time 08:00 --limit 4
```

```
Brno hl.n. → Ostrava hl.n.  ·  08.05.2026 08:00  ·  lang=en

  price        dep     arr     duration   transfers   legs
 ──────────────────────────────────────────────────────────────────────
  269 Kč       08:31   10:37   2h06               0   Ex3 Ostravan
  269 Kč       09:31   11:37   2h06               0   Ex3 Polonia
  269 Kč       10:31   12:37   2h06               0   Ex3 Vindobona
  269 Kč       11:31   13:37   2h06               0   Ex3 Metropolitan

· 4 results · click legs for share URL · --json for machine-readable · Search ↗
```

## Install

Not on PyPI — install from GitHub:

```
uv tool install 'cd-trains @ git+https://github.com/FilipDusek/cd-trains.git@main'
# or one-shot, no install:
uvx --from 'git+https://github.com/FilipDusek/cd-trains.git' cd-trains 'Brno hl.n.' 'Ostrava hl.n.' 2026-05-08
```

## CLI

```
cd-trains FROM TO [DATE] [options]

Positional:
  FROM         Origin station/town (e.g. "Brno hl.n.")
  TO           Destination station/town (e.g. "Ostrava hl.n.")
  DATE         YYYY-MM-DD or D.M.YYYY (defaults to today)

Options:
  -t, --time HH:MM       Departure time (default: now)
  --class 1|2|business   Seat class (default: 2)
  -a, --adults 1         Adults without discount card (default: 1)
  --lang en|cs|de        Site language (default: en)
  -n, --limit 10         Max results
  --json                 JSON output
  --no-rate-limit        Disable the local SQLite rate limiter
```

## Programmatic use

```python
from cd_trains import search_connections

rows = search_connections(
    "Brno hl.n.", "Ostrava hl.n.", "2026-05-08",
    time="09:00", limit=5,
)
for r in rows:
    print(f"{r.departure} → {r.arrival}  ({r.duration}, {r.changes} xfer)  {r.price_label}")
    for t in r.trains:
        print(f"  · {t.name}  {t.from_station} → {t.to_station}")
```

## Output shape

The CLI's `--json` output carries these fields per result:

- `from`, `to` — resolved station names
- `departure`, `arrival` — `"DD.MM.YYYY HH:MM"`
- `duration` — `"Hh MMmin"` (computed locally; the server's `timeLength`
  field is sometimes garbage like `"0:2 hh"`)
- `transfers` — number of train changes
- `price` — e.g. `"269 Kč"`, or `"n/a"` for international routes whose
  fare data lives behind a partner ticket API we don't credential against
- `share_url` — canonical cd.cz URL for the train detail page
- `legs[]` — per-train `name`, `number`, `carrier`, `from`, `to`,
  `dep_time`, `arr_time`, `delay_min`, `duration`, `distance_km`
- `distance_km`, `train_types` — overall trip stats

## Rate limiting

Cross-invocation rate limit defaults to 5 req / 5s, 30 / min, 300 / hr.
State persists across CLI invocations and parallel processes via SQLite +
file-lock at `~/Library/Caches/cd-trains/ratelimit.sqlite` (macOS) or
`$XDG_CACHE_HOME/cd-trains/ratelimit.sqlite` (Linux).

The exact threshold of cd.cz is not published — these limits are arbitrary.
Disable with `--no-rate-limit`, `CD_TRAINS_NO_RATE_LIMIT=1`, or
`rate_limit=False`.

## Caveats

- **Prices for some international routes often come back as `n/a`** —
  that fare data lives behind a partner ticket API not exposed via the
  public site. Open the `share_url` to see fares on cd.cz.
- **Scrapes cd.cz HTML + a JSON-embedded model.** It will break when they
  redesign. Pin a commit if you depend on it.

## License

MIT
