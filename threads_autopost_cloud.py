#!/usr/bin/env python3
"""Threads自動投稿（クラウド版・GitHub Actions用）
- トークンは環境変数 THREADS_TOKEN から読む
- queue.json の予約時刻が来た投稿を投稿し、結果をqueue.jsonに書き戻す
- タイムゾーンはワークフロー側で TZ=Asia/Tokyo を設定
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
GRACE_MINUTES = 45


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
    time.sleep(3)
    published = api_call(
        f"{API}/{USER_ID}/threads_publish",
        {"creation_id": container["id"], "access_token": token},
    )
    return published["id"]


def main():
    token = os.environ.get("THREADS_TOKEN")
    if not token:
        log("ERROR: THREADS_TOKEN not set")
        sys.exit(1)

    with open(QUEUE_PATH) as f:
        queue = json.load(f)
    now = datetime.now()
    changed = False

    for item in queue:
        if item.get("status") != "pending":
            continue
        sched = datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
        if sched > now:
            continue
        if now - sched > timedelta(minutes=GRACE_MINUTES):
            item["status"] = "missed"
            changed = True
            log(f"MISSED: {item['id']} scheduled {item['time']}")
            continue
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

    if changed:
        with open(QUEUE_PATH, "w") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        log("queue.json updated")


if __name__ == "__main__":
    main()
