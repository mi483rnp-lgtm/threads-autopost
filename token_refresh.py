#!/usr/bin/env python3
"""Threadsトークンの定期更新（ローカル実行）
- 7日ごとに長期トークンをリフレッシュ（60日期限を延長し続ける）
- 更新したトークンをGitHubのSecretにも反映（クラウド投稿用）
- launchdから1日1回起動される想定。Macが起きていればOK
"""
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, date

CONFIG_PATH = os.path.expanduser("~/.config/threads-autopost/config.json")
GH = os.path.expanduser("~/bin/gh")
REPO = "mi483rnp-lgtm/threads-autopost"
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [token] {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line)


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    last = date.fromisoformat(config.get("token_last_refreshed", "2000-01-01"))
    if (date.today() - last).days < 7:
        return
    url = "https://graph.threads.net/refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "th_refresh_token", "access_token": config["access_token"]}
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.loads(r.read().decode())
    if "access_token" not in res:
        log(f"refresh failed: {res}")
        return
    config["access_token"] = res["access_token"]
    config["token_last_refreshed"] = date.today().isoformat()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    log("token refreshed locally")
    p = subprocess.run(
        [GH, "secret", "set", "THREADS_TOKEN", "-R", REPO],
        input=config["access_token"].encode(),
        capture_output=True,
    )
    if p.returncode == 0:
        log("GitHub secret updated")
    else:
        log(f"GitHub secret update FAILED: {p.stderr.decode()[:200]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
