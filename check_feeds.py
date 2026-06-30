#!/usr/bin/env python3
"""
feeds.txt に書かれたRSS/Atomフィードを定期的にチェックし、
新着記事だけをDiscordのウェブフックに通知するスクリプト。

状態管理:
  seen.json に「これまでに通知済みの記事ID」をフィードごとに保存する。
  GitHub Actions上では、実行後にこのファイルをリポジトリへコミットし直すことで
  次回実行時にも状態を引き継ぐ。
"""

import json
import os
import socket
import time
from pathlib import Path

import feedparser
import requests

socket.setdefaulttimeout(20)

FEEDS_FILE = Path("feeds.txt")
SEEN_FILE = Path("seen.json")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
USER_AGENT = "rss-discord-notifier/1.0 (personal use, periodic checker)"
EMBED_COLOR = 5793266  # Discordのblurple


def load_feed_urls():
    if not FEEDS_FILE.exists():
        print(f"warning: {FEEDS_FILE} が見つかりません")
        return []
    urls = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"warning: {SEEN_FILE} の読み込みに失敗。空の状態から始めます")
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def entry_id(entry):
    return entry.get("id") or entry.get("link") or entry.get("title") or ""


def send_to_discord(site_title, entry):
    if not WEBHOOK_URL:
        print("error: 環境変数 DISCORD_WEBHOOK_URL が設定されていません")
        return False

    title = (entry.get("title") or "(無題)")[:256]
    link = entry.get("link") or ""

    embed = {"title": title, "color": EMBED_COLOR}
    if link:
        embed["url"] = link
    if site_title:
        embed["author"] = {"name": site_title[:256]}
    published = entry.get("published") or entry.get("updated")
    if published:
        embed["footer"] = {"text": published[:2048]}

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code == 429:
            retry_after = float(resp.json().get("retry_after", 1))
            print(f"  discordのレート制限。{retry_after:.1f}秒待機")
            time.sleep(retry_after + 0.5)
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code >= 300:
            print(f"  discordへの送信に失敗 ({resp.status_code}): {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"  discordへの送信中にエラー: {e}")
        return False


def process_feed(url, seen):
    print(f"checking: {url}")
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  フィードの取得に失敗: {e}")
        return False

    if parsed.bozo and not parsed.entries:
        print(f"  フィードを読み取れませんでした: {parsed.get('bozo_exception')}")
        return False

    site_title = parsed.feed.get("title") or url
    is_first_run = url not in seen
    seen_ids = set(seen.get(url, []))
    changed = False

    for entry in parsed.entries:
        eid = entry_id(entry)
        if not eid or eid in seen_ids:
            continue

        seen_ids.add(eid)
        changed = True

        if is_first_run:
            continue  # 初回は通知せず「既読」として記録するだけ

        if send_to_discord(site_title, entry):
            print(f"  通知済み: {entry.get('title')}")
            time.sleep(1)  # discordのレート制限対策

    if changed:
        seen[url] = sorted(seen_ids)
        if is_first_run:
            print(f"  このフィードは初回登録のため、既存{len(seen_ids)}件を通知なしで記録しました")

    return changed


def main():
    feed_urls = load_feed_urls()
    if not feed_urls:
        print("feeds.txt にフィードURLがありません")
        return

    seen = load_seen()
    any_changed = False

    for url in feed_urls:
        try:
            if process_feed(url, seen):
                any_changed = True
        except Exception as e:
            print(f"  予期しないエラー、このフィードをスキップ: {e}")

    if any_changed:
        save_seen(seen)
        print("seen.json を更新しました")
    else:
        print("変化なし")


if __name__ == "__main__":
    main()
