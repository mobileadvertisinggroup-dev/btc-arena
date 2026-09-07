"""
PHASE 2 - Data collection.

For each selected channel: pull channel facts, then walk the uploads playlist
and fetch full metadata for every video published in the study window.

Quota is cheap on this path (channels.list=1, playlistItems.list=1 per 50,
videos.list=1 per 50), so a 30-channel / 24-month harvest costs well under
1,000 units. search.list (100/call) is only used in discovery.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ytapi import YouTubeClient, utcnow          # noqa: E402
from db import connect                            # noqa: E402

# Study window: 24 months back from collection date.
WINDOW_MONTHS = 24
SHORT_MAX_SEC = 180   # see METHODOLOGY.json for why this is a heuristic

ISO_DUR = re.compile(
    r"P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def iso_duration_to_sec(iso: str | None) -> int | None:
    if not iso:
        return None
    m = ISO_DUR.fullmatch(iso)
    if not m:
        return None
    p = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return p["d"] * 86400 + p["h"] * 3600 + p["m"] * 60 + p["s"]


def cutoff_iso(months: int = WINDOW_MONTHS) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(days=months * 30.44)).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_channel(con, item: dict, bucket: str) -> str:
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    cid = item["id"]
    handle = sn.get("customUrl")
    topics = item.get("topicDetails", {}).get("topicCategories", [])
    con.execute("""
        INSERT OR REPLACE INTO channels
        (channel_id,title,handle,channel_url,description,country,default_language,
         published_at,subscriber_count,hidden_subs,view_count,video_count,
         uploads_playlist,topic_categories,thumbnail_url,bucket,collected_at,evidence_file)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        cid, sn.get("title"), handle,
        f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{cid}",
        sn.get("description"), sn.get("country"), sn.get("defaultLanguage"),
        sn.get("publishedAt"),
        int(st["subscriberCount"]) if st.get("subscriberCount") is not None else None,
        1 if st.get("hiddenSubscriberCount") else 0,
        int(st["viewCount"]) if st.get("viewCount") is not None else None,
        int(st["videoCount"]) if st.get("videoCount") is not None else None,
        item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
        json.dumps(topics, ensure_ascii=False),
        (sn.get("thumbnails", {}).get("high") or {}).get("url"),
        bucket, utcnow(), item.get("_evidence_file"),
    ))
    return cid


def upsert_video(con, item: dict) -> None:
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    cd = item.get("contentDetails", {})
    vid = item["id"]
    dur = iso_duration_to_sec(cd.get("duration"))
    con.execute("""
        INSERT OR REPLACE INTO videos
        (video_id,channel_id,title,description,published_at,duration_iso,duration_sec,
         is_short,view_count,like_count,comment_count,default_language,default_audio_lang,
         caption_flag,licensed_content,live_broadcast,tags,category_id,thumbnail_url,
         video_url,collected_at,evidence_file)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        vid, sn.get("channelId"), sn.get("title"), sn.get("description"),
        sn.get("publishedAt"), cd.get("duration"), dur,
        1 if (dur is not None and dur <= SHORT_MAX_SEC) else 0,
        int(st["viewCount"]) if st.get("viewCount") is not None else None,
        int(st["likeCount"]) if st.get("likeCount") is not None else None,
        int(st["commentCount"]) if st.get("commentCount") is not None else None,
        sn.get("defaultLanguage"), sn.get("defaultAudioLanguage"),
        cd.get("caption"), 1 if cd.get("licensedContent") else 0,
        sn.get("liveBroadcastContent"),
        json.dumps(sn.get("tags", []), ensure_ascii=False), sn.get("categoryId"),
        (sn.get("thumbnails", {}).get("high") or {}).get("url"),
        f"https://www.youtube.com/watch?v={vid}",
        utcnow(), item.get("_evidence_file"),
    ))


def collect_channel(client: YouTubeClient, con, channel_item: dict,
                    bucket: str, max_videos: int = 600) -> int:
    cid = upsert_channel(con, channel_item, bucket)
    title = channel_item.get("snippet", {}).get("title")
    uploads = client.uploads_playlist_id(channel_item)
    if not uploads:
        print(f"  ! {title}: no uploads playlist (uploads hidden) - channel facts only")
        con.commit()
        return 0

    cut = cutoff_iso()
    refs = client.playlist_video_ids(uploads, limit=max_videos)
    in_window = [r["videoId"] for r in refs
                 if (r.get("videoPublishedAt") or "") >= cut]
    # Keep at least the 60 most recent even if the channel is slow-moving,
    # so median-of-last-N metrics stay computable.
    if len(in_window) < 60:
        in_window = [r["videoId"] for r in refs[:60]]

    vids = client.videos_by_id(in_window)
    for v in vids:
        upsert_video(con, v)
    con.commit()
    print(f"  + {title[:44]:44} {len(vids):4} videos in window "
          f"(quota left {client.quota_remaining()})")
    return len(vids)


def run(channel_ids: list[str], bucket_map: dict[str, str], budget: int = 9000) -> None:
    client = YouTubeClient(budget=budget)
    con = connect()
    run_id = uuid.uuid4().hex[:12]
    started = utcnow()
    print(f"[collect] run {run_id} | {len(channel_ids)} channels | "
          f"window={WINDOW_MONTHS}mo | cutoff={cutoff_iso()}")

    items = client.channels_by_id(channel_ids)
    found = {it["id"] for it in items}
    missing = [c for c in channel_ids if c not in found]
    if missing:
        print(f"[collect] WARNING {len(missing)} channel ids returned nothing "
              f"(deleted/terminated/invalid): {missing}")

    total = 0
    for it in items:
        if client.quota_remaining() < 60:
            print("[collect] stopping: quota budget nearly spent")
            break
        try:
            total += collect_channel(client, con, it, bucket_map.get(it["id"], "?"))
        except Exception as e:
            print(f"  ! {it.get('snippet',{}).get('title')}: {e}")

    con.execute(
        "INSERT OR REPLACE INTO collection_runs "
        "(run_id,started_at,finished_at,phase,notes,quota_used) VALUES (?,?,?,?,?,?)",
        (run_id, started, utcnow(), "collection",
         f"{len(items)} channels, {total} videos, window={WINDOW_MONTHS}mo", client.used))
    con.commit()
    print(f"[collect] done. {len(items)} channels, {total} videos. quota used {client.used}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--bucket", default="?")
    a = ap.parse_args()
    run(a.ids, {i: a.bucket for i in a.ids})
