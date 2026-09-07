"""
Derived coding of videos by subject and title features.

IMPORTANT: this is a RULE-BASED HEURISTIC, not ground truth. Everything it
produces is stored in `video_coding`, kept separate from the `videos` fact
table, and is labelled as a calculation (not a fact) in the report.
"""
from __future__ import annotations
import re, sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect  # noqa: E402

# Arabic + English keyword rules. Order matters: first match wins.
SUBJECT_RULES: list[tuple[str, list[str]]] = [
    ("crypto", ["بيتكوين", "بتكوين", "العملات الرقمية", "عملة رقمية", "كريبتو", "ايثيريوم",
                "إيثيريوم", "الميتافيرس", "بلوكشين", "التشفير",
                "bitcoin", "btc", "crypto", "ethereum", "altcoin", "blockchain", "solana",
                "xrp", "stablecoin", "defi"]),
    ("gold", ["الذهب", "ذهب", "الفضة", "المعادن الثمينة", "أوقية",
              "gold", "silver", "bullion", "precious metal"]),
    ("stocks", ["الأسهم", "سهم", "البورصة", "تاسي", "أرامكو", "توزيعات",
                "الاكتتاب", "المؤشر العام",
                "stock", "stocks", "shares", "equity", "equities", "s&p", "nasdaq",
                "dividend", "ipo", "etf", "portfolio"]),
    ("indicators", ["المؤشرات الفنية", "التحليل الفني", "مؤشر", "الشموع", "ستوكاستك",
                    "الماكد", "فيبوناتشي", "الدعم والمقاومة", "استراتيجية تداول",
                    "rsi", "macd", "moving average", "fibonacci", "candlestick",
                    "indicator", "chart pattern", "support and resistance",
                    "trading strategy", "backtest"]),
    ("macro", ["التضخم", "الفائدة", "الركود", "الفيدرالي", "الاقتصاد العالمي", "الدولار",
               "العملة", "الناتج المحلي", "أزمة اقتصادية", "البنك المركزي", "الديون",
               "inflation", "interest rate", "recession", "federal reserve", "the fed",
               "gdp", "economy", "economic", "debt", "currency", "central bank",
               "tariff", "trade war"]),
    ("news", ["عاجل", "اليوم", "الآن", "أخبار", "تحديث", "ماذا حدث", "هذا الأسبوع",
              "breaking", "today", "this week", "update", "just happened", "right now",
              "latest"]),
    ("education", ["تعلم", "شرح", "كيف", "ما هو", "ماهو", "للمبتدئين", "دليل", "أساسيات",
                   "الفرق بين", "خطوات", "طريقة",
                   "how to", "what is", "explained", "beginner", "guide", "basics",
                   "tutorial", "learn", "understanding", "why"]),
]

ASSET_WORDS = ["بيتكوين", "الذهب", "الدولار", "الأسهم", "النفط", "اليورو", "الفضة",
               "bitcoin", "gold", "dollar", "oil", "silver", "euro", "nasdaq", "s&p",
               "tesla", "nvidia", "apple"]

AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
QUESTION_WORDS = ["كيف", "لماذا", "ماذا", "هل", "ما هو", "متى", "أين", "؟",
                  "how", "why", "what", "when", "should", "is it", "can you", "?"]


def classify_subject(title: str, description: str = "") -> str:
    hay = f"{title} {description[:300]}".lower()
    for subject, words in SUBJECT_RULES:
        for w in words:
            if w.lower() in hay:
                return subject
    return "other"


def has_number(title: str) -> bool:
    return bool(re.search(r"\d", title)) or any(d in title for d in AR_DIGITS)


def has_year(title: str) -> bool:
    return bool(re.search(r"\b20[2-3]\d\b", title))


def has_question(title: str) -> bool:
    t = title.lower()
    return any(q in t for q in QUESTION_WORDS)


def has_asset(title: str) -> bool:
    t = title.lower()
    return any(a.lower() in t for a in ASSET_WORDS)


def run() -> None:
    con = connect()
    rows = con.execute("SELECT video_id,title,description FROM videos").fetchall()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for r in rows:
        t = r["title"] or ""
        out.append((r["video_id"], classify_subject(t, r["description"] or ""),
                    "keyword_rule_v1",
                    int(has_number(t)), int(has_year(t)), int(has_question(t)),
                    int(has_asset(t)), len(t.split()), len(t), now))
    con.executemany("""INSERT OR REPLACE INTO video_coding
        (video_id,subject,subject_method,title_has_number,title_has_year,
         title_has_question,title_has_asset,title_word_count,title_char_count,coded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", out)
    con.commit()
    dist = con.execute("SELECT subject,COUNT(*) c FROM video_coding "
                       "GROUP BY subject ORDER BY c DESC").fetchall()
    print(f"[classify] coded {len(out)} videos")
    for d in dist:
        print(f"   {d['subject']:12} {d['c']}")


if __name__ == "__main__":
    run()
