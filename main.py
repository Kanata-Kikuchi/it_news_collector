import os
import json
import time
from datetime import datetime, timezone

import feedparser
import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HN_RSS_URL = "https://news.ycombinator.com/rss"

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")


def require_env(name, value):
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value

# 起動時にチェック
NOTION_TOKEN = require_env("NOTION_TOKEN", NOTION_TOKEN)
NOTION_DATABASE_ID = require_env("NOTION_DATABASE_ID", NOTION_DATABASE_ID)
DEEPL_API_KEY = require_env("DEEPL_API_KEY", DEEPL_API_KEY)


def translate_en_to_ja(text):
    if not text:
        return ""

    # Free / Pro の自動判別（安全）
    if DEEPL_API_KEY.strip().endswith(":fx"):
        url = "https://api-free.deepl.com/v2/translate"
    else:
        url = "https://api.deepl.com/v2/translate"

    headers = {
        "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY.strip()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {
        "text": text,
        "source_lang": "EN",
        "target_lang": "JA",
    }

    r = requests.post(url, headers=headers, data=payload, timeout=30)

    if r.status_code >= 300:
        raise RuntimeError(f"DeepL error: {r.status_code}\n{r.text}")

    return r.json()["translations"][0]["text"]



# =========================
# Notion 共通
# =========================
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_query_by_url(url):
    payload = {
        "filter": {"property": "URL", "url": {"equals": url}}
    }

    r = requests.post(
        f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
        headers=notion_headers(),
        data=json.dumps(payload),
        timeout=30,
    )

    if r.status_code >= 300:
        raise RuntimeError(f"Notion query error: {r.status_code}\n{r.text}")

    return r.json().get("results", [])


def already_posted(url):
    return len(notion_query_by_url(url)) > 0


# =========================
# 日付処理
# =========================
def to_date_iso(entry):
    if getattr(entry, "published_parsed", None):
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt.date().isoformat()

    # published が無い場合（不確か）
    return datetime.now().date().isoformat()


# =========================
# Notion ページ作成
# =========================
def create_news_page(title, url, published_iso, source_text, summary_short, body_text):
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "Source": {"rich_text": [{"text": {"content": source_text}}]},
            "URL": {"url": url},
            "Published": {"date": {"start": published_iso}},
            "Summary": {"rich_text": [{"text": {"content": summary_short}}]},
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "翻訳"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": body_text}}]
                },
            },
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "原文リンク"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": url, "link": {"url": url}}}
                    ]
                },
            },
        ],
    }

    r = requests.post(
        f"{NOTION_API_BASE}/pages",
        headers=notion_headers(),
        data=json.dumps(payload),
        timeout=30,
    )

    if r.status_code >= 300:
        raise RuntimeError(f"Notion create page error: {r.status_code}\n{r.text}")


# =========================
# Hacker News RSS
# =========================
def fetch_hn_entries(limit=5):
    feed = feedparser.parse(HN_RSS_URL)

    if feed.bozo:
        raise RuntimeError(f"RSS parse error: {feed.bozo_exception}")

    return (feed.entries or [])[:limit]


# =========================
# main
# =========================
def main():
    entries = fetch_hn_entries(limit=5)

    posted = 0
    skipped = 0

    for e in entries:
        title_en = e.get("title", "").strip() or "Untitled"
        url = e.get("link", "").strip()
        if not url:
            continue

        if already_posted(url):
            skipped += 1
            continue

        published_iso = to_date_iso(e)

        # --- ここで翻訳 ---
        try:
            title_ja = translate_en_to_ja(title_en)
            print("DEEPL OK:", title_en, "->", title_ja)
        except Exception as ex:
            print("DEEPL FAIL:", ex)
            title_ja = title_en

        summary_short = title_ja[:120]

        body_text = (
            "【日本語訳（タイトル）】\n"
            f"{title_ja}\n\n"
            "【原文タイトル】\n"
            f"{title_en}"
        )

        create_news_page(
            title=title_ja,          # ← Notionタイトルは日本語
            url=url,
            published_iso=published_iso,
            source_text="Hacker News",
            summary_short=summary_short,
            body_text=body_text,
        )

        posted += 1
        time.sleep(0.4)

    print(f"OK: posted={posted}, skipped={skipped}")


if __name__ == "__main__":
    main()