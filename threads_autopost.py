#!/usr/bin/env python3
"""Threads自動投稿（ローカル版・Mac launchd用）

クラウド(GitHub Actions)は1日数回しか動かないことがあるため、
Macが起きているときはこちらが正確な時刻(5分以内)に投稿する二重化の役割。

安全設計:
- リポジトリ全体を書き換える操作（reset --hard 等）は絶対に行わない
- 同期するのは queue.json 1ファイルのみ
- 1回の実行で1件だけ投稿
- 投稿可能な時間帯(7:00-22:00)のみ投稿。時間外は持ち越し
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date

CONFIG_PATH = os.path.expanduser("~/.config/threads-autopost/config.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(BASE_DIR, "queue.json")
LOG_PATH = os.path.join(BASE_DIR, "log.txt")
GH = os.path.expanduser("~/bin/gh")
REPO = "mi483rnp-lgtm/threads-autopost"
API = "https://graph.threads.net/v1.0"

POST_WINDOW_START = 7
POST_WINDOW_END = 22
MAX_AGE_HOURS = 24


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [local] {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line)


def git(*args):
    return subprocess.run(
        ["git"] + list(args), cwd=BASE_DIR, capture_output=True, text=True, timeout=60
    )


def sync_queue_from_cloud():
    """queue.json だけをリモートの最新に合わせる（他のファイルには触れない）"""
    git("fetch", "-q", "origin", "main")
    git("checkout", "origin/main", "--", "queue.json")


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
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            log("token refreshed")
            p = subprocess.run(
                [GH, "secret", "set", "THREADS_TOKEN", "-R", REPO],
                input=config["access_token"].encode(),
                capture_output=True,
            )
            log("GitHub secret updated" if p.returncode == 0 else "secret update failed")
    except Exception as e:
        log(f"token refresh failed: {e}")
    return config


def publish_post(config, text, reply_to_id=None):
    params = {
        "media_type": "TEXT",
        "text": text,
        "access_token": config["access_token"],
    }
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    container = api_call(f"{API}/{config['user_id']}/threads", params)
    # コンテナ準備待ち。短いと公開が失敗するので長めに取り、失敗したらリトライ
    last_err = None
    for attempt in range(4):
        time.sleep(7)
        try:
            published = api_call(
                f"{API}/{config['user_id']}/threads_publish",
                {"creation_id": container["id"], "access_token": config["access_token"]},
            )
            return published["id"]
        except Exception as e:
            last_err = e
            log(f"publish retry {attempt+1}/4: {e}")
    raise last_err


def already_posted_recently(config, first_text):
    """既に同内容がタイムラインにあれば True（二重投稿の最終防波堤）"""
    try:
        res = api_call(
            f"{API}/{config['user_id']}/threads",
            {"fields": "text", "limit": "10", "access_token": config["access_token"]},
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
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    config = refresh_token_if_needed(config)

    sync_queue_from_cloud()

    with open(QUEUE_PATH) as f:
        queue = json.load(f)
    now = datetime.now()
    in_window = POST_WINDOW_START <= now.hour < POST_WINDOW_END
    changed = False

    for item in queue:
        if item.get("status") != "pending":
            continue
        sched = datetime.strptime(item["time"], "%Y-%m-%d %H:%M")
        if sched > now:
            continue
        if now - sched > timedelta(hours=MAX_AGE_HOURS):
            item["status"] = "missed"
            changed = True
            log(f"MISSED (over {MAX_AGE_HOURS}h): {item['id']}")
            continue
        if not in_window:
            continue
        # ツリー（複数ポスト）は自動で出さない（途中で切れる事故を防ぐ）。手動で出す運用。
        if len(item.get("posts", [])) > 1:
            continue
        if already_posted_recently(config, item["posts"][0]):
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
        break

    if changed:
        with open(QUEUE_PATH, "w") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        git("add", "queue.json")
        git("commit", "-q", "-m", "update queue status (local)")
        p = git("push", "-q")
        if p.returncode != 0:
            git("pull", "--rebase", "-q")
            p = git("push", "-q")
        log("synced to cloud" if p.returncode == 0 else f"push failed: {p.stderr[:120]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
