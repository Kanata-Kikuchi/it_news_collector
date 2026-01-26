from dotenv import load_dotenv
load_dotenv()

import os
import json
import time
from datetime import datetime, timezone

import feedparser
import requests


# =========================
# 環境変数
# =========================
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def require_env(name, value):
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


# 起動時にチェック（ここで落ちるのは「設定が無い」時だけ）
NOTION_TOKEN = require_env("NOTION_TOKEN", NOTION_TOKEN)
NOTION_DATABASE_ID = require_env("NOTION_DATABASE_ID", NOTION_DATABASE_ID)
DEEPL_API_KEY = require_env("DEEPL_API_KEY", DEEPL_API_KEY)


# =========================
# RSS ソース（各3件）
# InfoQ は不安定になりがちなので、代替として The Register を採用
# =========================
SOURCES = [
    # 開発者コミュニティ発：スタートアップ／OSS／技術トレンドの一次情報
    {"name": "Hacker News", "rss": "https://news.ycombinator.com/rss", "limit": 3},

    # 深掘り系テックメディア：OS・セキュリティ・インフラ・ハードウェアに強い
    {"name": "Ars Technica", "rss": "https://feeds.arstechnica.com/arstechnica/index", "limit": 3},

    # ITビジネス／スタートアップ動向：資金調達・企業ニュース寄り
    {"name": "TechCrunch", "rss": "https://techcrunch.com/feed/", "limit": 3},

    # 研究・AI・未来技術寄り：論文背景や長期的インパクトを解説
    {"name": "MIT Technology Review", "rss": "https://www.technologyreview.com/topnews.rss", "limit": 3},

    # 実務・インフラ・業界ゴシップ：運用／クラウド／企業ITの現実的な話題
    {"name": "The Register", "rss": "https://www.theregister.com/headlines.atom", "limit": 3},
]



# =========================
# DeepL 翻訳（見出しだけ）
# =========================
def translate_en_to_ja(text):
    if not text:
        return ""

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
# Notion
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


def to_date_iso(entry):
    if getattr(entry, "published_parsed", None):
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt.date().isoformat()

    # published が無い場合（不確か）
    return datetime.now().date().isoformat()


def create_news_page(title, url, published_iso, source_text, body_text):
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "Source": {"rich_text": [{"text": {"content": source_text}}]},
            "URL": {"url": url},
            "Published": {"date": {"start": published_iso}},
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
# RSS（止まらない）
# =========================
def fetch_entries(rss_url, limit=3):
    feed = feedparser.parse(rss_url)

    if feed.bozo:
        # RSSが壊れてても止めない
        print(f"[WARN] RSS parse failed: {rss_url} -> {feed.bozo_exception}")
        return []

    return (feed.entries or [])[:limit]


# =========================
# main（止まらない）
# =========================
def main():
    posted = 0
    skipped = 0

    for src in SOURCES:
        source_name = src["name"]
        rss_url = src["rss"]
        limit = src.get("limit", 3)

        print(f"\n=== Fetch: {source_name} ===\n{rss_url}")

        try:
            entries = fetch_entries(rss_url, limit=limit)
        except Exception as ex:
            # fetch_entries自体が想定外に落ちても続行
            print(f"[WARN] fetch failed: {source_name} -> {ex}")
            continue

        for e in entries:
            try:
                title_en = (e.get("title", "") or "").strip() or "Untitled"
                url = (e.get("link", "") or "").strip()
                if not url:
                    continue

                if already_posted(url):
                    skipped += 1
                    continue

                published_iso = to_date_iso(e)

                try:
                    title_ja = translate_en_to_ja(title_en)
                    print("DEEPL OK:", source_name, ":", title_en, "->", title_ja)
                except Exception as ex:
                    print("DEEPL FAIL:", source_name, ":", ex)
                    title_ja = title_en

                body_text = (
                    "【日本語訳（見出し）】\n"
                    f"{title_ja}\n\n"
                    "【原文見出し】\n"
                    f"{title_en}"
                )

                create_news_page(
                    title=title_ja,
                    url=url,
                    published_iso=published_iso,
                    source_text=source_name,
                    body_text=body_text,
                )

                posted += 1
                time.sleep(0.4)

            except Exception as ex:
                # 1記事単位で落ちても続行
                print(f"[WARN] item failed: {source_name} -> {ex}")
                continue

    print(f"\nOK: posted={posted}, skipped={skipped}")


if __name__ == "__main__":
    main()
