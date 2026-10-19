"""TheSportsDB: v1 events-by-day API; date field `dateEvent`."""
import datetime as dt
import time

from pipeline import env

NAME = "TheSportsDB"
URL = "https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php"
# NOTE(vani): the free test key allows about 30 requests a minute and returns few events per day, so pull walks day by day, slowly.
PAUSE = 2.0


def pull(client, start, end, limit, purpose):
    out, day = [], start
    while len(out) < limit and day <= end:
        resp = client.request("GET", URL.format(key=env.get("THESPORTSDB_API_KEY")), purpose=purpose, params={"d": day.isoformat()})
        resp.raise_for_status()
        out += [{"record_id": e["idEvent"], "record_url": f"https://www.thesportsdb.com/event/{e['idEvent']}", "date": e.get("dateEvent"), "raw": e}
                for e in resp.json().get("events") or []]
        day += dt.timedelta(days=1)
        time.sleep(PAUSE)
    return out[:limit]
