#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SOOP 방송 달력 - 자동 사이트 빌더
=================================
GitHub Actions에서 주기적으로 실행되어 SOOP 방송(다시보기) 기록을 크롤링하고,
public/index.html 을 새로 만들어 GitHub Pages로 배포합니다.

- 표준 라이브러리만 사용 (설치 필요 없음)
- 바꾸고 싶으면 아래 STREAMER_ID 한 줄만 수정하세요.
"""

import os
import json
import time
import shutil
import datetime
import urllib.request
import urllib.error

# ================= 설정 (여기만 바꾸면 됨) =================
STREAMER_ID = "allblack1019"      # SOOP 방송국 주소 sooplive.com/station/<여기>
SINCE = "2026-01-01"              # 달력에 표시할 시작일(이 날짜 이후 방송만 달력에 표시)
PRED_SINCE = "2025-07-01"         # 확률 계산에 쓸 데이터 시작일(달력보다 길게, 약 12개월). 달력엔 안 보이고 예측에만 사용
EVAL_SINCE = "2026-07-05"         # 예측성공/실패 표시 & 적중률 계산 시작일(이 날부터의 과거 날만 판정)
VIDEO_FILE = "intro.mp4"          # 인트로 영상 (assets/ 폴더에 넣기)
BGM_FILE = "bgm.mp3"              # 배경음악 (assets/ 폴더에 넣기)
SLASH_TIME = 4.0                  # 영상에서 화면이 찢기는 시점(초)
REST_DAYS = [1, 5]                # 정기 휴방 요일 (일0 월1 화2 수3 목4 금5 토6). 월·금 휴방 → [1,5]
REST_PENALTY = 0.6               # (백테스트 튜닝) 정기 휴방 요일이면 예측 확률에 곱하는 값(0~1)
MAKEUP_BOOST = 1.5               # (백테스트 튜닝) 휴방일인데 전날 정규일에 방송을 안 했으면 '대타 방송' 확률 배수
# 확률 예측 가중치 [wdRecent(최근 같은요일), wdRate(전체 요일), recentRate(최근30일 빈도), durTr(직전날 전이)] — 백테스트로 튜닝
PRED_WEIGHTS = [0.24, 0.10, 0.50, 0.16]
PRED_ALPHA = 3                   # 베이지안 스무딩 강도: 표본 적은 통계일수록 전체평균 쪽으로 당김(0=끔)
# '과보상'(오늘만): 마지막 방송 이후 경과일이 길수록 오늘 뱅온 확률을 올림
OVERDUE_AFTER = 1                # 경과일이 이 값을 넘으면 상향 시작(1이면 이틀 이상 쉬었을 때부터)
OVERDUE_STEP  = 0.12             # 경과일 1일당 상향 폭(배수에 가산). 0이면 끔
OVERDUE_MAX   = 2.0              # 상향 배수 상한(너무 커지지 않게)
DAY_START_HOUR = 7               # 이 시각 이전(새벽)에 '시작'한 방송은 전날 방송으로 간주(확률 계산용). 달력 표시는 업로드일 그대로.
EVENING_START = 19               # 저녁 감쇠 시작 시각(24h). 오늘 미방송이면 이 시각부터 자정까지 확률이 시간마다 감소
EVENING_DECAY = 0.95             # 1시간 경과마다 곱하는 감쇠 배수(0~1). 낮출수록 더 빨리 떨어짐
# 목차(상단 메뉴)에 넣을 외부 링크. 이름:주소 형태. 원하면 주석 풀고 추가하세요.
LINKS = {
    "방송국(SOOP)": "https://www.sooplive.com/station/allblack1019",
    # "유튜브": "https://www.youtube.com/@여기",
    # "치지직": "https://chzzk.naver.com/여기",
    # "X(트위터)": "https://x.com/여기",
}
# =========================================================

PER_PAGE = 60
API_TMPL = ("https://chapi.sooplive.co.kr/api/{bid}/vods/review"
            "?orderby=reg_date&page={page}&per_page={per}")
VOD_URL_TMPL = "https://vod.sooplive.co.kr/player/{title_no}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Referer": "https://www.sooplive.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
OUT_DIR = "public"


def fetch_json(url, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("요청 실패 %s (%s)" % (url, last))


def crawl(bid):
    print("crawling review VODs for", bid)
    items, page = [], 1
    while True:
        data = fetch_json(API_TMPL.format(bid=bid, page=page, per=PER_PAGE))
        batch = data.get("data") or []
        items.extend(batch)
        links = data.get("links") or {}
        print("  page %d, +%d (total %d)" % (page, len(batch), len(items)))
        if not links.get("next") or not batch:
            break
        page += 1
        time.sleep(0.3)
    return items


def build(items, bid):
    out, nick, profile = [], bid, ""
    for it in items:
        ucc = it.get("ucc") or {}
        if ucc.get("file_type") != "REVIEW":
            continue
        if it.get("user_id") != bid:
            continue
        reg = it.get("reg_date")
        if not reg:
            continue
        try:
            end = datetime.datetime.strptime(reg, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        dur = ucc.get("total_file_duration") or 0
        start = end - datetime.timedelta(milliseconds=dur)
        day_str = start.strftime("%Y-%m-%d")   # 방송을 켠(시작) 시점 기준 (달력 표시용)
        # 확률 계산용 '방송일': 새벽(DAY_START_HOUR 이전)에 '시작'한 방송은 전날 방송으로 간주
        pdate = (start - datetime.timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")
        cutoff = PRED_SINCE or SINCE
        if cutoff and day_str < cutoff:
            continue          # 예측용 데이터 시작일 이전은 제외(달력 표시는 JS에서 SINCE로 다시 자름)
        tno = it.get("title_no")
        thumb = ucc.get("thumb") or ""
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        if it.get("user_nick"):
            nick = it["user_nick"]
        if it.get("profile_image"):
            pi = it["profile_image"]
            profile = ("https:" + pi) if pi.startswith("//") else pi
        cnt = it.get("count") or {}
        out.append({
            "date": day_str,
            "pdate": pdate,
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
            "duration_ms": dur,
            "title": (it.get("title_name") or "").strip(),
            "title_no": tno,
            "url": VOD_URL_TMPL.format(title_no=tno),
            "reads": cnt.get("read_cnt", 0),
            "thumb": thumb,
            "category": (ucc.get("category_tags") or [""])[0],
        })
    out.sort(key=lambda b: b["start"])
    return out, nick, profile


def _daily_predict(day, pset, dur, pf):
    """그 날 이전 데이터로 계산한 '그날의 예측 확률'(0~1). 저녁 감쇠 제외, 과보상 포함. (예측 로그 기록·재현 공용)"""
    import statistics
    prior = [pf + datetime.timedelta(days=i) for i in range((day - pf).days)]
    if len(prior) < 20:
        return None
    W = PRED_WEIGHTS; A = PRED_ALPHA; pen = REST_PENALTY; boost = MAKEUP_BOOST; REST = set(REST_DAYS)
    jsday = lambda x: (x.weekday() + 1) % 7
    def sm(o, n, pr):
        if A <= 0:
            return (o / n) if n > 0 else pr
        return (o + A * pr) / (n + A)
    onDays = sum(1 for t in prior if t.isoformat() in pset); base = onDays / len(prior)
    twd = jsday(day)
    wdOn = sum(1 for t in prior if jsday(t) == twd and t.isoformat() in pset)
    wdTot = sum(1 for t in prior if jsday(t) == twd)
    sw = [t for t in reversed(prior) if jsday(t) == twd][:8]
    wO = sum(1 for t in sw if t.isoformat() in pset); wT = len(sw)
    r30 = prior[-30:]; rO = sum(1 for t in r30 if t.isoformat() in pset); rT = len(r30)
    prev = day - datetime.timedelta(days=1); prevOn = prev.isoformat() in pset
    ondur = [dur[t.isoformat()] for t in prior if t.isoformat() in pset]
    med = statistics.median(ondur) if ondur else 0
    ln = ld = sn = sd = a0 = b0 = 0
    for i in range(len(prior) - 1):
        t = prior[i]; nxt = (t + datetime.timedelta(days=1)).isoformat() in pset
        if t.isoformat() in pset:
            if dur.get(t.isoformat(), 0) >= med: ld += 1; ln += 1 if nxt else 0
            else: sd += 1; sn += 1 if nxt else 0
        else: b0 += 1; a0 += 1 if nxt else 0
    dO, dN = ((ln, ld) if dur.get(prev.isoformat(), 0) >= med else (sn, sd)) if prevOn else (a0, b0)
    wr = sm(wdOn, wdTot, base); wrec = sm(wO, wT, wr); rr = sm(rO, rT, base); dt = sm(dO, dN, base)
    p = min(0.98, max(0.02, W[0]*wrec + W[1]*wr + W[2]*rr + W[3]*dt))
    rest = twd in REST; prevRest = jsday(prev) in REST; makeup = rest and (not prevRest) and (not prevOn)
    if rest: p = min(0.98, max(0.02, p * pen))
    if makeup: p = min(0.98, max(0.02, p * boost))
    gap = 1
    for t in reversed(prior):
        if t.isoformat() in pset:
            break
        gap += 1
    if gap > OVERDUE_AFTER:
        p = min(0.98, p * min(OVERDUE_MAX, 1 + OVERDUE_STEP * (gap - OVERDUE_AFTER)))
    return p


def compute_evals(broadcasts, eval_since, pred_since, log):
    """저장된 예측 로그(log)가 있으면 그 값으로, 없으면 재현(_daily_predict)으로 성공/실패 판정."""
    pset = set(); dateset = set(); dur = {}
    for b in broadcasts:
        pd = b["pdate"]; pset.add(pd); dur[pd] = dur.get(pd, 0) + (b.get("duration_ms") or 0)
        dateset.add(b["date"])
    if not pset:
        return {}, 0
    Dt = datetime.date.fromisoformat
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()  # KST
    pf = Dt(pred_since) if pred_since else Dt(min(pset))
    start = Dt(eval_since) if eval_since else pf
    evals = {}; ok = 0; tot = 0
    d = start
    while d < today:
        ds = d.isoformat()
        if ds in log:
            pred = bool(log[ds].get("pred"))     # 그날 실제로 기록한 예측(로그 우선)
        else:
            p = _daily_predict(d, pset, dur, pf)  # 로그 없는 과거 날은 재현
            if p is None:
                d += datetime.timedelta(days=1); continue
            pred = p >= 0.5
        actual = ds in dateset
        evals[ds] = (pred == actual)
        ok += 1 if evals[ds] else 0; tot += 1
        d += datetime.timedelta(days=1)
    return evals, (round(100 * ok / tot) if tot else 0)


def main():
    bid = os.environ.get("STREAMER_ID", STREAMER_ID)
    items = crawl(bid)
    broadcasts, nick, profile = build(items, bid)
    if not broadcasts:
        raise SystemExit("다시보기를 찾지 못했습니다. STREAMER_ID를 확인하세요: " + bid)

    # 예측 로그(그날 실제로 낸 예측을 저장) 로드 → 오늘 예측을 한 번만 기록(이후 고정) → 성공/실패 판정
    LOG_FILE = "predictions.json"
    try:
        pred_log = json.load(open(LOG_FILE, encoding="utf-8"))
    except Exception:
        pred_log = {}
    _pset = set(); _dur = {}
    for b in broadcasts:
        _pset.add(b["pdate"]); _dur[b["pdate"]] = _dur.get(b["pdate"], 0) + (b.get("duration_ms") or 0)
    _today = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()
    _pf = datetime.date.fromisoformat(PRED_SINCE) if PRED_SINCE else min(datetime.date.fromisoformat(x) for x in _pset)
    _p = _daily_predict(_today, _pset, _dur, _pf)
    _ts = _today.isoformat()
    if _p is not None and _ts not in pred_log:
        pred_log[_ts] = {"pred": bool(_p >= 0.5), "p": round(_p * 100)}
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(pred_log, f, ensure_ascii=False, indent=0, sort_keys=True)
        print("예측 로그 기록:", _ts, pred_log[_ts])
    evals, eval_acc = compute_evals(broadcasts, EVAL_SINCE, PRED_SINCE, pred_log)
    print("예측 판정: %d일, 적중률 %d%% (로그 %d일)" % (len(evals), eval_acc, len(pred_log)))

    payload = {
        "bid": bid, "nick": nick, "profile": profile,
        "since": SINCE, "predSince": PRED_SINCE,
        "evals": evals, "evalAcc": eval_acc,
        "generated": (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST"),
        "broadcasts": broadcasts,
    }
    os.makedirs(OUT_DIR, exist_ok=True)

    def fill(tpl):
        h = tpl
        h = h.replace("__VIDEO__", "assets/" + VIDEO_FILE)
        h = h.replace("__BGM__", "assets/" + BGM_FILE)
        h = h.replace("__SLASH__", str(SLASH_TIME))
        h = h.replace("__RESTDAYS__", json.dumps(REST_DAYS))
        h = h.replace("__RESTPENALTY__", str(REST_PENALTY))
        h = h.replace("__MAKEUP__", str(MAKEUP_BOOST))
        h = h.replace("__EVESTART__", str(EVENING_START))
        h = h.replace("__EVEDECAY__", str(EVENING_DECAY))
        h = h.replace("__ALPHA__", str(PRED_ALPHA))
        h = h.replace("__W1__", str(PRED_WEIGHTS[0]))
        h = h.replace("__W2__", str(PRED_WEIGHTS[1]))
        h = h.replace("__W3__", str(PRED_WEIGHTS[2]))
        h = h.replace("__W4__", str(PRED_WEIGHTS[3]))
        h = h.replace("__ODAFTER__", str(OVERDUE_AFTER))
        h = h.replace("__ODSTEP__", str(OVERDUE_STEP))
        h = h.replace("__ODMAX__", str(OVERDUE_MAX))
        h = h.replace("__LINKS__", json.dumps(LINKS, ensure_ascii=False))
        h = h.replace("__NICK__", nick or bid)
        h = h.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
        return h

    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(fill(HTML_TEMPLATE))

    # assets 폴더(영상/음악)를 public/assets 로 복사
    if os.path.isdir("assets"):
        dst = os.path.join(OUT_DIR, "assets")
        os.makedirs(dst, exist_ok=True)
        for fn in os.listdir("assets"):
            s = os.path.join("assets", fn)
            if os.path.isfile(s):
                shutil.copy(s, os.path.join(dst, fn))
        print("assets 복사 완료:", os.listdir("assets"))
    else:
        print("주의: assets/ 폴더가 없어 인트로 영상/음악이 포함되지 않습니다.")

    days = len(set(b["date"] for b in broadcasts))
    print("OK: %d broadcasts / %d days -> %s/index.html" %
          (len(broadcasts), days, OUT_DIR))


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOOP 방송 달력</title>
<style>
  :root{
    --bg:#0f1116; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
    --text:#e7e9ee; --muted:#8b92a3; --accent:#3b82f6;
    --on:#22c55e; --on-soft:#14351f; --today:#f59e0b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;
       -webkit-font-smoothing:antialiased}
  a{color:inherit}
  .wrap{max-width:960px;margin:0 auto;padding:24px 16px 60px}
  header.top{display:flex;align-items:center;gap:16px;margin-bottom:20px}
  header.top img{width:64px;height:64px;border-radius:50%;object-fit:cover;
       background:var(--panel2);border:1px solid var(--line)}
  header.top .who h1{margin:0;font-size:22px}
  header.top .who h1 a{color:inherit;text-decoration:none}
  header.top .who h1 a:hover{text-decoration:underline;color:#fff}
  header.top .who .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .logo{position:fixed;top:14px;left:14px;height:144px;width:auto;z-index:30;
        filter:drop-shadow(0 3px 12px rgba(0,0,0,.5))}
  /* z-index 30 < 인트로(40): 인트로가 화면을 덮는 동안 좌상단 로고는 가려지고, 달력에 들어오면 나타남 */
  @media(max-width:1180px){.logo{position:static;height:96px;margin:0 0 14px}}
  @media(max-width:560px){.logo{height:64px}}
  /* ===== 목차(내비게이션) ===== */
  .nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
  .navtab{display:inline-block;background:var(--panel2);border:1px solid var(--line);color:var(--text);
    border-radius:10px;padding:9px 18px;cursor:pointer;font-size:15px;font-weight:700;
    font-family:inherit;text-decoration:none}
  .navtab:hover{border-color:var(--accent)}
  .navtab.active{background:linear-gradient(#f4e4a8,#d4af37);color:#111;border-color:#d4af37}
  .navacc{margin-left:auto;align-self:center;font-size:14px;font-weight:700;color:var(--text);
    background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:8px 14px;white-space:nowrap}
  @media(max-width:560px){.navacc{margin-left:0;font-size:13px;padding:7px 12px}}
  .navlink{display:inline-flex;align-items:center;gap:5px;background:var(--panel2);
    border:1px solid var(--line);color:var(--text);border-radius:10px;padding:9px 16px;
    font-size:14px;font-weight:600;text-decoration:none}
  .navlink:hover{border-color:var(--accent);color:#fff}
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:22px}
  @media(max-width:520px){.stats{grid-template-columns:repeat(2,1fr)}}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:12px 16px;display:flex;flex-direction:column;justify-content:center}
  .stat .n{font-size:22px;font-weight:700;color:#fff}
  .stat.high{background:rgba(134,239,172,.22);border-color:rgba(134,239,172,.5)}
  .stat.mid{background:rgba(253,224,138,.22);border-color:rgba(253,224,138,.5)}
  .stat.low{background:rgba(252,165,165,.22);border-color:rgba(252,165,165,.5)}
  .stat.high .l,.stat.mid .l,.stat.low .l{color:#eef1f6}
  .stat[title]{cursor:help}
  .stat .l{font-size:12px;color:var(--muted);margin-top:2px}
  .stat .l2{font-size:12px;color:#cbd2e0;margin-top:5px;font-weight:600}
  .calbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
  .calbar .title{font-size:18px;font-weight:700;min-width:150px;text-align:center}
  .navbtn{background:var(--panel2);border:1px solid var(--line);color:var(--text);
          border-radius:10px;padding:8px 14px;cursor:pointer;font-size:15px}
  .navbtn:hover{border-color:var(--accent)}
  .navbtn:disabled{opacity:.3;cursor:default}
  .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
  .dow{text-align:center;color:var(--muted);font-size:12px;padding:6px 0}
  .dow.sun{color:#f87171}.dow.sat{color:#60a5fa}
  .cell{aspect-ratio:1/1;background:var(--panel);border:1px solid var(--line);
        border-radius:10px;padding:6px;position:relative;overflow:hidden;
        display:flex;flex-direction:column}
  .cell.empty{background:transparent;border:none}
  .cell .d{font-size:13px;color:var(--muted)}
  .cell.on{background:var(--on-soft);border-color:#2f6b45;cursor:pointer}
  .cell.on:hover{border-color:var(--on)}
  .cell.on .d{color:#dff6e6;font-weight:700}
  .cell.off{background:rgba(252,165,165,.13);border-color:rgba(252,165,165,.3)}
  .cell.off .d{color:#f3c4c4}
  .cell.today{outline:2px solid var(--today);outline-offset:-2px}
  .cell .hrs{margin-top:auto;font-size:11px;color:#7fd3a0;font-weight:600;line-height:1.2}
  .cell .pred{font-size:9px;font-weight:800;line-height:1.1;margin-top:2px;padding:1px 4px;border-radius:5px;align-self:flex-start}
  .cell .pred.ok{color:#eafff2;background:#0b7a39;border:1px solid #17b352}
  .cell .pred.no{color:#ffecec;background:#9c1414;border:1px solid #e23c3c}
  @media(max-width:560px){.cell .pred{font-size:8px;padding:0 3px}}
  .cell .cnt{position:absolute;top:5px;right:7px;font-size:11px;color:var(--on);font-weight:700}
  .legend{margin:14px 2px;color:var(--muted);font-size:12px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  .legend .k{display:inline-flex;align-items:center;gap:6px}
  .legend .box{width:14px;height:14px;border-radius:4px;display:inline-block}
  .detail{margin-top:20px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:60px}
  .detail h3{margin:0 0 12px;font-size:16px}
  .detail .empty{color:var(--muted);font-size:14px}
  .vod{display:flex;gap:12px;padding:10px;border:1px solid var(--line);border-radius:10px;
       margin-bottom:10px;background:var(--panel2);text-decoration:none;transition:border-color .15s}
  .vod:hover{border-color:var(--accent)}
  .vod img{width:120px;height:68px;object-fit:cover;border-radius:8px;background:#000;flex:none}
  .vod .meta{min-width:0}
  .vod .t{font-weight:600;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;
       display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .vod .s{font-size:12px;color:var(--muted);line-height:1.6}
  .vod .s b{color:#cbd2e0;font-weight:600}
  footer{margin-top:30px;color:var(--muted);font-size:12px;text-align:center}
  @media(max-width:560px){.vod img{width:92px;height:52px}.stat{min-width:90px}}
  /* 은은한 등장 애니메이션 */
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
  @keyframes popIn{from{opacity:0;transform:scale(.94)}to{opacity:1;transform:scale(1)}}
  header.top{animation:fadeUp .5s ease both}
  .stats{animation:fadeUp .5s .08s ease both}
  .calbar{animation:fadeUp .5s .14s ease both}
  #dow{animation:fadeUp .5s .18s ease both}
  .legend,.detail{animation:fadeUp .5s .22s ease both}
  .cell{animation:popIn .32s ease both}
  .detail .vod{animation:fadeUp .3s ease both}
  @media(prefers-reduced-motion:reduce){.wrap *{animation:none !important}}
  /* ===== 인트로 영상 연출 ===== */
  #intro{position:fixed;inset:0;z-index:40;overflow:hidden;background:#000;cursor:pointer}
  #ivid{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:#000}
  .tear{position:absolute;inset:0;display:none;will-change:transform;
    transition:transform .9s cubic-bezier(.7,0,.2,1)}
  #tearA{clip-path:polygon(0 0,37% 0,44% 8%,36% 17%,48% 25%,41% 33%,53% 42%,45% 50%,57% 58%,49% 67%,61% 75%,54% 83%,66% 92%,63% 100%,0 100%)}
  #tearB{clip-path:polygon(100% 0,37% 0,44% 8%,36% 17%,48% 25%,41% 33%,53% 42%,45% 50%,57% 58%,49% 67%,61% 75%,54% 83%,66% 92%,63% 100%,100% 100%)}
  #intro.cut #tearA{transform:translate(-10px,-4px)}
  #intro.cut #tearB{transform:translate(10px,4px)}
  #intro.open #tearA{transform:translate(-120%,-24%)}
  #intro.open #tearB{transform:translate(120%,24%)}
  #iflash{position:absolute;inset:0;background:#fff;opacity:0;pointer-events:none;z-index:5}
  #iflash.go{animation:iflash .55s ease forwards}
  @keyframes iflash{0%{opacity:0}14%{opacity:.9}100%{opacity:0}}
  #ihint{position:absolute;left:0;right:0;bottom:8%;text-align:center;color:#fff;font-size:15px;
    z-index:6;text-shadow:0 2px 12px #000;animation:ipulse 1.4s ease-in-out infinite}
  @keyframes ipulse{0%,100%{opacity:.4}50%{opacity:1}}
  #enterBtn{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%) scale(.9);z-index:6;
    padding:15px 42px;font-size:18px;font-weight:700;letter-spacing:3px;color:#111;
    background:linear-gradient(#f4e4a8,#d4af37);border:none;border-radius:999px;cursor:pointer;
    opacity:0;pointer-events:none;box-shadow:0 10px 34px rgba(212,175,55,.55)}
  #enterBtn.show{opacity:1;pointer-events:auto;animation:enterIn .6s ease, floaty 2.6s ease-in-out .6s infinite}
  @keyframes enterIn{from{opacity:0;transform:translate(-50%,-50%) scale(.9)}
    to{opacity:1;transform:translate(-50%,-50%) scale(1)}}
  @keyframes floaty{0%,100%{transform:translate(-50%,-50%) translateY(0)}50%{transform:translate(-50%,-50%) translateY(-9px)}}
  #enterBtn:hover{filter:brightness(1.08)}
  /* 인트로 로고 (START 위쪽, 살랑살랑) */
  #introLogo{position:absolute;left:50%;top:15%;transform:translateX(-50%);
    width:min(62vw,360px);height:auto;z-index:6;opacity:0;pointer-events:none;
    filter:drop-shadow(0 4px 16px rgba(0,0,0,.5));transition:opacity .6s ease}
  #intro.cut #introLogo{opacity:1;animation:logoWobble 3s ease-in-out infinite}
  @keyframes logoWobble{
    0%,100%{transform:translateX(-50%) translateY(0) rotate(-2deg)}
    25%{transform:translateX(-50%) translateY(-7px) rotate(1.5deg)}
    50%{transform:translateX(-50%) translateY(3px) rotate(2deg)}
    75%{transform:translateX(-50%) translateY(-3px) rotate(-1deg)}
  }
  #intro.gone{display:none}
  /* 배경음악 컨트롤 (우측 상단) */
  #bgmWrap{position:fixed;top:14px;right:14px;z-index:80;display:flex;flex-direction:column;align-items:flex-end;gap:5px}
  #bgmCtl{display:flex;align-items:center;gap:9px;
    background:rgba(18,20,26,.72);backdrop-filter:blur(6px);border:1px solid #2a2f3a;
    border-radius:999px;padding:7px 12px}
  #bgmNote{font-size:10px;color:var(--muted);background:rgba(18,20,26,.55);
    padding:2px 8px;border-radius:8px;letter-spacing:.2px}
  #bgmToggle{width:30px;height:30px;border-radius:50%;border:none;cursor:pointer;font-size:14px;
    background:linear-gradient(#f4e4a8,#d4af37);color:#111;display:flex;align-items:center;justify-content:center}
  #bgmToggle.off{background:#333844;color:#9aa3b2}
  #bgmVol{width:82px;accent-color:#d4af37;cursor:pointer}
  @media(max-width:560px){#bgmVol{width:60px}}
</style>
</head>
<body>
<!-- 배경음악 + 컨트롤(우측 상단) -->
<audio id="bgm" loop preload="auto" src="__BGM__"></audio>
<div id="bgmWrap">
  <div id="bgmCtl">
    <button id="bgmToggle" class="off" title="음악 켜기/끄기">♪</button>
    <input id="bgmVol" type="range" min="0" max="100" value="9" title="볼륨">
  </div>
  <div id="bgmNote">AI로 생성(합성)된 음원입니다</div>
</div>

<!-- 인트로 영상 -->
<div id="intro">
  <video id="ivid" src="__VIDEO__" muted playsinline preload="auto"></video>
  <canvas id="tearA" class="tear"></canvas>
  <canvas id="tearB" class="tear"></canvas>
  <div id="iflash"></div>
  <img id="introLogo" src="assets/logo.png" alt="" onerror="this.style.display='none'">
  <div id="ihint">화면을 클릭하세요</div>
  <button id="enterBtn">START</button>
</div>

<div class="wrap">
  <img class="logo" src="assets/logo.png" alt="새까망이 올까 말까?" onerror="this.style.display='none'">
  <header class="top">
    <img id="pf" alt="">
    <div class="who">
      <h1 id="nick">스트리머</h1>
      <div class="sub" id="sub"></div>
    </div>
  </header>
  <nav class="nav" id="nav">
    <a class="navtab active" href="index.html">📅 방송 달력</a>
    <span class="navacc" id="navAcc"></span>
  </nav>
  <div class="stats" id="stats"></div>
  <div class="calbar">
    <button class="navbtn" id="prev">◀</button>
    <div class="title" id="calTitle"></div>
    <button class="navbtn" id="next">▶</button>
  </div>
  <div class="grid" id="dow"></div>
  <div class="grid" id="cal" style="margin-top:6px"></div>
  <div class="legend">
    <span class="k"><span class="box" style="background:#14351f;border:1px solid #2f6b45"></span> 방송한 날</span>
    <span class="k"><span class="box" style="background:rgba(252,165,165,.18);border:1px solid rgba(252,165,165,.4)"></span> 노쇼(미방송)</span>
    <span class="k"><span class="box" style="background:transparent;outline:2px solid #f59e0b"></span> 오늘</span>
    <span class="k">초록 날짜를 클릭 → 그날 방송 보기</span>
  </div>
  <div class="detail" id="detail">
    <div class="empty">달력에서 초록색으로 표시된 날짜를 눌러보세요.</div>
  </div>
  <footer id="foot"></footer>
</div>
<script>
const DATA = /*__DATA__*/null;
const byDate={};
const CAL_SINCE=DATA.since||"";   // 달력에는 SINCE 이후만 표시(예측용 이전 데이터는 제외)
for(const b of DATA.broadcasts){ if(CAL_SINCE && b.date<CAL_SINCE) continue; (byDate[b.date]=byDate[b.date]||[]).push(b); }
const allDates=Object.keys(byDate).sort();
const firstDate=allDates[0], lastDate=allDates[allDates.length-1];
function bToday(){const d=new Date();d.setHours(0,0,0,0);return d;}
function pad(n){return (n<10?"0":"")+n;}
function fmtDur(ms){const min=Math.round(ms/60000);const h=Math.floor(min/60),m=min%60;
  return h>0?(h+"시간 "+(m?m+"분":"")).trim():m+"분";}
// 특정 달(연,월)의 방송일 수 / 방송시간 / 연속 뱅송·노쇼 (활동 시작일~오늘 범위로 제한)
function monthStats(y,m){
  const bset=new Set(allDates);
  const fF=new Date(firstDate+"T00:00:00").getTime();
  const td=bToday();
  let s=new Date(y,m,1).getTime(); if(fF>s)s=fF;
  let e=new Date(y,m+1,0).getTime(); if(td.getTime()<e)e=td.getTime();
  const y2=(t)=>{const x=new Date(t);return x.getFullYear()+"-"+pad(x.getMonth()+1)+"-"+pad(x.getDate());};
  let days=0,maxOn=0,maxOff=0,rOn=0,rOff=0;
  for(let t=s;t<=e;t+=86400000){
    if(bset.has(y2(t))){days++;rOn++;rOff=0;if(rOn>maxOn)maxOn=rOn;}
    else{rOff++;rOn=0;if(rOff>maxOff)maxOff=rOff;}
  }
  let ms2=0;
  for(const b of DATA.broadcasts){const dt=new Date(b.date+"T00:00:00"); if(dt.getFullYear()===y&&dt.getMonth()===m) ms2+=(b.duration_ms||0);}
  return {days, hours:Math.round(ms2/3600000), maxOn, maxOff};
}
function updateMonthStats(){
  const s=monthStats(view.getFullYear(), view.getMonth());
  const set=(id,v)=>{const el=document.getElementById(id); if(el) el.textContent=v;};
  set('statDays',s.days); set('statHours',s.hours+"h");
  set('statOn',s.maxOn); set('statOff',s.maxOff);
}
(function stats(){
  // 예측 전용 방송일 집합: 새벽 시작분은 전날로 잡힌 pdate 기준 (달력/월간통계는 date 기준 유지)
  const bset=new Set(DATA.broadcasts.map(b=>b.pdate||b.date));
  const totalBroad=DATA.broadcasts.length, totalDays=allDates.length;
  const totalMs=DATA.broadcasts.reduce((s,b)=>s+(b.duration_ms||0),0);
  const totalHours=Math.round(totalMs/3600000);

  // ---- 내일 방송 예측 (과거 요일별 + 최근 빈도 기반 추정) ----
  const dayMs=86400000;
  const today=bToday();
  const first=new Date((DATA.predSince||firstDate)+"T00:00:00");   // 확률은 12개월(predSince)부터 계산
  const ymdL=(dt)=>dt.getFullYear()+"-"+pad(dt.getMonth()+1)+"-"+pad(dt.getDate());
  const wdTot=[0,0,0,0,0,0,0], wdOn=[0,0,0,0,0,0,0];
  for(let t=first.getTime(); t<=today.getTime(); t+=dayMs){
    const dt=new Date(t), wd=dt.getDay();
    wdTot[wd]++; if(bset.has(ymdL(dt))) wdOn[wd]++;
  }
  // 연속 패턴 통계 (방송한 다음날 / 안 한 다음날 방송률)
  let a1=0,b1=0,a0=0,b0=0;
  for(let t=first.getTime(); t<today.getTime(); t+=dayMs){
    const c=bset.has(ymdL(new Date(t))), n=bset.has(ymdL(new Date(t+dayMs)));
    if(c){b1++; if(n)a1++;} else {b0++; if(n)a0++;}
  }
  const pAfterOn=b1?a1/b1:0.5, pAfterOff=b0?a0/b0:0.5;
  // 방송 길이 조건부 통계
  const dayDur={};
  for(const bb of DATA.broadcasts){const k=bb.pdate||bb.date; dayDur[k]=(dayDur[k]||0)+(bb.duration_ms||0);}
  const ddv=Object.values(dayDur).sort((x,y)=>x-y);
  const medDayDur=ddv.length?ddv[Math.floor(ddv.length/2)]:0;
  let ln=0,ld=0,sn=0,sd=0;
  for(let t=first.getTime(); t<today.getTime(); t+=dayMs){
    const ds=ymdL(new Date(t)); if(!bset.has(ds)) continue;
    const n=bset.has(ymdL(new Date(t+dayMs)));
    if((dayDur[ds]||0)>=medDayDur){ld++; if(n)ln++;} else {sd++; if(n)sn++;}
  }
  const pLong=ld?ln/ld:pAfterOn, pShort=sd?sn/sd:pAfterOn;
  const REST_DAYS=__RESTDAYS__;
  const EVE_START=__EVESTART__, EVE_DECAY=__EVEDECAY__;
  const ALPHA=__ALPHA__, W1=__W1__, W2=__W2__, W3=__W3__, W4=__W4__;
  const OD_AFTER=__ODAFTER__, OD_STEP=__ODSTEP__, OD_MAX=__ODMAX__;
  const sm=(o,n,pr,a)=> a<=0 ? (n>0?o/n:pr) : (o+a*pr)/(n+a);   // 베이지안 스무딩
  const baseRate=(()=>{const o=wdOn.reduce((s,x)=>s+x,0),n=wdTot.reduce((s,x)=>s+x,0);return n?o/n:0.5;})();
  const wdN=['일','월','화','수','목','금','토'];

  // 특정 날짜(target)의 방송 확률 예측
  function predict(target, isToday){
    const twd=target.getDay();
    const prev=new Date(target.getTime()-dayMs);
    const wr=sm(wdOn[twd], wdTot[twd], baseRate, ALPHA);
    let rT=0,rO=0;
    for(let t=target.getTime()-dayMs; t>=first.getTime() && rT<30; t-=dayMs){ rT++; if(bset.has(ymdL(new Date(t)))) rO++; }
    const rr=sm(rO, rT, baseRate, ALPHA);
    let wT=0,wO=0;
    for(let t=target.getTime()-dayMs; t>=first.getTime() && wT<8; t-=dayMs){ const dt=new Date(t); if(dt.getDay()===twd){wT++; if(bset.has(ymdL(dt))) wO++;} }
    const wrec=sm(wO, wT, wr, ALPHA);
    const prevOn=bset.has(ymdL(prev));
    let durO,durN;
    if(prevOn){ if((dayDur[ymdL(prev)]||0)>=medDayDur){durO=ln;durN=ld;} else {durO=sn;durN=sd;} } else {durO=a0;durN=b0;}
    const durTr=sm(durO, durN, baseRate, ALPHA);
    let p=Math.round(100*(W1*wrec + W2*wr + W3*rr + W4*durTr));
    p=Math.max(2,Math.min(98,p));
    const rest=REST_DAYS.indexOf(twd)>=0;
    if(rest) p=Math.max(2, Math.round(p*__RESTPENALTY__));
    // 대타 방송: 휴방일인데 전날(정규 방송일)에 방송을 안 했으면 확률 ↑
    const prevRest=REST_DAYS.indexOf(prev.getDay())>=0;
    const makeup = rest && !prevRest && !prevOn;
    if(makeup) p=Math.min(98, Math.round(p*__MAKEUP__));
    // 저녁 감쇠: 오늘 아직 방송을 안 켰으면 19시~자정 사이 시간이 갈수록 확률 하락(실시간)
    if(isToday){
      // 과보상: 마지막 방송 이후 경과일이 길수록 오늘 확률 ↑
      let gap=1; for(let t=target.getTime()-dayMs; t>=first.getTime(); t-=dayMs){ if(bset.has(ymdL(new Date(t)))) break; gap++; }
      if(gap>OD_AFTER) p=Math.min(98, Math.round(p*Math.min(OD_MAX, 1+OD_STEP*(gap-OD_AFTER))));
      // 저녁 감쇠: 19시~자정 사이 시간이 갈수록 확률 하락
      const nowh=new Date(); const hf=nowh.getHours()+nowh.getMinutes()/60;
      if(hf>=EVE_START && hf<24){ const into=Math.min(hf-EVE_START, 24-EVE_START);
        p=Math.max(2, Math.round(p*Math.pow(EVE_DECAY, into))); } }
    const lv=p>=65?'high':p>=45?'mid':'low';
    const word=lv==='high'?'뱅온 가능성 높음':lv==='mid'?'지각할 가능성 높음':'노쇼할 가능성 높음';
    return {prob:p, lvl:lv, word, rest, makeup, wd:twd};
  }

  const tomorrow=new Date(today.getTime()+dayMs);
  const pTom=predict(tomorrow);
  const prob=pTom.prob, lvl=pTom.lvl, twd=pTom.wd;
  const predLabel = `내일(${wdN[twd]}) ${pTom.word}`+(pTom.rest?(pTom.makeup?' · 정기휴방(대타 가능)':' · 정기휴방'):'');
  // 오늘 카드: 이미 방송했으면 확정 표시, 아니면 확률 예측
  const todayHas=bset.has(ymdL(today));
  let todayCard;
  if(todayHas){
    todayCard=["뱅온 ✓","오늘 방송함","high","오늘 이미 방송을 켰어요",""];
  } else {
    const pT=predict(today, true);
    todayCard=[pT.prob+"%",`오늘(${wdN[pT.wd]}) ${pT.word}`+(pT.rest?' · 정기휴방':''),pT.lvl,"오늘 방송 확률 (참고용)",""];
  }

  // ---- 예상 시작 시각 (요일별 시작시간 중앙값, 새벽은 +24h 보정) ----
  const startMin=(b)=>{const p=b.start.slice(11).split(':');let h=+p[0],m=+p[1],v=h*60+m;if(h<12)v+=1440;return v;};
  const med=(arr)=>{if(!arr.length)return null;const s=arr.slice().sort((x,y)=>x-y);return s[Math.floor(s.length/2)];};
  const fmtHM=(mn)=>{if(mn==null)return '-';const v=((mn%1440)+1440)%1440;return pad(Math.floor(v/60))+':'+pad(v%60);};
  const overallMed=med(DATA.broadcasts.slice(-30).map(startMin));
  const wdStarts=[[],[],[],[],[],[],[]];
  for(const b of DATA.broadcasts){const w=new Date((b.pdate||b.date)+"T00:00:00").getDay(); wdStarts[w].push(startMin(b));}
  const predStartFor=(w)=>{const arr=wdStarts[w]; return fmtHM(arr.length>=3?med(arr):overallMed);};
  const durs=DATA.broadcasts.slice(-30).map(b=>b.duration_ms).sort((x,y)=>x-y);
  const mdur=durs.length?durs[Math.floor(durs.length/2)]:0;
  const dh=Math.floor(mdur/3600000),dm=Math.round((mdur%3600000)/60000);
  const durLabel=(dh?dh+'시간':'')+(dm?' '+dm+'분':'')||'-';
  const todayWd=today.getDay(), tomoWd=tomorrow.getDay();
  const startTodayCard=[predStartFor(todayWd),`오늘(${wdN[todayWd]}) 예상 시작`,"",wdN[todayWd]+"요일 시작시각 중앙값 · 평균 방송길이 "+durLabel,""];
  const startTomoCard=[predStartFor(tomoWd),`내일(${wdN[tomoWd]}) 예상 시작`,"",wdN[tomoWd]+"요일 시작시각 중앙값 · 평균 방송길이 "+durLabel,""];

  const iv=new Date(lastDate+"T00:00:00");
  const ms0=monthStats(iv.getFullYear(), iv.getMonth());
  const genItems=[
    [ms0.days,"방송한 날","","이 달에 방송한 날 수 (달을 넘기면 바뀝니다)","statDays"],
    [ms0.hours+"h","누적 방송시간","","이 달 방송 시간 합계 (달을 넘기면 바뀝니다)","statHours"],
    [ms0.maxOn,"연속 뱅송일","","이 달 최장 연속 방송일 (달을 넘기면 바뀝니다)","statOn"],
    [ms0.maxOff,"연속 노쇼일","","이 달 방송을 켜지 않은 최장 연속일 (달을 넘기면 바뀝니다)","statOff"],
  ];
  const statHTML=([n,l,c,t,id])=>`<div class="stat ${c||''}"${t?` title="${t}"`:''}><div class="n"${id?` id="${id}"`:''}>${n}</div><div class="l">${l}</div></div>`;
  // 오늘 카드: 확률 + 예상 시작
  let tBig,tLine,tLvl;
  if(todayHas){ tBig="뱅온 ✓"; tLine="오늘 방송함"; tLvl="high"; }
  else{ const pT=predict(today, true); tBig=pT.prob+"%"; tLine=`오늘(${wdN[pT.wd]}) ${pT.word}`+(pT.rest?(pT.makeup?' · 정기휴방(대타 가능)':' · 정기휴방'):''); tLvl=pT.lvl; }
  const cardToday=`<div class="stat ${tLvl}" title="오늘 방송 확률 + 예상 시작 (참고용)"><div class="n">${tBig}</div><div class="l">${tLine}</div><div class="l2">예상 시작 ${predStartFor(todayWd)}</div></div>`;
  // 내일 카드: 확률 + 예상 시작
  const cardTomo=`<div class="stat ${lvl}" title="내일 방송 확률 + 예상 시작 (참고용)"><div class="n">${prob}%</div><div class="l">${predLabel}</div><div class="l2">예상 시작 ${predStartFor(tomoWd)}</div></div>`;
  document.getElementById('stats').innerHTML= genItems.map(statHTML).join('') + cardToday + cardTomo;
})();
document.getElementById('nick').innerHTML=`<a href="https://www.sooplive.com/station/${DATA.bid}" target="_blank" rel="noopener" title="방송국 홈으로 이동">${escapeHtml(DATA.nick||DATA.bid)}</a>`;
document.getElementById('sub').innerHTML=`@${DATA.bid} · 방송 기록 ${firstDate} ~ ${lastDate}`;
const pf=document.getElementById('pf');
if(DATA.profile){pf.src=DATA.profile;pf.onerror=()=>pf.style.display='none';}else{pf.style.display='none';}
document.getElementById('foot').textContent=`데이터 갱신: ${DATA.generated} · 다시보기(VOD) 기준 · SOOP 비공식 API`;
(function(){const ea=document.getElementById('navAcc'); if(ea && DATA.evalAcc!=null) ea.innerHTML='🎯 예측 적중률 <b style="color:#f4e4a8">'+DATA.evalAcc+'%</b>';})();
const dows=['일','월','화','수','목','금','토'];
document.getElementById('dow').innerHTML=
  dows.map((d,i)=>`<div class="dow ${i===0?'sun':i===6?'sat':''}">${d}</div>`).join('');
let view=new Date(lastDate+"T00:00:00");view.setDate(1);
const minView=new Date(firstDate+"T00:00:00");minView.setDate(1);
const maxView=new Date();maxView.setDate(1);maxView.setHours(0,0,0,0);
function sameMonth(a,b){return a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth();}
// 마우스 오버 시 은은한 틱 소리 (파일 없이 Web Audio로 생성)
let uiAC=null;
function uiTick(){
  try{
    uiAC=uiAC||new (window.AudioContext||window.webkitAudioContext)();
    if(uiAC.state==='suspended') uiAC.resume();
    const t=uiAC.currentTime;
    const o=uiAC.createOscillator(), g=uiAC.createGain();
    o.type='sine'; o.frequency.setValueAtTime(1046,t); o.frequency.exponentialRampToValueAtTime(1568,t+0.05);
    g.gain.setValueAtTime(0.0001,t);
    g.gain.exponentialRampToValueAtTime(0.05,t+0.008);
    g.gain.exponentialRampToValueAtTime(0.0001,t+0.09);
    o.connect(g); g.connect(uiAC.destination); o.start(t); o.stop(t+0.1);
  }catch(e){}
}
// 노쇼 칸 호버 시 '삐빅' 소리
function uiBeep(){
  try{
    uiAC=uiAC||new (window.AudioContext||window.webkitAudioContext)();
    if(uiAC.state==='suspended') uiAC.resume();
    const t=uiAC.currentTime;
    [[0,1320],[0.09,1760]].forEach(([off,f])=>{
      const o=uiAC.createOscillator(), g=uiAC.createGain(); const s=t+off;
      o.type='square'; o.frequency.value=f;
      g.gain.setValueAtTime(0.0001,s);
      g.gain.exponentialRampToValueAtTime(0.045,s+0.005);
      g.gain.exponentialRampToValueAtTime(0.0001,s+0.06);
      o.connect(g); g.connect(uiAC.destination); o.start(s); o.stop(s+0.07);
    });
  }catch(e){}
}
function render(){
  const y=view.getFullYear(),m=view.getMonth();
  document.getElementById('calTitle').textContent=`${y}년 ${m+1}월`;
  document.getElementById('prev').disabled=sameMonth(view,minView);
  document.getElementById('next').disabled=sameMonth(view,maxView);
  const startDow=new Date(y,m,1).getDay();
  const daysInMonth=new Date(y,m+1,0).getDate();
  const bt=bToday();const todayStr=bt.getFullYear()+"-"+pad(bt.getMonth()+1)+"-"+pad(bt.getDate());
  let cells=[];
  for(let i=0;i<startDow;i++)cells.push(`<div class="cell empty"></div>`);
  for(let d=1;d<=daysInMonth;d++){
    const ds=`${y}-${pad(m+1)}-${pad(d)}`;const list=byDate[ds];const isT=ds===todayStr;
    const dly=`animation-delay:${Math.min((startDow+d-1)*12,320)}ms`;
    const pv=(DATA.evals && (ds in DATA.evals))?DATA.evals[ds]:null;
    const predH = pv===null ? '' : `<div class="pred ${pv?'ok':'no'}">${pv?'예측성공!':'예측실패..'}</div>`;
    if(list){
      const totMs=list.reduce((s,b)=>s+(b.duration_ms||0),0);
      const hlabel=totMs>=3600000?(totMs/3600000).toFixed(1).replace(/\.0$/,'')+'시간':Math.round(totMs/60000)+'분';
      const cnt=list.length>1?`<span class="cnt">${list.length}</span>`:'';
      cells.push(`<div class="cell on ${isT?'today':''}" style="${dly}" data-d="${ds}"><div class="d">${d}</div>${cnt}<div class="hrs">${hlabel}</div>${predH}</div>`);
    }else{
      const off = ds>=firstDate && ds<todayStr;   // 활동 시작 후 ~ 어제까지의 미방송일
      cells.push(`<div class="cell ${off?'off ':''}${isT?'today':''}" style="${dly}"><div class="d">${d}</div>${predH}</div>`);
    }
  }
  document.getElementById('cal').innerHTML=cells.join('');
  document.querySelectorAll('.cell.on').forEach(c=>{
    c.addEventListener('click',()=>showDetail(c.dataset.d));
    c.addEventListener('mouseenter',uiTick);
  });
  document.querySelectorAll('.cell.off').forEach(c=>c.addEventListener('mouseenter',uiBeep));
  updateMonthStats();
}
function escapeHtml(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function showDetail(ds){
  const list=(byDate[ds]||[]).slice().sort((a,b)=>a.start.localeCompare(b.start));
  const box=document.getElementById('detail');
  const dt=new Date(ds+"T00:00:00");let html=`<h3>${ds} (${dows[dt.getDay()]}) · 방송 ${list.length}회</h3>`;
  for(const b of list){
    const st=b.start.slice(11),et=b.end.slice(11);
    const thumb=b.thumb?`<img src="${b.thumb}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`:'';
    html+=`<a class="vod" href="${b.url}" target="_blank" rel="noopener">${thumb}
      <div class="meta"><div class="t">${escapeHtml(b.title||'(제목 없음)')}</div>
      <div class="s"><b>켠 시각</b> ${st} → ${et} · <b>길이</b> ${fmtDur(b.duration_ms)}<br>
      <b>조회</b> ${(b.reads||0).toLocaleString()}${b.category?` · ${escapeHtml(b.category)}`:''}</div></div></a>`;
  }
  box.innerHTML=html;box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
document.getElementById('prev').onclick=()=>{view.setMonth(view.getMonth()-1);render();};
document.getElementById('next').onclick=()=>{view.setMonth(view.getMonth()+1);render();};
render();
</script>
<script>
/* ===== 인트로 영상 + 찢김 + 배경음악 ===== */
(function(){
  const SLASH_T=__SLASH__;
  const intro=document.getElementById('intro');
  const vid=document.getElementById('ivid');
  const tearA=document.getElementById('tearA');
  const tearB=document.getElementById('tearB');
  const flash=document.getElementById('iflash');
  const hint=document.getElementById('ihint');
  const enterBtn=document.getElementById('enterBtn');
  const bgm=document.getElementById('bgm');
  const bgmToggle=document.getElementById('bgmToggle');
  const bgmVol=document.getElementById('bgmVol');
  let started=false, torn=false;

  // 이번 방문에서 이미 인트로를 봤으면(게임↔달력 이동 등) 건너뛰고 바로 달력 표시
  try{ if(sessionStorage.getItem('introDone')){ intro.classList.add('gone'); started=true; torn=true; } }catch(e){}

  // 배경음악 컨트롤
  bgm.volume=bgmVol.value/100;
  function playBgm(){ bgm.play().then(()=>bgmToggle.classList.remove('off')).catch(()=>{}); }
  bgmVol.addEventListener('input',()=>{ bgm.volume=bgmVol.value/100; });
  bgmToggle.addEventListener('click',(e)=>{
    e.stopPropagation();
    if(bgm.paused) playBgm(); else { bgm.pause(); bgmToggle.classList.add('off'); }
  });

  function drawCover(cv){
    const w=window.innerWidth,h=window.innerHeight;
    cv.width=w; cv.height=h;
    const ctx=cv.getContext('2d');
    const vw=vid.videoWidth||16, vh=vid.videoHeight||9;
    const s=Math.max(w/vw,h/vh), dw=vw*s, dh=vh*s;
    ctx.drawImage(vid,(w-dw)/2,(h-dh)/2,dw,dh);
  }
  function doTear(){
    if(torn) return; torn=true;
    drawCover(tearA); drawCover(tearB);
    tearA.style.display='block'; tearB.style.display='block';
    vid.pause(); vid.style.visibility='hidden';
    flash.classList.add('go');
    requestAnimationFrame(()=>intro.classList.add('cut'));
    setTimeout(()=>enterBtn.classList.add('show'),520);
  }
  function watch(){ if(torn) return; if(vid.currentTime>=SLASH_T){ doTear(); } else { requestAnimationFrame(watch); } }

  function start(){
    if(started) return; started=true;
    hint.style.display='none';
    try{ vid.currentTime=0; }catch(e){}
    vid.play().catch(()=>{});
    playBgm();
    requestAnimationFrame(watch);
    // 안전장치: 영상이 안 넘어가도 강제로 진행
    setTimeout(()=>{ if(!torn) doTear(); }, (SLASH_T+2)*1000);
  }
  intro.addEventListener('click',(e)=>{
    if(e.target===enterBtn) return;
    start();
  });
  enterBtn.addEventListener('click',(e)=>{
    e.stopPropagation();
    try{ sessionStorage.setItem('introDone','1'); }catch(e){}
    intro.classList.add('open');
    setTimeout(()=>intro.classList.add('gone'),950);
  });
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
# rev: 새벽 방송 확률예외(pdate) · 목차 방송 달력만(게임 제거)
