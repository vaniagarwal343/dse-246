"""CourtListener: case-law API v4 opinion clusters, which carry `date_filed`."""
from pipeline import env

NAME = "CourtListener"
URL = "https://www.courtlistener.com/api/rest/v4/clusters/"


def pull(client, start, end, limit, purpose):
    params = {"date_filed__gte": start.isoformat(), "date_filed__lte": end.isoformat(), "order_by": "date_filed"}
    headers = {"Authorization": f"Token {env.get('COURTLISTENER_TOKEN')}"}
    resp = client.request("GET", URL, purpose=purpose, params=params, headers=headers)
    resp.raise_for_status()
    return [{"record_id": str(c["id"]), "record_url": "https://www.courtlistener.com" + c["absolute_url"], "date": c.get("date_filed"), "raw": c}
            for c in resp.json().get("results", [])[:limit]]
