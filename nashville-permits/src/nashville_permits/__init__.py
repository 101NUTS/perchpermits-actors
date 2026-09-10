"""Nashville / Davidson County building-permit intelligence.

Public-records data from two sources:

* Metro Nashville Open Data ArcGIS feature services (permits issued and
  permit applications), updated daily.
* Metro Nashville ePermits case API (licensed contractor, property owner,
  sub-trade permit conditions, inspection tasks), real time.

The package is pure standard library so it runs identically inside the Apify
actor, from the CLI, and in tests.
"""

from .service import rank_contractors, search_permits

__all__ = ["search_permits", "rank_contractors"]
