# Threads自動投稿システム

Buffer不要の完全自動投稿。queue.json に予約を書いておくと、Macが起きていれば5分おきのチェックで自動投稿される。

## 仕組み

| 部品 | 場所 | 役割 |
|---|---|---|
| queue.json | このフォルダ | 投稿の予約リスト（これだけ編集すればいい） |
| threads_autopost.py | このフォルダ | 投稿スクリプト（Threads公式API使用） |
| log.txt | このフォルダ | 投稿結果のログ |
| 設定・トークン | ~/.config/threads-autopost/config.json | アクセストークン（60日期限・7日ごとに自動更新） |
| launchd | ~/Library/LaunchAgents/com.roco.threads-autopost.plist | 5分おきにスクリプトを起動 |

## queue.json の書き方

```json
{
  "id": "0717-am-series1",
  "time": "2026-07-17 07:00",
  "posts": [
    "1ポスト目の本文",
    "2ポスト目の本文（自動でツリーの返信になる）"
  ],
  "status": "pending"
}
```

- `time`: 24時間表記。この時刻を過ぎた最初のチェック（5分おき）で投稿される
- `posts`: 1要素なら単発投稿、複数ならツリー投稿
- `status`: `pending`（投稿待ち）→ 実行後に `posted` / `failed` / `missed` に自動で変わる
- 予約時刻から**45分以上遅れた場合は投稿せず missed** になる（Macがスリープしていた場合など、変な時間に投稿されるのを防ぐ）

## 注意事項

- **Macがスリープ中は投稿されない**。投稿時間帯（朝7時・昼12時台・夜21時台）はMacを起こしておくか、電源設定でスケジュール起動を設定する。クラウド実行（GitHub Actions）への移行も可能（必要になったら相談）
- いいね・フォロー・コメントの自動化はしない（スパム判定・凍結リスク）。自動化するのは投稿のみ
- 投稿内容は 04_ng-rules.md のルール（稼NG・金銭ワードとCTA分離等）を通したものだけをキューに入れる
- CTAリプの後付け戦術（伸びてから最終リプを追加）は手動で行う

## トラブル時

- 投稿されない → log.txt と launchd_error.log を確認
- 手動実行テスト: `python3 threads_autopost.py`
- 停止したいとき: `launchctl unload ~/Library/LaunchAgents/com.roco.threads-autopost.plist`
