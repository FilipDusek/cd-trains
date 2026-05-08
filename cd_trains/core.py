"""HTTP + parser layer for cd.cz connection search.

Two-step flow:
  1. Resolve free-text station names against ip4azak-prod.cdis.cz/DataWS/ACEConnection
     (a JSONP autocomplete endpoint that gives us station ID + display name)
  2. POST `data={…json…}` to cd.cz/[lang/]spojeni-a-jizdenka/api-hp/, then pull
     the `var model = {…}` blob out of the returned HTML
"""
from __future__ import annotations

import datetime as _dt
import http.cookiejar
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


BASE = "https://www.cd.cz"
AUTOCOMPLETE_URL = "https://ip4azak-prod.cdis.cz/DataWS/ACEConnection"
SEARCH_PATH = "/spojeni-a-jizdenka/api-hp/"
PRICES_PATH = "/spojeni-a-jizdenka/GetConnListPrice/"
LANG_PREFIX = {"cs": "", "en": "/en", "de": "/de"}


# ─────────────────── dataclasses ───────────────────


@dataclass(frozen=True)
class StationChoice:
    """Resolved station: name + numeric list ID, plus the original query for context."""
    query: str
    name: str
    list_id: int

    @property
    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "listId": self.list_id}


@dataclass(frozen=True)
class TrainLeg:
    """One physical train segment within a connection."""
    name: str
    number: str
    train_type_and_num: str
    from_station: str
    to_station: str
    dep_date: str
    dep_time: str
    arr_date: str
    arr_time: str
    delay_min: int
    detail_path: str
    distance_km: str
    duration: str


@dataclass(frozen=True)
class ConnectionRow:
    """One end-to-end connection (potentially multi-leg)."""
    from_station: str
    to_station: str
    departure: str        # "DD.MM.YYYY HH:MM"
    arrival: str
    duration: str         # "Hh MMmin" (free-form string from server)
    changes: int
    distance_km: str
    train_types: str      # "EC, R, …"
    trains: list[TrainLeg]
    details_url: str
    price_label: str      # "n/a" | "sold out" | "X CZK"
    raw: dict[str, Any]


# ─────────────────── helpers ───────────────────


# Date input parsing — accept both ISO and CZ formats. Hand-rolled to avoid
# dateparser/dateutil's cold-start cost and ISO-vs-DMY ambiguity.
def _parse_date(s: str) -> _dt.date:
    """Accepts YYYY-MM-DD (ISO) or D[D].M[M].YYYY (CZ). Returns a date."""
    s = s.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _dt.date(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        return _dt.date(y, mo, d)
    raise ValueError(f"date must be YYYY-MM-DD or D.M.YYYY (got {s!r})")


def _to_display_date(date: str) -> str:
    """YYYY-MM-DD or D.M.YYYY → DD.MM.YYYY (cd.cz's expected wire format)."""
    return _parse_date(date).strftime("%d.%m.%Y")


def _parse_time(time_str: Optional[str]) -> str:
    """HH:MM (1- or 2-digit hour OK), or None for now. Returns zero-padded."""
    if time_str is None:
        return _dt.datetime.now().strftime("%H:%M")
    s = time_str.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError(f"time must be HH:MM (got {time_str!r})")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"time out of 24h range (got {time_str!r})")
    return f"{hh:02d}:{mm:02d}"


def _cookie_opener():
    jar: http.cookiejar.CookieJar = http.cookiejar.CookieJar()
    return build_opener(HTTPCookieProcessor(jar), HTTPRedirectHandler())


def _fetch(opener, url: str, *, method: str = "GET", data: bytes | None = None,
           headers: Optional[dict[str, str]] = None, timeout: float = 60.0) -> str:
    req = Request(url, data=data, method=method, headers=headers or {})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _parse_jsonp(text: str) -> Any:
    text = text.strip()
    m = re.match(r"^[^(]+\((.*)\);?$", text, re.S)
    if not m:
        raise ValueError("not a JSONP response")
    return json.loads(m.group(1))


def _extract_model(html: str) -> dict[str, Any]:
    """Pull the JSON object after `var model = ` in the search result HTML.

    Walks balanced braces, ignoring string contents — handles strings with
    escaped quotes inside JSON values cleanly.
    """
    marker = "var model = "
    start = html.find(marker)
    if start == -1:
        raise ValueError("Could not find result model in CD.cz response")
    sub = html[start + len(marker):]
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(sub):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(sub[: i + 1])
    raise ValueError("Could not parse result model JSON (unbalanced braces)")


# ─────────────────── high-level API ───────────────────


def resolve_station(opener, query: str, *, lang: str = "en", limit: int = 12) -> StationChoice:
    """Resolve a free-text station/town name to a StationChoice.

    Accepts a "Name%ID" shortcut (skip the lookup) or any string the
    autocomplete recognizes.
    """
    raw = query.strip()
    if re.fullmatch(r".+%\d+", raw):
        name, list_id = raw.rsplit("%", 1)
        return StationChoice(query=query, name=name, list_id=int(list_id))

    params = {
        "count": str(limit), "prefixText": raw, "lang": lang,
        "combination": "", "searchDate": "", "isNoNad": "false",
        "format": "json", "callback": "cb",
    }
    text = _fetch(opener, f"{AUTOCOMPLETE_URL}?{urlencode(params)}")
    items = _parse_jsonp(text)
    if not items:
        raise LookupError(f"no station suggestions for {query!r}")

    norm = raw.casefold()
    exact = [
        x for x in items
        if str(x.get("selectedText", "")).casefold() == norm
        or str(x.get("text", "")).casefold() == norm
    ]
    # iconId == 3 marks "station" entries (vs city/region clusters)
    chosen = (
        next((x for x in exact if x.get("iconId") == 3), exact[0]) if exact
        else next((x for x in items if x.get("iconId") == 3), items[0])
    )
    list_id = int(chosen["value"])
    name = str(chosen.get("selectedText") or chosen.get("text") or raw)
    return StationChoice(query=query, name=name, list_id=list_id)


def _compute_duration(dep: str, arr: str) -> str:
    """Compute Hh MMmin from "DD.MM.YYYY HH:MM" timestamps.

    The server's `timeLength` field is sometimes garbage (e.g. "0:2 hh"); the
    UI reformats it client-side. We recompute from the actual departure /
    arrival times for stability.
    """
    try:
        dep_dt = _dt.datetime.strptime(dep, "%d.%m.%Y %H:%M")
        arr_dt = _dt.datetime.strptime(arr, "%d.%m.%Y %H:%M")
        mins = max(0, int((arr_dt - dep_dt).total_seconds() // 60))
        h, m = divmod(mins, 60)
        return f"{h}h{m:02d}"
    except (ValueError, TypeError):
        return ""


def _format_price(price: dict[str, Any]) -> str:
    """Format the connection price block. The cd.cz price field is in hellers
    (1/100 CZK) but the values returned for canProceed=true look like whole CZK,
    so we render as-is."""
    if price.get("soldOut"):
        return "sold out"
    if price.get("noPrice") or not price.get("canProceed"):
        return "n/a"
    p = price.get("price")
    if p is None or p == 0:
        return "n/a"
    # GetConnListPrice returns whole CZK already (e.g. 359). Older endpoints
    # used hellers (35900 == 359 CZK). Heuristic: anything > 9999 looks like
    # hellers and gets divided.
    czk = p / 100 if p > 9999 else p
    if czk == int(czk):
        return f"{int(czk)} CZK"
    return f"{czk:.2f} CZK"


def _extract_csrf(html: str) -> Optional[str]:
    """Pull the __RequestVerificationToken from the search result HTML."""
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def _fetch_prices(
    opener,
    html: str,
    model: dict[str, Any],
    lang: str,
) -> dict[int, dict[str, Any]]:
    """Make the follow-up GetConnListPrice POST that the cd.cz UI fires after
    a search to fill in real prices. Returns {connID: price_dict}.

    Failures are non-fatal — connections without prices fall through to the
    "n/a" rendering. We don't raise.
    """
    items = model.get("list") or []
    guid = model.get("guid")
    csrf = _extract_csrf(html)
    if not (items and guid and csrf):
        return {}

    url = urljoin(BASE, f"{LANG_PREFIX[lang]}{PRICES_PATH}")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE,
        "Referer": f"{BASE}{LANG_PREFIX[lang]}{SEARCH_PATH}",
        "X-Requested-With": "XMLHttpRequest",
    }
    # The endpoint only respects loadPrices[0] per request — cd.cz fires N sequential
    # POSTs, one per connection. We do the same.
    out: dict[int, dict[str, Any]] = {}
    for it in items:
        conn_id = it.get("id")
        handle = it.get("handle")
        aux = it.get("auxDesc") or ""
        if conn_id is None or handle is None:
            continue
        fields = [
            ("__RequestVerificationToken", csrf),
            ("model[guid]", str(guid)),
            ("model[SearchType]", "0"),
            ("model[pageType]", "0"),
            ("model[priceType]", "0"),
            ("model[trainIndex]", "0"),
            ("model[noCachePrices]", "true"),
            ("model[loadPrices][0][connID]", str(conn_id)),
            ("model[loadPrices][0][handle]", str(handle)),
            ("model[loadPrices][0][auxDesc]", str(aux)),
        ]
        try:
            resp_text = _fetch(opener, url, method="POST", data=urlencode(fields).encode(), headers=headers)
            resp = json.loads(resp_text)
        except Exception:
            continue
        for entry in resp.get("list") or []:
            eid = entry.get("id")
            if eid is not None and isinstance(entry.get("price"), dict):
                out[eid] = entry["price"]
    return out


def _parse_connection(
    item: dict[str, Any],
    train_detail_prefix: str,
    *,
    enriched_prices: Optional[dict[int, dict[str, Any]]] = None,
) -> ConnectionRow:
    trains = [
        TrainLeg(
            name=str(t.get("trainName", "")),
            number=str(t.get("trainNum", "")),
            train_type_and_num=str(t.get("trainTypeAndNum", "")),
            from_station=str(t.get("from", "")),
            to_station=str(t.get("to", "")),
            dep_date=str(t.get("depDate", "")),
            dep_time=str(t.get("depTime", "")),
            arr_date=str(t.get("arrDate", "")),
            arr_time=str(t.get("arrTime", "")),
            delay_min=int(t.get("delay", 0) or 0),
            detail_path=str(t.get("trainDetail", "")),
            distance_km=str(t.get("distance", "")),
            duration=str(t.get("timeLength", "")),
        )
        for t in item.get("trains", [])
    ]
    first = trains[0] if trains else None
    last = trains[-1] if trains else None
    details_url = (
        urljoin(BASE, f"{train_detail_prefix}{first.detail_path}") if first and first.detail_path
        else ""
    )
    departure = f"{first.dep_date} {first.dep_time}" if first else ""
    arrival = f"{last.arr_date} {last.arr_time}" if last else ""
    # Prefer enriched price (from GetConnListPrice) over the placeholder
    # `noPrice: true` block that comes back with the initial search.
    conn_id = item.get("id")
    price_block = (enriched_prices or {}).get(conn_id) or item.get("price") or {}
    return ConnectionRow(
        from_station=first.from_station if first else str(item.get("from", "")),
        to_station=last.to_station if last else str(item.get("to", "")),
        departure=departure,
        arrival=arrival,
        duration=_compute_duration(departure, arrival) or str(item.get("timeLength", "")),
        changes=int(item.get("changesCount") or 0),
        distance_km=str(item.get("distance", "")),
        train_types=str(item.get("trainTypes", "")),
        trains=trains,
        details_url=details_url,
        price_label=_format_price(price_block),
        raw=item,
    )


def search_connections(
    from_station: str,
    to_station: str,
    date: str,
    *,
    time: Optional[str] = None,
    seat_class: str = "2",
    adults: int = 1,
    lang: str = "en",
    limit: int = 10,
    fetch_prices: bool = True,
    max_pages: int = 8,
    rate_limit: bool = True,
    rate_limiter=None,
) -> list[ConnectionRow]:
    """End-to-end search: resolve stations, POST search, parse results, fetch
    prices, paginate forward until `limit` connections are collected.

    Args:
        from_station, to_station: Free-text station/town names. The "Name%ID"
            shortcut skips the autocomplete lookup.
        date: YYYY-MM-DD or DD.MM.YYYY.
        time: HH:MM (defaults to now).
        seat_class: "1" | "2" | "business".
        adults: 1-9.
        lang: "en" | "cs" | "de".
        limit: Max connections to return.
        fetch_prices: Whether to fire the follow-up GetConnListPrice POSTs.
        max_pages: Hard cap on pagination requests (default 8 → up to ~40 rows).
    """
    if seat_class not in {"1", "2", "business"}:
        raise ValueError("seat_class must be '1', '2', or 'business'")
    if lang not in LANG_PREFIX:
        raise ValueError(f"lang must be one of: {', '.join(LANG_PREFIX)}")

    display_date = _to_display_date(date)
    display_time = _parse_time(time)
    opener = _cookie_opener()

    if rate_limit:
        from .ratelimit import BUCKET_NAME, shared as _shared
        limiter = rate_limiter or _shared()
        if limiter is not None:
            limiter.try_acquire(BUCKET_NAME)  # blocks until a slot is free

    origin = resolve_station(opener, from_station, lang=lang)
    dest = resolve_station(opener, to_station, lang=lang)

    klass = 2 if seat_class == "2" else 1 if seat_class == "1" else 3
    post_url = urljoin(BASE, f"{LANG_PREFIX[lang]}{SEARCH_PATH}")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE,
        "Referer": f"{BASE}{LANG_PREFIX[lang]}/",
    }

    def _one_page(page_date: str, page_time: str):
        payload = {
            "from": origin.payload, "to": dest.payload,
            "date": page_date, "time": page_time,
            "isAdvanced": False, "doSearch": True, "Class": klass,
            "passengers": [{"typeId": 5, "count": adults, "cardIds": []}],
        }
        body = urlencode({"data": json.dumps(payload, separators=(",", ":"))}).encode()
        html = _fetch(opener, post_url, method="POST", data=body, headers=headers)
        return html, _extract_model(html)

    rows: list[ConnectionRow] = []
    # Dedupe on (departure timestamp, first train number) — connID changes
    # between page fetches but the underlying train doesn't.
    seen_keys: set[tuple[str, str]] = set()
    page_date, page_time = display_date, display_time

    for _ in range(max_pages):
        html, model = _one_page(page_date, page_time)
        train_detail_prefix = model.get("trainDetailUrl") or ""
        items = model.get("list") or []
        enriched = _fetch_prices(opener, html, model, lang) if fetch_prices else {}

        added_in_this_page = 0
        for item in items:
            row = _parse_connection(item, train_detail_prefix, enriched_prices=enriched)
            first_num = row.trains[0].train_type_and_num if row.trains else ""
            key = (row.departure, first_num)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(row)
            added_in_this_page += 1
            if len(rows) >= limit:
                return rows

        # Stop if no more pages OR if the page added nothing new (e.g. server keeps
        # returning the same window — guard against loops).
        if not model.get("allowNext") or added_in_this_page == 0:
            break

        # Advance to "last departure + 1 min" for the next page. The server returns
        # connections departing AT or AFTER this time, so +1 minute skips dupes.
        last = items[-1]
        last_trains = last.get("trains") or []
        if not last_trains:
            break
        first_train = last_trains[0]
        last_date = first_train.get("depDate") or page_date
        last_time = first_train.get("depTime")
        if not last_time:
            break
        try:
            t = _dt.datetime.strptime(last_time, "%H:%M") + _dt.timedelta(minutes=1)
            page_time = t.strftime("%H:%M")
            page_date = last_date  # last_date is already DD.MM.YYYY
        except ValueError:
            break

    return rows
