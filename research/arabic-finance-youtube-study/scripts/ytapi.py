"""
YouTube Data API v3 client for the Arabic finance channel study.

Design goals:
  * Every raw API response is archived to disk (evidence preservation / auditability).
  * Quota is tracked and enforced locally so we never blow the 10,000 unit/day free tier.
  * Only lawful, public, metadata endpoints are used. No scraping, no video downloads.
"""
from __future__ import annotations

import json
import os
import time
import hashlib
import datetime as dt
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "raw_api"
LOGS = ROOT / "logs"
EVIDENCE.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

BASE = "https://youtube.googleapis.com/youtube/v3"

# Documented quota costs (units per call), YouTube Data API v3.
QUOTA_COST = {
    "channels": 1,
    "playlistItems": 1,
    "videos": 1,
    "playlists": 1,
    "search": 100,
    "captions": 50,
    "commentThreads": 1,
}

DAILY_QUOTA_LIMIT = 10_000


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class QuotaExceeded(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, api_key: str | None = None, budget: int = DAILY_QUOTA_LIMIT):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "No API key. Set YOUTUBE_API_KEY in the environment or pass api_key=."
            )
        self.budget = budget
        self.ledger_path = LOGS / "quota_ledger.jsonl"
        self.used = self._replay_ledger()
        self.session = requests.Session()

    # ---------------- quota ----------------
    def _replay_ledger(self) -> int:
        """Sum today's quota usage so restarts don't lose track."""
        if not self.ledger_path.exists():
            return 0
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        total = 0
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", "").startswith(today):
                total += rec.get("cost", 0)
        return total

    def _charge(self, endpoint: str, params: dict) -> None:
        cost = QUOTA_COST.get(endpoint, 1)
        if self.used + cost > self.budget:
            raise QuotaExceeded(
                f"Quota budget {self.budget} would be exceeded "
                f"(used={self.used}, next call={endpoint} costs {cost})."
            )
        self.used += cost
        safe = {k: v for k, v in params.items() if k != "key"}
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": utcnow(), "endpoint": endpoint, "cost": cost,
                "cumulative_today": self.used, "params": safe,
            }, ensure_ascii=False) + "\n")

    def quota_remaining(self) -> int:
        return self.budget - self.used

    # ---------------- transport ----------------
    def _archive(self, endpoint: str, params: dict, payload: Any) -> str:
        safe = {k: v for k, v in params.items() if k != "key"}
        digest = hashlib.sha256(
            json.dumps([endpoint, safe], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = EVIDENCE / f"{endpoint}_{stamp}_{digest}.json"
        path.write_text(json.dumps({
            "collected_at_utc": utcnow(),
            "endpoint": endpoint,
            "request_url": f"{BASE}/{endpoint}",
            "request_params": safe,
            "response": payload,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return str(path.relative_to(ROOT))

    def call(self, endpoint: str, **params) -> dict:
        """Single API call. Archives the raw response and charges quota."""
        params = {k: v for k, v in params.items() if v is not None}
        self._charge(endpoint, params)
        params["key"] = self.api_key
        url = f"{BASE}/{endpoint}"

        last_err = None
        for attempt in range(5):
            try:
                r = self.session.get(url, params=params, timeout=45)
            except requests.RequestException as e:  # network hiccup
                last_err = e
                time.sleep(2 ** attempt)
                continue

            if r.status_code == 200:
                payload = r.json()
                payload["_evidence_file"] = self._archive(endpoint, params, payload)
                payload["_collected_at_utc"] = utcnow()
                return payload

            # 403 quotaExceeded / keyInvalid are terminal; 5xx and 429 are retryable.
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(2 ** attempt)
                continue

            raise RuntimeError(f"{endpoint} failed HTTP {r.status_code}: {r.text[:600]}")

        raise RuntimeError(f"{endpoint} failed after retries: {last_err}")

    def paged(self, endpoint: str, max_pages: int = 40, **params) -> Iterator[dict]:
        """Yield every page of a paginated endpoint."""
        token = None
        for _ in range(max_pages):
            page = self.call(endpoint, pageToken=token, **params)
            yield page
            token = page.get("nextPageToken")
            if not token:
                return

    # ---------------- typed helpers ----------------
    def channels_by_id(self, channel_ids: Iterable[str]) -> list[dict]:
        ids = list(dict.fromkeys(channel_ids))
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            page = self.call(
                "channels",
                part="snippet,statistics,contentDetails,topicDetails,brandingSettings,status",
                id=",".join(chunk), maxResults=50,
            )
            out.extend(page.get("items", []))
        return out

    def channel_by_handle(self, handle: str) -> dict | None:
        h = handle if handle.startswith("@") else "@" + handle
        page = self.call(
            "channels",
            part="snippet,statistics,contentDetails,topicDetails,brandingSettings,status",
            forHandle=h,
        )
        items = page.get("items", [])
        return items[0] if items else None

    def search_channels(self, query: str, *, relevance_language: str | None = None,
                        region_code: str | None = None, max_results: int = 25) -> list[dict]:
        page = self.call(
            "search", part="snippet", q=query, type="channel",
            maxResults=min(max_results, 50),
            relevanceLanguage=relevance_language, regionCode=region_code,
        )
        return page.get("items", [])

    def search_videos(self, query: str, *, relevance_language: str | None = None,
                      region_code: str | None = None, published_after: str | None = None,
                      order: str = "viewCount", max_results: int = 25) -> list[dict]:
        page = self.call(
            "search", part="snippet", q=query, type="video",
            order=order, maxResults=min(max_results, 50),
            relevanceLanguage=relevance_language, regionCode=region_code,
            publishedAfter=published_after,
        )
        return page.get("items", [])

    def uploads_playlist_id(self, channel_item: dict) -> str | None:
        return (channel_item.get("contentDetails", {})
                .get("relatedPlaylists", {}).get("uploads"))

    def playlist_video_ids(self, playlist_id: str, *, limit: int = 1000) -> list[dict]:
        """Return [{videoId, publishedAt}] newest-first from an uploads playlist."""
        rows = []
        for page in self.paged("playlistItems", part="contentDetails",
                               playlistId=playlist_id, maxResults=50,
                               max_pages=(limit // 50) + 1):
            for it in page.get("items", []):
                cd = it.get("contentDetails", {})
                if cd.get("videoId"):
                    rows.append({"videoId": cd["videoId"],
                                 "videoPublishedAt": cd.get("videoPublishedAt")})
            if len(rows) >= limit:
                break
        return rows[:limit]

    def videos_by_id(self, video_ids: Iterable[str]) -> list[dict]:
        ids = list(dict.fromkeys(video_ids))
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            page = self.call(
                "videos",
                part="snippet,statistics,contentDetails,status,topicDetails",
                id=",".join(chunk), maxResults=50,
            )
            out.extend(page.get("items", []))
        return out

    def caption_tracks(self, video_id: str) -> list[dict]:
        """List available caption tracks (metadata only; does not download text)."""
        page = self.call("captions", part="snippet", videoId=video_id)
        return page.get("items", [])
