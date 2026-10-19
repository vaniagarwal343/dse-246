"""Federal Register: documents API; date field `publication_date`."""
NAME = "Federal Register"
URL = "https://www.federalregister.gov/api/v1/documents.json"


def pull(client, start, end, limit, purpose):
    params = {"per_page": limit, "order": "oldest", "conditions[publication_date][gte]": start.isoformat(),
              "conditions[publication_date][lte]": end.isoformat()}
    resp = client.request("GET", URL, purpose=purpose, params=params)
    resp.raise_for_status()
    return [{"record_id": d["document_number"], "record_url": d["html_url"], "date": d["publication_date"], "raw": d}
            for d in resp.json().get("results", [])]
