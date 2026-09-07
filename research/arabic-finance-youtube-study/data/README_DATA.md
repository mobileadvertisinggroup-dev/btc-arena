# Data files

Collected 2026-09-07 (UTC) via the official YouTube Data API v3.

## Unpacking

```bash
gunzip -k data/youtube_study.sqlite.gz              # the full database (47 MB unpacked)
gunzip -k data/processed/videos_full.csv.gz         # 16,281 video rows (38 MB unpacked)
gunzip -k data/processed/video_cohort_scores.csv.gz # age-fair per-video scores
tar -xzf evidence/raw_api_archive.tar.gz -C evidence/   # 823 raw API responses
```

## What is in the database

| Table | Rows | Contents |
|---|---|---|
| `discovery_hits` | 597 | Every channel returned by every discovery query, with the query and its rank |
| `channels` | 48 | Channel facts as returned by `channels.list` |
| `videos` | 16,281 | Video facts as returned by `videos.list`, 24-month window |
| `video_coding` | 16,281 | **Derived** subject + title features. Rule-based estimate, not fact |
| `collection_runs` | 2 | Run log with timestamps and quota spend |

`channels` and `videos` hold **only** values returned verbatim by the API.
Anything inferred lives in `video_coding` or in `data/format_coding.csv`.

## Provenance

Every row in `channels` and `videos` carries an `evidence_file` column pointing at the
exact archived raw response inside `evidence/raw_api_archive.tar.gz`, plus a
`collected_at` UTC timestamp. Any number in the report can be traced back to its
original API response.

## Deliberately absent

Watch time, click-through rate, retention, impressions, traffic sources, and audience
demographics. These are private channel analytics. They are not in the API, they were
not estimated, and no claim in the report depends on them.
