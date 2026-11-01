#!/usr/bin/env python3
"""鳥かど（目黒）Toreta予約カレンダー空席監視 → Slack通知

データ取得元（ブラウザの通信と main.bundle.js から特定）:
  1) GET https://yoyaku.toreta.in/web/v1/web_reservations/<店舗ID>/contents.json
       → restaurant.key（予約APIの公開キー）, restaurant_tel など
  2) GET https://yoyaku.toreta.in/web/v1/web_reservations/<key>/days.json?year=Y&month=M&seats=N
       → result.days[i] が i日目の予約枠リスト
          null = 受付期間外 / [] = 満席 / [ {start_at, start_time, end_time, label, ...}, ... ] = 空きあり
          ※ days[0] は前月末日分の重複なので無視する

標準ライブラリのみ。設定は環境変数で上書き可能。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"

RESTAURANT = os.environ.get("RESTAURANT", "megurotorikado")
SEATS = [int(s) for s in os.environ.get("SEATS", "2").split(",") if s.strip()]
MONTHS_AHEAD = int(os.environ.get("MONTHS_AHEAD", "3"))  # 今月を含めて何か月分見るか
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
TEST_NOTIFY = os.environ.get("TEST_NOTIFY", "").lower() in ("1", "true", "yes")

BASE = "https://yoyaku.toreta.in"
SITE_URL = f"{BASE}/{RESTAURANT}/#/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# ---------------------------------------------------------------- HTTP
def get_json(path, retries=3):
    url = BASE + path
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{BASE}/{RESTAURANT}/",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def post_slack(text):
    if not WEBHOOK:
        print("[slack] SLACK_WEBHOOK_URL 未設定のため送信スキップ\n" + text)
        return
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


# ---------------------------------------------------------------- 取得・解析
def month_list(today, n):
    y, m = today.year, today.month
    for _ in range(n):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def days_in_month(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def slot_time(slot):
    """枠の開始時刻を HH:MM で返す（start_at → start_time → label の順）。"""
    if slot.get("start_at"):
        return datetime.fromtimestamp(int(slot["start_at"]), JST).strftime("%H:%M")
    if slot.get("start_time") not in (None, ""):
        sec = int(slot["start_time"]) % 86400
        return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"
    return str(slot.get("label") or "時間不明")


def slot_range(slot):
    s = slot_time(slot)
    e = None
    if slot.get("end_at"):
        e = datetime.fromtimestamp(int(slot["end_at"]), JST).strftime("%H:%M")
    elif slot.get("end_time") not in (None, ""):
        sec = int(slot["end_time"]) % 86400
        e = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"
    return f"{s}〜{e}" if e else s


def fetch_available():
    """戻り値: (空き枠dict {key: info}, 店舗情報dict)"""
    contents = get_json(f"/web/v1/web_reservations/{RESTAURANT}/contents.json")
    rest = contents["restaurant"]
    key = rest["key"]
    info = {"name": rest.get("restaurant_name", RESTAURANT),
            "tel": rest.get("restaurant_tel", "")}

    found = {}
    today = datetime.now(JST).date()
    for y, m in month_list(today, MONTHS_AHEAD):
        for seats in SEATS:
            data = get_json(f"/web/v1/web_reservations/{key}/days.json"
                            f"?year={y}&month={m}&seats={seats}")
            days = data["result"]["days"]  # KeyError なら構造変化 → 失敗扱い
            if not isinstance(days, list):
                raise RuntimeError("days が配列ではありません（API仕様変更の可能性）")
            for d in range(1, days_in_month(y, m) + 1):
                slots = days[d] if d < len(days) else None
                if not slots:
                    continue
                ds = f"{y:04d}-{m:02d}-{d:02d}"
                for sl in slots:
                    t = slot_time(sl)
                    k = f"{ds}|{t}|{seats}"
                    found[k] = {"date": ds, "time": slot_range(sl), "seats": seats}
            time.sleep(1)
    return found, info


# ---------------------------------------------------------------- 状態
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 通知文
def fmt_tel(tel):
    t = "".join(c for c in tel if c.isdigit())
    if len(t) == 11:
        return f"{t[:3]}-{t[3:7]}-{t[7:]}"
    if len(t) == 10:
        return f"{t[:2]}-{t[2:6]}-{t[6:]}"
    return tel


def fmt_date(ds):
    d = date.fromisoformat(ds)
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def build_message(title, items, info):
    lines = [title]
    by_date = {}
    for it in sorted(items, key=lambda x: (x["date"], x["time"], x["seats"])):
        by_date.setdefault(it["date"], []).append(f'{it["time"]}（{it["seats"]}名）')
    for ds, ts in by_date.items():
        lines.append(f"• {fmt_date(ds)}  " + " / ".join(ts))
    lines += [
        f"☎ {fmt_tel(info.get('tel', ''))}",
        f"🔗 {SITE_URL}",
        f"🕒 検知: {datetime.now(JST):%Y-%m-%d %H:%M} JST",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def main():
    name_default = {"name": "鳥かど", "tel": "08041149899"}

    if TEST_NOTIFY:
        sample = [{"date": (datetime.now(JST).date() + timedelta(days=7)).isoformat(),
                   "time": "18:00〜20:00", "seats": SEATS[0]}]
        post_slack(build_message("🧪 テスト通知（実際の空きではありません）",
                                 sample, name_default))
        return 0

    prev = load_state()
    first_run = prev is None
    prev = prev or {"slots": {}, "failing": False, "info": name_default}

    try:
        current, info = fetch_available()
    except Exception as e:  # 取得・解析失敗
        print(f"ERROR: {e}", file=sys.stderr)
        if not prev.get("failing"):
            post_slack(f"⚠️ {prev.get('info', name_default)['name']} 空席チェック失敗\n"
                       f"{type(e).__name__}: {str(e)[:300]}\n"
                       f"🔗 {SITE_URL}\n🕒 {datetime.now(JST):%Y-%m-%d %H:%M} JST\n"
                       "（復旧するまで失敗通知は送りません）")
        prev["failing"] = True
        save_state(prev)
        return 0  # 失敗通知済みなのでジョブ自体は成功扱い（state をキャッシュ保存させる）

    if prev.get("failing"):
        post_slack(f"✅ {info['name']} 空席チェック復旧\n"
                   f"🕒 {datetime.now(JST):%Y-%m-%d %H:%M} JST")

    new_keys = sorted(set(current) - set(prev.get("slots", {})))
    print(f"available={len(current)} new={len(new_keys)} first_run={first_run}")

    if first_run:
        if current:
            post_slack(build_message(f"👀 {info['name']} 監視開始：現在の空き",
                                     list(current.values()), info))
        else:
            post_slack(f"👀 {info['name']} 空席監視を開始しました（現在空きなし / "
                       f"人数 {','.join(map(str, SEATS))}名 / {MONTHS_AHEAD}か月分）\n🔗 {SITE_URL}")
    elif new_keys:
        post_slack(build_message(f"🍗 {info['name']} に空きが出ました！",
                                 [current[k] for k in new_keys], info))

    save_state({"slots": current, "failing": False, "info": info,
                "checked_at": datetime.now(JST).isoformat(timespec="seconds")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
