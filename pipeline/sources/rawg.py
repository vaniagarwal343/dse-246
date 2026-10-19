"""RAWG: games API; date field `released`."""
from pipeline import env

NAME = "RAWG"
URL = "https://api.rawg.io/api/games"


def pull(client, start, end, limit, purpose):
    params = {"key": env.get("RAWG_API_KEY"), "dates": f"{start},{end}", "ordering": "released", "page_size": limit}
    resp = client.request("GET", URL, purpose=purpose, params=params)
    resp.raise_for_status()
    return [{"record_id": str(g["id"]), "record_url": f"https://rawg.io/games/{g['slug']}", "date": g.get("released"), "raw": g}
            for g in resp.json().get("results", [])]
