#!/usr/bin/env python3
"""Threads自動投稿スクリプト
- queue.json の予約時刻が来た投稿をThreads APIで自動投稿する
- ツリー投稿対応（posts配列の2つ目以降は前のポストへの返信として投稿）
- 長期トークンの自動リフレッシュ（7日ごと）
- launchdから5分おきに起動される想定
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date

CONFIG_PATH = os.path.expanduser("~/.config/threads-autopost/config.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE_DIR, "queue.json")
LOG_PATH = os.path.join(BASE_DIR, "log.txt")
API = "https://graph.threads.net/v1.0"
GRACE_MINUTES = 45  # 予約時刻からこれ以上遅れていたら投稿せず missed にする


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def api_call(url, params, method="POST"):
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
    else:
        req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def refresh_token_if_needed(config):
    last = date.fromisoformat(config.get("token_last_refreshed", "2000-01-01"))
    if (date.today() - last).days < 7:
        return config
    try:
        res = api_call(
            "https://graph.threads.net/refresh_access_token",
            {"grant_type": "th_refresh_token", "access_token": config["access_token"]},
            method="GET",
        )
        if "access_token" in res:
            config["access_token"] = res["access_token"]
            config["token_last_refreshed"] = date.today().isoformat()
            save_json(CONFIG_PATH, config)
            log("token refreshed OK")
    except Exception as e:
        log(f"token refresh FAILED: {e}")
    return config


def publish_post(config, text, reply_to_id=None):
    """コンテナ作成→公開。公開されたメディアIDを返す"""
    params = {
        "media_type": "TEXT",
        "text": text,
        "access_token": config["access_token"],
    }
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    container = api_call(f"{API}/{config['user_id']}/threads", params)
    creation_id = container["id"]
    time.sleep(3)
    published = api_call(
        f"{API}/{config['user_id']}/threads_publish",
        {"creation_id": creation_id, "access_token": config["access_token"]},
    )
    return published["id"]


def main():
    config = load_json(CONFIG_PATH)
    config = refresh_token_if_needed(config)

    if not os.path.exists(QUEUE_PATH):
        log("queue.json not found — nothing to do")
        return
    queue = load_json(QUEUE_PATH)
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
            log(f"MISSED (too late): {item['id']} scheduled {item['time']}")
            continue
        # 投稿実行
        try:
            posted_ids = []
            reply_to = None
            for i, text in enumerate(item["posts"]):
                media_id = publish_post(config, text, reply_to_id=reply_to)
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
        save_json(QUEUE_PATH, queue)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
