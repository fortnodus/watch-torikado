# watch-torikado

[鳥かど（目黒）](https://yoyaku.toreta.in/megurotorikado/#/) の Toreta 予約カレンダーを 10 分ごとに確認し、新しく空きが出たら Slack に通知します。GitHub Actions で動き、Python 標準ライブラリのみ使用。

## 仕組み

画面ではなく、予約ページ自身が使っている JSON API を直接読みます（ログイン不要）。

1. `GET /web/v1/web_reservations/megurotorikado/contents.json`
   → 予約 API 用の公開キー `restaurant.key` と電話番号を取得（キーは毎回ここから取り直すので変わっても追従）
2. `GET /web/v1/web_reservations/<key>/days.json?year=Y&month=M&seats=N`
   → `result.days[日]` が空き枠の配列。`null` = 受付期間外、`[]` = 満席、要素あり = 空き（`days[0]` は前月末日の重複なので無視）

前回の空き枠は `state.json` に保存し、`actions/cache` で次回に引き継ぎます。前回になかった枠だけを通知します。

| 状況 | 通知 |
|---|---|
| 初回実行 | 「監視開始」（その時点の空き一覧 or 空きなし） |
| 新しい空き枠が出た | 日付(曜日)・時間帯・人数・電話番号・URL・検知時刻 |
| 取得失敗 | 1 回だけ「チェック失敗」。失敗が続く間は黙る |
| 失敗から回復 | 「復旧」 |

## セットアップ（ブラウザだけで完結）

1. GitHub で新規リポジトリ `watch-torikado` を作成（Public）。
2. **Add file → Upload files** で `watch.py` と `README.md` をアップロード。
3. `watch.yml` は **Add file → Create new file** で、ファイル名欄に `.github/workflows/watch.yml` と入力して中身を貼り付けて Commit。
   （ドラッグ&ドロップでは `.github` フォルダが落ちやすいため、この方法が確実です）
4. **Settings → Secrets and variables → Actions → New repository secret**
   名前 `SLACK_WEBHOOK_URL`、値に Slack Incoming Webhook の URL。
5. **Actions** タブ → `watch-torikado` → **Run workflow**
   - `test_notify` にチェック → Slack にテスト通知が届くか確認
   - チェックなしで実行 → 「監視開始」通知が届けば稼働開始

## 設定変更

`watch.yml` の `env` で変更できます。

- `SEATS`: 監視する人数。`"2"` / `"2,3,4"` など（店舗の受付は 2〜4 名）
- `MONTHS_AHEAD`: 今月から何か月分見るか（既定 3。現状の公開範囲は約 2 か月先まで）

## 注意

- GitHub の schedule は混雑時に数分〜十数分遅れることがあります。
- Public リポジトリでは、**60 日間コミットがないと schedule が自動停止**します。停止したら Actions 画面から再有効化するか、何かコミットしてください。
- キャッシュは「最新のものを復元 → 毎回新しいキーで保存」しています。古いキャッシュは GitHub が自動で掃除します（容量上限 10GB、state.json は数 KB）。
- 監視間隔 10 分・1 回あたり数リクエスト程度に抑えています。間隔を詰めすぎないでください。
