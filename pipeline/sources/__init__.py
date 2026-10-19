"""One puller per PROTOCOL.md §2 seed source, in table order.

Each module has NAME and pull(client, start, end, limit, purpose), which returns up to `limit` records as
{record_id, record_url, date, raw}, with `date` the source's §2 date field as YYYY-MM-DD.
"""
from pipeline.sources import arxiv, courtlistener, federal_register, gdelt, nvd, rawg, thesportsdb, usgs

SOURCES = (nvd, usgs, gdelt, rawg, thesportsdb, federal_register, courtlistener, arxiv)
