"""
PHASE 3 - Quantitative analysis.

Age-bias control (the user's explicit requirement):
  A video published yesterday has not finished accruing views, so comparing its
  raw view count against a two-year-old video is invalid. Two guards are used:

  1. MATURITY FILTER - cross-video comparisons only use videos at least
     MATURITY_DAYS old. Younger videos are reported separately, never mixed in.
  2. VPD (views per day) - a rate, reported alongside raw views, plus
     COHORT comparison, where a video is scored against the median of videos
     from the SAME channel in the SAME quarter.

Outputs go to data/processed/ as CSV.
"""
from __future__ import annotations

import json, sys, math, datetime as dt
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect, DB_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

MATURITY_DAYS = 30      # a video must be this old to enter cross-video comparisons
OUTPERFORM_X = 2.0      # "substantially outperforms" = >= 2x the channel median
SHORT_MAX_SEC = 180


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    con = connect()
    ch = pd.read_sql_query("SELECT * FROM channels", con)
    vd = pd.read_sql_query("""
        SELECT v.*, c.subject, c.title_has_number, c.title_has_year,
               c.title_has_question, c.title_has_asset, c.title_word_count
        FROM videos v LEFT JOIN video_coding c USING(video_id)""", con)
    if vd.empty:
        return ch, vd
    vd["published_at"] = pd.to_datetime(vd["published_at"], format="ISO8601", utc=True)
    now = pd.Timestamp.now(tz="UTC")
    vd["age_days"] = (now - vd["published_at"]).dt.total_seconds() / 86400
    vd["mature"] = vd["age_days"] >= MATURITY_DAYS
    vd["vpd"] = vd["view_count"] / vd["age_days"].clip(lower=1)
    vd["quarter"] = vd["published_at"].dt.to_period("Q").astype(str)
    vd["dow"] = vd["published_at"].dt.day_name()
    vd["hour_utc"] = vd["published_at"].dt.hour
    vd["is_short"] = vd["is_short"].astype(bool)
    vd["duration_bucket"] = pd.cut(
        vd["duration_sec"],
        bins=[0, 60, 180, 480, 720, 1200, 1800, 10**9],
        labels=["0-1m", "1-3m", "3-8m", "8-12m", "12-20m", "20-30m", "30m+"])
    vd["engagement_rate"] = vd["like_count"] / vd["view_count"].replace(0, np.nan)
    return ch, vd


def gini(x: np.ndarray) -> float:
    """View concentration. 0 = every video equal, 1 = one video takes everything."""
    x = np.sort(np.asarray(x, dtype=float))
    x = x[~np.isnan(x)]
    if len(x) < 2 or x.sum() == 0:
        return float("nan")
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def channel_metrics(ch: pd.DataFrame, vd: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, c in ch.iterrows():
        v = vd[vd["channel_id"] == c["channel_id"]].sort_values("published_at",
                                                                ascending=False)
        if v.empty:
            continue
        mat = v[v["mature"]]
        longf = mat[~mat["is_short"]]
        shorts = mat[mat["is_short"]]

        span_days = max((v["published_at"].max() - v["published_at"].min()).days, 1)
        upw = len(v) / (span_days / 7)
        upm = len(v) / (span_days / 30.44)

        base = longf if len(longf) >= 5 else mat
        med = base["view_count"].median() if len(base) else np.nan
        outperf = (base["view_count"] >= OUTPERFORM_X * med).mean() if med and med > 0 else np.nan

        # view concentration: share of total views held by the top 10% of videos
        vals = base["view_count"].dropna().sort_values(ascending=False)
        top10 = (vals.head(max(1, int(len(vals) * 0.1))).sum() / vals.sum()
                 if vals.sum() else np.nan)

        subs = c["subscriber_count"]
        rows.append({
            "channel_id": c["channel_id"], "title": c["title"],
            "handle": c["handle"], "url": c["channel_url"], "bucket": c["bucket"],
            "country": c["country"] or "not disclosed",
            "subscriber_count": subs, "total_views": c["view_count"],
            "public_video_count": c["video_count"],
            "videos_collected": len(v), "videos_mature": len(mat),
            "first_seen": str(v["published_at"].min().date()),
            "last_upload": str(v["published_at"].max().date()),
            "days_since_last_upload": int((pd.Timestamp.now(tz="UTC")
                                           - v["published_at"].max()).days),
            "uploads_per_week": round(upw, 2), "uploads_per_month": round(upm, 2),
            "median_views_all_mature": med,
            "mean_views_all_mature": base["view_count"].mean(),
            "median_views_last10": v.head(10)["view_count"].median(),
            "median_views_last30": v.head(30)["view_count"].median(),
            "median_vpd": base["vpd"].median(),
            "views_per_sub": round(med / subs, 4) if subs else np.nan,
            "n_longform": len(longf), "n_shorts": len(shorts),
            "median_views_longform": longf["view_count"].median() if len(longf) else np.nan,
            "median_views_shorts": shorts["view_count"].median() if len(shorts) else np.nan,
            "median_duration_sec_longform": longf["duration_sec"].median() if len(longf) else np.nan,
            "pct_outperform_2x": round(outperf, 3) if outperf == outperf else np.nan,
            "top10pct_view_share": round(top10, 3) if top10 == top10 else np.nan,
            "gini_views": round(gini(base["view_count"].values), 3),
            "median_engagement_rate": round(base["engagement_rate"].median(), 4)
                if base["engagement_rate"].notna().any() else np.nan,
            "likes_hidden_pct": round(base["like_count"].isna().mean(), 2),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("median_views_last30", ascending=False) if not df.empty else df


def breakdown(vd: pd.DataFrame, by: str, min_n: int = 4) -> pd.DataFrame:
    """Median views per group, per channel, using mature videos only."""
    mat = vd[vd["mature"] & ~vd["is_short"]]
    g = mat.groupby(["channel_id", by], observed=True).agg(
        n=("video_id", "count"),
        median_views=("view_count", "median"),
        median_vpd=("vpd", "median")).reset_index()
    g = g[g["n"] >= min_n]
    ch_med = mat.groupby("channel_id")["view_count"].median().rename("channel_median")
    g = g.join(ch_med, on="channel_id")
    g["index_vs_channel"] = (g["median_views"] / g["channel_median"]).round(2)
    return g.sort_values(["channel_id", "index_vs_channel"], ascending=[True, False])


def cohort_scores(vd: pd.DataFrame) -> pd.DataFrame:
    """Score each video against its own channel+quarter cohort. Age-fair."""
    mat = vd[vd["mature"]].copy()
    med = mat.groupby(["channel_id", "quarter"])["view_count"].transform("median")
    n = mat.groupby(["channel_id", "quarter"])["view_count"].transform("count")
    mat["cohort_median"] = med
    mat["cohort_n"] = n
    mat["cohort_index"] = (mat["view_count"] / med).round(2)
    return mat[mat["cohort_n"] >= 4].sort_values("cohort_index", ascending=False)


def main() -> None:
    ch, vd = load()
    if vd.empty:
        print("[analyze] NO VIDEO DATA in the database. Run discover.py + collect.py first.")
        return

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[analyze] {len(ch)} channels | {len(vd)} videos | "
          f"{vd['mature'].sum()} mature (>={MATURITY_DAYS}d) | {stamp}")

    cm = channel_metrics(ch, vd)
    cm.to_csv(OUT / "channel_metrics.csv", index=False)
    print(f"[analyze] channel_metrics.csv  ({len(cm)} rows)")

    for name, col in [("by_subject", "subject"), ("by_duration", "duration_bucket"),
                      ("by_weekday", "dow"), ("by_quarter", "quarter")]:
        b = breakdown(vd, col)
        b.to_csv(OUT / f"{name}.csv", index=False)
        print(f"[analyze] {name}.csv ({len(b)} rows)")

    cs = cohort_scores(vd)
    keep = ["video_id", "channel_id", "title", "published_at", "quarter", "duration_sec",
            "is_short", "view_count", "like_count", "comment_count", "vpd", "subject",
            "cohort_median", "cohort_index", "video_url", "thumbnail_url"]
    cs[keep].to_csv(OUT / "video_cohort_scores.csv", index=False)
    print(f"[analyze] video_cohort_scores.csv ({len(cs)} rows)")

    # Shorts vs long-form, per channel
    mat = vd[vd["mature"]]
    sv = mat.groupby(["channel_id", "is_short"]).agg(
        n=("video_id", "count"), median_views=("view_count", "median"),
        median_engagement=("engagement_rate", "median")).reset_index()
    sv.to_csv(OUT / "shorts_vs_longform.csv", index=False)

    vd.to_csv(OUT / "videos_full.csv", index=False)
    ch.to_csv(OUT / "channels_full.csv", index=False)
    print(f"[analyze] wrote raw exports. DB: {DB_PATH}")


if __name__ == "__main__":
    main()
