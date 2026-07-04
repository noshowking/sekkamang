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
import datetime
import urllib.request
import urllib.error

# ================= 설정 (여기만 바꾸면 됨) =================
STREAMER_ID = "allblack1019"      # SOOP 방송국 주소 sooplive.com/station/<여기>
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
    html = HTML_TEMPLATE.replace("/*__DATA__*/null",
                                 json.dumps(payload, ensure_ascii=False))
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
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
  .stat .n{font-size:22px;font-weight:700}
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
</style>
</head>
<body>
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
(function stats(){
  const totalBroad=DATA.broadcasts.length, totalDays=allDates.length;
  const totalMs=DATA.broadcasts.reduce((s,b)=>s+(b.duration_ms||0),0);
  const totalHours=Math.round(totalMs/3600000);
  let best=0,cur=0,prev=null;
  for(const d of allDates){const dt=new Date(d+"T00:00:00");
    if(prev&&(dt-prev)===86400000)cur++;else cur=1;best=Math.max(best,cur);prev=dt;}
  const today=new Date();today.setHours(0,0,0,0);
  const ago=new Date(today.getTime()-29*86400000);
  const recent=allDates.filter(d=>{const x=new Date(d+"T00:00:00");return x>=ago&&x<=today;}).length;
  const items=[[totalDays.toLocaleString(),"방송한 날"],[totalBroad.toLocaleString(),"총 방송 수"],
    [totalHours.toLocaleString()+"h","누적 방송시간"],[best,"최장 연속(일)"],[recent+"/30","최근 30일"]];
  document.getElementById('stats').innerHTML=
    items.map(([n,l])=>`<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
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
    if(list){
      const totMs=list.reduce((s,b)=>s+(b.duration_ms||0),0);
      const hlabel=totMs>=3600000?(totMs/3600000).toFixed(1).replace(/\.0$/,'')+'시간':Math.round(totMs/60000)+'분';
      const cnt=list.length>1?`<span class="cnt">${list.length}</span>`:'';
      cells.push(`<div class="cell on ${isT?'today':''}" data-d="${ds}"><div class="d">${d}</div>${cnt}<div class="hrs">${hlabel}</div></div>`);
    }else cells.push(`<div class="cell ${isT?'today':''}"><div class="d">${d}</div></div>`);
  }
  document.getElementById('cal').innerHTML=cells.join('');
  document.querySelectorAll('.cell.on').forEach(c=>c.addEventListener('click',()=>showDetail(c.dataset.d)));
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
</body>
</html>
"""

if __name__ == "__main__":
    main()
