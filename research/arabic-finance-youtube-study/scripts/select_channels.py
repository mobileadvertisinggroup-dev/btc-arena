"""
PHASE 1B - Verify and rank discovery candidates, then select the study set.

Every candidate channel id from discovery_hits is verified against channels.list
(a channel that returns nothing is deleted/terminated/invalid and is dropped).
Ranking is deliberately NOT subscriber count. It is a composite of reach
efficiency, recent output, consistency and relevance.
"""
from __future__ import annotations

import json, sys, re, datetime as dt
from pathlib import Path
import pandas as pd, numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ytapi import YouTubeClient, utcnow   # noqa: E402
from db import connect                     # noqa: E402
from collect import upsert_channel         # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

MIN_SUBS = 20_000          # below this a channel is not yet a useful benchmark
MIN_VIDEOS = 20
INACTIVE_DAYS = 180        # no upload in this long => excluded unless flagged

ARABIC = re.compile(r"[؀-ۿ]")

# Keywords that confirm the channel is actually on-topic for this study.
TOPIC_WORDS = ["مال", "اقتصاد", "استثمار", "تداول", "أسهم", "بورصة", "ذهب", "عملات",
               "تمويل", "بيتكوين", "فوركس", "ثروة", "ادخار", "محفظة",
               "money", "finance", "financial", "invest", "investing", "econom",
               "trading", "trader", "stock", "market", "crypto", "bitcoin", "gold",
               "wealth", "wall street", "portfolio", "budget"]


def is_on_topic(title: str, desc: str) -> bool:
    hay = f"{title} {desc}".lower()
    return any(w in hay for w in TOPIC_WORDS)


def has_arabic(text: str) -> bool:
    return bool(ARABIC.search(text or ""))


def verify_and_score(budget: int = 9000, per_bucket: int = 12) -> pd.DataFrame:
    client = YouTubeClient(budget=budget)
    con = connect()

    hits = pd.read_sql_query(
        "SELECT channel_id, query_group, COUNT(*) n_queries, "
        "       MIN(rank_in_result) best_rank, MIN(channel_title) title "
        "FROM discovery_hits GROUP BY channel_id, query_group", con)
    if hits.empty:
        print("[select] discovery_hits is empty - run discover.py first.")
        return pd.DataFrame()

    # A channel can surface in several buckets; keep its strongest bucket.
    hits = hits.sort_values(["channel_id", "n_queries", "best_rank"],
                            ascending=[True, False, True])
    primary = hits.drop_duplicates("channel_id", keep="first")
    ids = primary["channel_id"].tolist()
    print(f"[select] verifying {len(ids)} unique candidate channels via channels.list")

    items = client.channels_by_id(ids)
    found = {it["id"] for it in items}
    print(f"[select] verified {len(items)} | {len(ids)-len(found)} did not resolve (dropped)")

    rows = []
    now = dt.datetime.now(dt.timezone.utc)
    for it in items:
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        cid = it["id"]
        subs = int(st.get("subscriberCount", 0) or 0)
        vids = int(st.get("videoCount", 0) or 0)
        views = int(st.get("viewCount", 0) or 0)
        title, desc = sn.get("title", ""), sn.get("description", "") or ""
        created = sn.get("publishedAt")
        age_days = ((now - dt.datetime.fromisoformat(created.replace("Z", "+00:00"))).days
                    if created else np.nan)
        bucket = primary.loc[primary["channel_id"] == cid, "query_group"].iloc[0]
        nq = int(primary.loc[primary["channel_id"] == cid, "n_queries"].iloc[0])
        rows.append({
            "channel_id": cid, "title": title, "handle": sn.get("customUrl"),
            "url": f"https://www.youtube.com/{sn.get('customUrl')}" if sn.get("customUrl")
                   else f"https://www.youtube.com/channel/{cid}",
            "bucket": bucket, "n_discovery_queries": nq,
            "country": sn.get("country"), "created": created, "age_days": age_days,
            "subs": subs, "videos": vids, "total_views": views,
            "avg_views_per_video": round(views / vids, 1) if vids else np.nan,
            "views_per_sub_lifetime": round(views / subs, 2) if subs else np.nan,
            "uploads_per_month_lifetime": round(vids / (age_days / 30.44), 2)
                                          if age_days and age_days > 30 else np.nan,
            "arabic_signal": int(has_arabic(title + desc)),
            "on_topic": int(is_on_topic(title, desc)),
            "hidden_subs": int(bool(st.get("hiddenSubscriberCount"))),
            "_item": it,
        })

    df = pd.DataFrame(rows)
    df["eligible"] = ((df["subs"] >= MIN_SUBS) & (df["videos"] >= MIN_VIDEOS)
                      & (df["on_topic"] == 1))
    # Arabic buckets must actually be Arabic; international buckets must not be.
    df.loc[df["bucket"].isin(["A", "B"]) & (df["arabic_signal"] == 0), "eligible"] = False

    # Composite score. Percentile-ranked so no single metric dominates.
    e = df[df["eligible"]].copy()
    if not e.empty:
        for c, w in [("avg_views_per_video", 0.30), ("views_per_sub_lifetime", 0.25),
                     ("uploads_per_month_lifetime", 0.20), ("n_discovery_queries", 0.15),
                     ("subs", 0.10)]:
            e[f"pct_{c}"] = e[c].rank(pct=True)
            e[f"w_{c}"] = e[f"pct_{c}"] * w
        e["score"] = e[[c for c in e.columns if c.startswith("w_")]].sum(axis=1).round(4)
        e = e.sort_values(["bucket", "score"], ascending=[True, False])
        sel = e.groupby("bucket").head(per_bucket)
    else:
        sel = e

    # Persist channel facts for the selected set.
    for _, r in sel.iterrows():
        upsert_channel(con, r["_item"], r["bucket"])
    con.commit()

    drop = ["_item"]
    df.drop(columns=drop).to_csv(OUT / "discovery_candidates_all.csv", index=False)
    sel.drop(columns=drop).to_csv(OUT / "selected_channels.csv", index=False)
    print(f"[select] {len(df)} candidates -> {df['eligible'].sum()} eligible -> "
          f"{len(sel)} selected")
    for b in sorted(sel["bucket"].unique()):
        print(f"   bucket {b}: {(sel['bucket']==b).sum()} channels")
    print(f"[select] quota used {client.used}")
    return sel.drop(columns=drop)


if __name__ == "__main__":
    verify_and_score()
