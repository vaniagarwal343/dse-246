"""GDELT: DOC 2.0 API article list; date field `seendate`."""
NAME = "GDELT"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# NOTE(vani): the DOC API needs a query term and has no match-everything query. Stage 1's query plan is set at C2; the smoke test uses a broad local-news one.
SMOKE_QUERY = '"city council" sourcelang:english'


def pull(client, start, end, limit, purpose, query=SMOKE_QUERY):
    params = {"query": query, "mode": "artlist", "format": "json", "sort": "dateasc", "maxrecords": limit,
              "startdatetime": f"{start:%Y%m%d}000000", "enddatetime": f"{end:%Y%m%d}235959"}
    resp = client.request("GET", URL, purpose=purpose, params=params)
    resp.raise_for_status()
    try:
        articles = resp.json().get("articles", [])
    except ValueError:
        raise ValueError(f"GDELT answered with text, not JSON: {resp.text[:200]!r}") from None
    return [{"record_id": a["url"], "record_url": a["url"], "date": f"{a['seendate'][:4]}-{a['seendate'][4:6]}-{a['seendate'][6:8]}", "raw": a}
            for a in articles]
