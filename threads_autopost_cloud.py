#!/usr/bin/env python3
"""Threads自動投稿（クラウド版・GitHub Actions用）

設計方針（2026-07-22改訂）:
GitHub Actionsのscheduleは1日数回しか動かないことがあるため、
「予約時刻ちょうどに実行されること」を前提にしない。
- 予約時刻を過ぎていて、かつ投稿可能な時間帯(7:00-22:00)なら投稿する
- 時間帯外なら pending のまま持ち越す（捨てない）
- 24時間以上過ぎたものだけ諦めて missed にする
- 1回の実行で投稿するのは1件だけ（溜まっていても連投しない）
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

USER_ID = "28532249756364073"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE_DIR, "queue.json")
API = "https://graph.threads.net/v1.0"

POST_WINDOW_START = 7   # この時刻以降なら投稿してよい
POST_WINDOW_END = 23    # この時刻未満なら投稿してよい（深夜投稿を防ぐ）
MAX_AGE_HOURS = 20      # 予約からこれ以上過ぎたら諦める（翌日の同枠にかぶせない）


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def api_call(url, params, method="POST"):
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
    else:
        req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def publish_post(token, text, reply_to_id=None):
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    container = api_call(f"{API}/{USER_ID}/threads", params)
    # コンテナ準備待ち。短いと公開が失敗するので長めに取り、失敗したらリトライ
    last_err = None
    for attempt in range(4):
        time.sleep(7)
        try:
            published = api_call(
                f"{API}/{USER_ID}/threads_publish",
                {"creation_id": container["id"], "access_token": token},
            )
            return published["id"]
        except Exception as e:
            last_err = e
            log(f"publish retry {attempt+1}/4: {e}")
    raise last_err


def already_posted_recently(token, first_text):
    """直近の自分の投稿に、これから出す本文の1ポスト目と一致するものがあれば True。
    二重投稿の最終防波堤（並行実行やリトライで同じ内容が2度出るのを防ぐ）。"""
    try:
        res = api_call(
            f"{API}/{USER_ID}/threads",
            {"fields": "text", "limit": "10", "access_token": token},
            method="GET",
        )
        head = first_text.strip()[:40]
        for post in res.get("data", []):
            if (post.get("text") or "").strip()[:40] == head:
                return True
    except Exception as e:
        log(f"idempotency check failed (continuing): {e}")
    return False


def main():
    token = os.environ.get("THREADS_TOKEN")
    if not token:
        log("ERROR: THREADS_TOKEN not set")
        sys.exit(1)

    with open(QUEUE_PATH) as f:
        queue = json.load(f)
    now = datetime.now()
    changed = False
    in_window = POST_WINDOW_START <= now.hour < POST_WINDOW_END

    for item in queue:
        if item.get("status") != "pending":
            continue
        sched = datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
        if sched > now:
            continue  # まだ時刻前
        if now - sched > timedelta(hours=MAX_AGE_HOURS):
            item["status"] = "missed"
            changed = True
            log(f"MISSED (over {MAX_AGE_HOURS}h): {item['id']}")
            continue
        if not in_window:
            log(f"HOLD (outside {POST_WINDOW_START}-{POST_WINDOW_END}h): {item['id']}")
            continue  # 時間帯外。pendingのまま次の実行に持ち越す
        # 二重投稿の最終防波堤: 既に同内容が投稿済みなら投稿せずpostedにする
        if already_posted_recently(token, item["posts"][0]):
            item["status"] = "posted"
            item["posted_at"] = now.strftime("%Y-%m-%d %H:%M")
            item["note_dup"] = "skipped: already on timeline"
            changed = True
            log(f"SKIP (already posted): {item['id']}")
            break
        try:
            posted_ids = []
            reply_to = None
            for i, text in enumerate(item["posts"]):
                media_id = publish_post(token, text, reply_to_id=reply_to)
                posted_ids.append(media_id)
                reply_to = media_id
                log(f"posted {item['id']} part {i+1}/{len(item['posts'])} -> {media_id}")
                if i < len(item["posts"]) - 1:
                    time.sleep(5)
            item["status"] = "posted"
            item["posted_ids"] = posted_ids
            item["posted_at"] = now.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            log(f"FAILED {item['id']}: {e}")
        changed = True
        break  # 1回の実行で1件のみ

    if changed:
        with open(QUEUE_PATH, "w") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        log("queue.json updated")


if __name__ == "__main__":
    main()
