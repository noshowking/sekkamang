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
SINCE = "2026-01-01"              # 이 날짜 이후 방송만 표시. 전체를 보려면 "" (빈 문자열)
VIDEO_FILE = "intro.mp4"          # 인트로 영상 (assets/ 폴더에 넣기)
BGM_FILE = "bgm.mp3"              # 배경음악 (assets/ 폴더에 넣기)
SLASH_TIME = 4.0                  # 영상에서 화면이 찢기는 시점(초)
REST_DAYS = [1, 5]                # 정기 휴방 요일 (일0 월1 화2 수3 목4 금5 토6). 월·금 휴방 → [1,5]
REST_PENALTY = 0.5               # 정기 휴방 요일이면 예측 확률에 곱하는 값(0~1). 낮출수록 노쇼쪽
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
        if SINCE and start.strftime("%Y-%m-%d") < SINCE:
            continue          # 지정한 날짜 이전 방송은 제외
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
            "date": start.strftime("%Y-%m-%d"),
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


def main():
    bid = os.environ.get("STREAMER_ID", STREAMER_ID)
    items = crawl(bid)
    broadcasts, nick, profile = build(items, bid)
    if not broadcasts:
        raise SystemExit("다시보기를 찾지 못했습니다. STREAMER_ID를 확인하세요: " + bid)

    payload = {
        "bid": bid, "nick": nick, "profile": profile,
        "generated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "broadcasts": broadcasts,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    html = HTML_TEMPLATE
    html = html.replace("__VIDEO__", "assets/" + VIDEO_FILE)
    html = html.replace("__BGM__", "assets/" + BGM_FILE)
    html = html.replace("__SLASH__", str(SLASH_TIME))
    html = html.replace("__RESTDAYS__", json.dumps(REST_DAYS))
    html = html.replace("__RESTPENALTY__", str(REST_PENALTY))
    html = html.replace("/*__DATA__*/null",
                        json.dumps(payload, ensure_ascii=False))
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

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
  header.top .who .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:22px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:12px 16px;min-width:110px;flex:1}
  .stat .n{font-size:22px;font-weight:700;color:#fff}
  .stat.high{background:rgba(134,239,172,.22);border-color:rgba(134,239,172,.5)}
  .stat.mid{background:rgba(253,224,138,.22);border-color:rgba(253,224,138,.5)}
  .stat.low{background:rgba(252,165,165,.22);border-color:rgba(252,165,165,.5)}
  .stat.high .l,.stat.mid .l,.stat.low .l{color:#eef1f6}
  .stat[title]{cursor:help}
  .stat .l{font-size:12px;color:var(--muted);margin-top:2px}
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
    <input id="bgmVol" type="range" min="0" max="100" value="22" title="볼륨">
  </div>
  <div id="bgmNote">AI로 생성(합성)된 음원입니다</div>
</div>

<!-- 인트로 영상 -->
<div id="intro">
  <video id="ivid" src="__VIDEO__" muted playsinline preload="auto"></video>
  <canvas id="tearA" class="tear"></canvas>
  <canvas id="tearB" class="tear"></canvas>
  <div id="iflash"></div>
  <div id="ihint">화면을 클릭하세요</div>
  <button id="enterBtn">START</button>
</div>

<div class="wrap">
  <header class="top">
    <img id="pf" alt="">
    <div class="who">
      <h1 id="nick">스트리머</h1>
      <div class="sub" id="sub"></div>
    </div>
  </header>
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
for(const b of DATA.broadcasts){(byDate[b.date]=byDate[b.date]||[]).push(b);}
const allDates=Object.keys(byDate).sort();
const firstDate=allDates[0], lastDate=allDates[allDates.length-1];
function pad(n){return (n<10?"0":"")+n;}
function fmtDur(ms){const min=Math.round(ms/60000);const h=Math.floor(min/60),m=min%60;
  return h>0?(h+"시간 "+(m?m+"분":"")).trim():m+"분";}
// 특정 달(연,월)의 방송일 수 / 방송시간 / 연속 뱅송·노쇼 (활동 시작일~오늘 범위로 제한)
function monthStats(y,m){
  const bset=new Set(allDates);
  const fF=new Date(firstDate+"T00:00:00").getTime();
  const td=new Date(); td.setHours(0,0,0,0);
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
  const bset=new Set(allDates);
  const totalBroad=DATA.broadcasts.length, totalDays=allDates.length;
  const totalMs=DATA.broadcasts.reduce((s,b)=>s+(b.duration_ms||0),0);
  const totalHours=Math.round(totalMs/3600000);

  // ---- 내일 방송 예측 (과거 요일별 + 최근 빈도 기반 추정) ----
  const dayMs=86400000;
  const today=new Date();today.setHours(0,0,0,0);
  const first=new Date(firstDate+"T00:00:00");
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
  for(const bb of DATA.broadcasts){dayDur[bb.date]=(dayDur[bb.date]||0)+(bb.duration_ms||0);}
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
  const wdN=['일','월','화','수','목','금','토'];

  // 특정 날짜(target)의 방송 확률 예측
  function predict(target){
    const twd=target.getDay();
    const prev=new Date(target.getTime()-dayMs);
    const wdRate = wdTot[twd] ? wdOn[twd]/wdTot[twd] : 0;
    let rT=0,rO=0;
    for(let t=target.getTime()-dayMs; t>=first.getTime() && rT<30; t-=dayMs){ rT++; if(bset.has(ymdL(new Date(t)))) rO++; }
    const recentRate = rT?rO/rT:0;
    let wT=0,wO=0;
    for(let t=target.getTime()-dayMs; t>=first.getTime() && wT<8; t-=dayMs){ const dt=new Date(t); if(dt.getDay()===twd){wT++; if(bset.has(ymdL(dt))) wO++;} }
    const wdRecent = wT?wO/wT:wdRate;
    const prevOn=bset.has(ymdL(prev));
    const durTr = prevOn ? (((dayDur[ymdL(prev)]||0)>=medDayDur)?pLong:pShort) : pAfterOff;
    let p=Math.round(100*(0.30*wdRecent + 0.20*wdRate + 0.16*recentRate + 0.34*durTr));
    p=Math.max(2,Math.min(98,p));
    const rest=REST_DAYS.indexOf(twd)>=0;
    if(rest) p=Math.max(2, Math.round(p*__RESTPENALTY__));
    const lv=p>=65?'high':p>=45?'mid':'low';
    const word=lv==='high'?'뱅온 가능성 높음':lv==='mid'?'지각할 가능성 높음':'노쇼할 가능성 높음';
    return {prob:p, lvl:lv, word, rest, wd:twd};
  }

  const tomorrow=new Date(today.getTime()+dayMs);
  const pTom=predict(tomorrow);
  const prob=pTom.prob, lvl=pTom.lvl, twd=pTom.wd;
  const predLabel = `내일(${wdN[twd]}) ${pTom.word}`+(pTom.rest?' · 정기휴방':'');
  // 오늘 카드: 이미 방송했으면 확정 표시, 아니면 확률 예측
  const todayHas=bset.has(ymdL(today));
  let todayCard;
  if(todayHas){
    todayCard=["뱅온 ✓","오늘 방송함","high","오늘 이미 방송을 켰어요",""];
  } else {
    const pT=predict(today);
    todayCard=[pT.prob+"%",`오늘(${wdN[pT.wd]}) ${pT.word}`+(pT.rest?' · 정기휴방':''),pT.lvl,"오늘 방송 확률 (참고용)",""];
  }

  // ---- 예상 시작 시각 (최근 방송 시작시간 중앙값, 새벽은 +24h 보정) ----
  const startMin=(b)=>{const p=b.start.slice(11).split(':');let h=+p[0],m=+p[1],v=h*60+m;if(h<12)v+=1440;return v;};
  const recentB=DATA.broadcasts.slice(-30);
  const mins=recentB.map(startMin).sort((x,y)=>x-y);
  let predStart='-';
  if(mins.length){const md=mins[Math.floor(mins.length/2)]%1440;predStart=pad(Math.floor(md/60))+':'+pad(md%60);}
  const durs=recentB.map(b=>b.duration_ms).sort((x,y)=>x-y);
  const mdur=durs.length?durs[Math.floor(durs.length/2)]:0;
  const dh=Math.floor(mdur/3600000),dm=Math.round((mdur%3600000)/60000);
  const durLabel=(dh?dh+'시간':'')+(dm?' '+dm+'분':'')||'-';

  const iv=new Date(lastDate+"T00:00:00");
  const ms0=monthStats(iv.getFullYear(), iv.getMonth());
  const items=[
    [ms0.days,"방송한 날","","이 달에 방송한 날 수 (달을 넘기면 바뀝니다)","statDays"],
    [ms0.hours+"h","누적 방송시간","","이 달 방송 시간 합계 (달을 넘기면 바뀝니다)","statHours"],
    [ms0.maxOn,"연속 뱅송일","","이 달 최장 연속 방송일 (달을 넘기면 바뀝니다)","statOn"],
    [ms0.maxOff,"연속 노쇼일","","이 달 방송을 켜지 않은 최장 연속일 (달을 넘기면 바뀝니다)","statOff"],
    todayCard,
    [prob+"%",predLabel,lvl,"요일별·최근 빈도·연속 패턴·방송 길이 + 정기휴방(월·금) 반영 추정치 (참고용)",""],
    [predStart,"예상 시작 시각","","보통 이 시각쯤 켜요 · 평균 방송길이 "+durLabel,""],
  ];
  document.getElementById('stats').innerHTML=
    items.map(([n,l,c,t,id])=>`<div class="stat ${c||''}"${t?` title="${t}"`:''}><div class="n"${id?` id="${id}"`:''}>${n}</div><div class="l">${l}</div></div>`).join('');
})();
document.getElementById('nick').textContent=DATA.nick||DATA.bid;
document.getElementById('sub').innerHTML=`@${DATA.bid} · 방송 기록 ${firstDate} ~ ${lastDate}`;
const pf=document.getElementById('pf');
if(DATA.profile){pf.src=DATA.profile;pf.onerror=()=>pf.style.display='none';}else{pf.style.display='none';}
document.getElementById('foot').textContent=`데이터 갱신: ${DATA.generated} · 다시보기(VOD) 기준 · SOOP 비공식 API`;
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
  const t=new Date();const todayStr=t.getFullYear()+"-"+pad(t.getMonth()+1)+"-"+pad(t.getDate());
  let cells=[];
  for(let i=0;i<startDow;i++)cells.push(`<div class="cell empty"></div>`);
  for(let d=1;d<=daysInMonth;d++){
    const ds=`${y}-${pad(m+1)}-${pad(d)}`;const list=byDate[ds];const isT=ds===todayStr;
    const dly=`animation-delay:${Math.min((startDow+d-1)*12,320)}ms`;
    if(list){
      const totMs=list.reduce((s,b)=>s+(b.duration_ms||0),0);
      const hlabel=totMs>=3600000?(totMs/3600000).toFixed(1).replace(/\.0$/,'')+'시간':Math.round(totMs/60000)+'분';
      const cnt=list.length>1?`<span class="cnt">${list.length}</span>`:'';
      cells.push(`<div class="cell on ${isT?'today':''}" style="${dly}" data-d="${ds}"><div class="d">${d}</div>${cnt}<div class="hrs">${hlabel}</div></div>`);
    }else{
      const off = ds>=firstDate && ds<todayStr;   // 활동 시작 후 ~ 어제까지의 미방송일
      cells.push(`<div class="cell ${off?'off ':''}${isT?'today':''}" style="${dly}"><div class="d">${d}</div></div>`);
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
