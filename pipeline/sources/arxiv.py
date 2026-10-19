"""arXiv: export API (Atom feed); date field `published`."""
import xml.etree.ElementTree as ET

NAME = "arXiv"
URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"


def text(entry, tag):
    return " ".join((entry.findtext(f"{ATOM}{tag}") or "").split())


def pull(client, start, end, limit, purpose):
    params = {"search_query": f"submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]", "start": 0, "max_results": limit,
              "sortBy": "submittedDate", "sortOrder": "ascending"}
    resp = client.request("GET", URL, purpose=purpose, params=params)
    resp.raise_for_status()
    out = []
    for entry in ET.fromstring(resp.content).iter(f"{ATOM}entry"):
        url = text(entry, "id")
        out.append({"record_id": url.rsplit("/abs/", 1)[-1], "record_url": url, "date": text(entry, "published")[:10],
                    "raw": {"title": text(entry, "title"), "summary": text(entry, "summary"),
                            "authors": [text(a, "name") for a in entry.iter(f"{ATOM}author")],
                            "categories": [c.get("term") for c in entry.iter(f"{ATOM}category")]}})
    return out
