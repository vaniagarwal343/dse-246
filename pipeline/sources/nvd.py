"""CVE/NVD: NVD CVE API 2.0; date field `published`."""
from pipeline import env

NAME = "CVE/NVD"
URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def pull(client, start, end, limit, purpose):
    # NOTE(vani): NVD_API_KEY is optional; without it NVD allows 5 requests per 30 seconds.
    headers = {"apiKey": env.get("NVD_API_KEY")} if env.get("NVD_API_KEY") else {}
    params = {"pubStartDate": f"{start}T00:00:00.000", "pubEndDate": f"{end}T23:59:59.999", "resultsPerPage": limit}
    resp = client.request("GET", URL, purpose=purpose, params=params, headers=headers)
    resp.raise_for_status()
    return [{"record_id": v["cve"]["id"], "record_url": f"https://nvd.nist.gov/vuln/detail/{v['cve']['id']}",
             "date": v["cve"]["published"][:10], "raw": v["cve"]} for v in resp.json().get("vulnerabilities", [])]
