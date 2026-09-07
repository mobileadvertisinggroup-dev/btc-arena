"""SQLite schema for the study. One file, fully auditable, exportable to CSV."""
from __future__ import annotations
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "youtube_study.sqlite"

SCHEMA = """
PRAGMA journal_mode=WAL;

-- Phase 1: every channel the discovery pass surfaced, with why it surfaced.
CREATE TABLE IF NOT EXISTS discovery_hits (
    query           TEXT NOT NULL,
    query_group     TEXT NOT NULL,   -- A / B / C / D
    relevance_lang  TEXT,
    region_code     TEXT,
    channel_id      TEXT NOT NULL,
    channel_title   TEXT,
    rank_in_result  INTEGER,
    collected_at    TEXT NOT NULL,
    evidence_file   TEXT,
    PRIMARY KEY (query, channel_id)
);

-- Phase 2: channel-level facts, straight from the API.
CREATE TABLE IF NOT EXISTS channels (
    channel_id        TEXT PRIMARY KEY,
    title             TEXT,
    handle            TEXT,
    channel_url       TEXT,
    description       TEXT,
    country           TEXT,          -- NULL = not disclosed by the channel
    default_language  TEXT,
    published_at      TEXT,
    subscriber_count  INTEGER,
    hidden_subs       INTEGER,       -- 1 if the channel hides its sub count
    view_count        INTEGER,
    video_count       INTEGER,
    uploads_playlist  TEXT,
    topic_categories  TEXT,          -- JSON array of Wikipedia topic URLs
    thumbnail_url     TEXT,
    bucket            TEXT,          -- A / B / C / D assignment
    collected_at      TEXT NOT NULL,
    evidence_file     TEXT
);

-- Phase 2: video-level facts.
CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL,
    title             TEXT,
    description       TEXT,
    published_at      TEXT,
    duration_iso      TEXT,
    duration_sec      INTEGER,
    is_short          INTEGER,       -- heuristic: <= 180s (see methodology)
    view_count        INTEGER,
    like_count        INTEGER,       -- NULL when the channel hides likes
    comment_count     INTEGER,       -- NULL when comments are disabled
    default_language  TEXT,
    default_audio_lang TEXT,
    caption_flag      TEXT,          -- API 'caption' field: true/false
    licensed_content  INTEGER,
    live_broadcast    TEXT,
    tags              TEXT,          -- JSON array
    category_id       TEXT,
    thumbnail_url     TEXT,
    video_url         TEXT,
    collected_at      TEXT NOT NULL,
    evidence_file     TEXT,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

-- Phase 4: manual/derived coding of subject + format. Kept SEPARATE from facts.
CREATE TABLE IF NOT EXISTS video_coding (
    video_id        TEXT PRIMARY KEY,
    subject         TEXT,   -- crypto/gold/stocks/macro/indicators/news/education/other
    subject_method  TEXT,   -- 'keyword_rule' etc. -- never presented as ground truth
    title_has_number   INTEGER,
    title_has_year     INTEGER,
    title_has_question INTEGER,
    title_has_asset    INTEGER,
    title_word_count   INTEGER,
    title_char_count   INTEGER,
    coded_at        TEXT
);

-- Every run recorded, so results can be reproduced and dated.
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT,
    finished_at   TEXT,
    phase         TEXT,
    notes         TEXT,
    quota_used    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


if __name__ == "__main__":
    c = connect()
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"DB ready at {DB_PATH}")
    print("tables:", tables)
