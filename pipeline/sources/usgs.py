"""USGS earthquakes: FDSN event API (GeoJSON); date field `time`."""
import datetime as dt

NAME = "USGS earthquakes"
URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def pull(client, start, end, limit, purpose):
    params = {"format": "geojson", "starttime": start.isoformat(), "endtime": (end + dt.timedelta(days=1)).isoformat(),
              "orderby": "time-asc", "limit": limit}
    resp = client.request("GET", URL, purpose=purpose, params=params)
    resp.raise_for_status()
    return [{"record_id": f["id"], "record_url": f["properties"].get("url"),
             "date": dt.datetime.fromtimestamp(f["properties"]["time"] / 1000, dt.UTC).date().isoformat(), "raw": f}
            for f in resp.json().get("features", [])]
