"""Agricultural-vertical extractor package (change add-agriculture-industry-sources, W2).

One module per source unit; each exposes ``SOURCE``, ``CONCEPTS`` and
``extract(cur, notes)``. The write discipline is NOT re-implemented here — it is
imported from ``scripts/restaurant_extractors/_common.py`` (same contract:
additive INSERT ... ON CONFLICT DO NOTHING on ``uq_sem_obs``, obs_month bucket at
the write site, concepts born verified=false, trend gate with explicit notes).
"""
