"""
PHASE 1 - Discovery.

Channels are found by querying the YouTube Data API, not from memory.
Each query is tagged with the bucket it serves:
  A = Arabic general finance / economics education
  B = Arabic trading / crypto / gold / stocks / market analysis
  C = International finance / investing / markets
  D = International faceless / animated / whiteboard explainer finance

search.list costs 100 quota units per call, so the query list is deliberate.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ytapi import YouTubeClient, utcnow          # noqa: E402
from db import connect                            # noqa: E402

# (query, bucket, relevanceLanguage, regionCode)
QUERIES: list[tuple[str, str, str | None, str | None]] = [
    # ---- A: Arabic general financial / economic education ----
    ("الثقافة المالية والاستثمار",        "A", "ar", "EG"),
    ("تعلم الاستثمار للمبتدئين",          "A", "ar", "SA"),
    ("الاقتصاد ببساطة شرح",               "A", "ar", "EG"),
    ("الذكاء المالي وإدارة الأموال",      "A", "ar", "SA"),
    ("شرح اقتصادي تحليل الأخبار الاقتصادية", "A", "ar", "AE"),
    ("التخطيط المالي والادخار",           "A", "ar", "MA"),

    # ---- B: Arabic trading / crypto / gold / stocks ----
    ("تحليل العملات الرقمية بيتكوين",      "B", "ar", "EG"),
    ("تعلم التداول من الصفر فوركس",        "B", "ar", "SA"),
    ("تحليل سعر الذهب اليوم",              "B", "ar", "AE"),
    ("تحليل الأسهم السعودية",              "B", "ar", "SA"),
    ("المؤشرات الفنية استراتيجية تداول",   "B", "ar", "EG"),
    ("التحليل الفني للأسواق شرح",          "B", "ar", "JO"),

    # ---- C: International finance / investing / markets ----
    ("personal finance explained",         "C", "en", "US"),
    ("investing for beginners stock market", "C", "en", "US"),
    ("stock market analysis this week",    "C", "en", "US"),
    ("crypto bitcoin explained analysis",  "C", "en", "US"),
    ("gold investing macro economy",       "C", "en", "GB"),
    ("trading indicators strategy explained", "C", "en", "US"),

    # ---- D: faceless / animated / whiteboard explainer finance ----
    ("whiteboard animation finance explained", "D", "en", "US"),
    ("economics explained animated channel",   "D", "en", "US"),
    ("animated finance education no face",     "D", "en", "US"),
    ("how money works explained animation",    "D", "en", "US"),
    ("doodle whiteboard money explainer",      "D", "en", "GB"),
    ("finance documentary animated explainer", "D", "en", "US"),
]


def run(budget: int = 9000) -> None:
    client = YouTubeClient(budget=budget)
    con = connect()
    run_id = uuid.uuid4().hex[:12]
    started = utcnow()
    print(f"[discover] run {run_id} | quota remaining {client.quota_remaining()}")

    total_hits = 0
    for query, bucket, lang, region in QUERIES:
        if client.quota_remaining() < 150:
            print("[discover] stopping early: quota budget nearly spent")
            break
        try:
            items = client.search_channels(
                query, relevance_language=lang, region_code=region, max_results=25)
        except Exception as e:
            print(f"[discover] FAILED q={query!r}: {e}")
            continue

        ev = None
        rows = []
        for rank, it in enumerate(items, 1):
            cid = it.get("snippet", {}).get("channelId") or it.get("id", {}).get("channelId")
            if not cid:
                continue
            rows.append((query, bucket, lang, region, cid,
                         it.get("snippet", {}).get("channelTitle"), rank, utcnow(), ev))
        con.executemany(
            "INSERT OR REPLACE INTO discovery_hits "
            "(query,query_group,relevance_lang,region_code,channel_id,channel_title,"
            " rank_in_result,collected_at,evidence_file) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        total_hits += len(rows)
        print(f"[discover] {bucket} {query[:42]:42} -> {len(rows):2} channels "
              f"(quota left {client.quota_remaining()})")

    con.execute(
        "INSERT OR REPLACE INTO collection_runs "
        "(run_id,started_at,finished_at,phase,notes,quota_used) VALUES (?,?,?,?,?,?)",
        (run_id, started, utcnow(), "discovery",
         f"{len(QUERIES)} queries, {total_hits} channel hits", client.used))
    con.commit()

    uniq = con.execute("SELECT COUNT(DISTINCT channel_id) FROM discovery_hits").fetchone()[0]
    print(f"[discover] done. {total_hits} hits, {uniq} unique channels. "
          f"quota used this session: {client.used}")


if __name__ == "__main__":
    run()
