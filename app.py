#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Спортивная мафия — полноценный сервер для GitHub + Railway.
Без внешних Python-библиотек. Данные сохраняются в DATA_DIR (на Railway — Volume).
"""
import json, os, secrets, threading, html, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

PORT = int(os.environ.get('PORT', '8080'))
DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.environ.get('DATA_FILE', os.path.join(DATA_DIR, 'mafia_data.json'))
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')
os.makedirs(IMAGE_DIR, exist_ok=True)
LOCK = threading.RLock()
SESSIONS = set()

ROLES = {
    'mafia': ('🎭', 'Мафия', 'black'),
    'don': ('👑', 'Дон', 'black'),
    'sheriff': ('🎯', 'Шериф', 'red'),
    'civilian': ('○', 'Мирный', 'red'),
}
NOMINATIONS = [('mafia','Лучшая мафия'),('don','Лучший дон'),('sheriff','Лучший шериф'),('civilian','Лучший мирный')]
DEFAULT = {'pass':'mafia','players':[],'rounds':[],'results':[],'settings':{'title':'Спортивная мафия','subtitle':'Школьный клуб'},'special_winners':{}}


def esc(x): return html.escape(str(x), quote=True)
def fmt(n):
    try:
        n=float(n)
        return str(int(n)) if n.is_integer() else f'{n:.2f}'.rstrip('0').rstrip('.')
    except Exception: return '0'

def load_data():
    if not os.path.exists(DATA_FILE): return json.loads(json.dumps(DEFAULT))
    try:
        with open(DATA_FILE, encoding='utf-8') as f: d=json.load(f)
        for k,v in DEFAULT.items(): d.setdefault(k, json.loads(json.dumps(v)))
        return d
    except Exception:
        return json.loads(json.dumps(DEFAULT))

def save_data(d):
    tmp=DATA_FILE+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(d,f,ensure_ascii=False,indent=2)
    os.replace(tmp,DATA_FILE)

def foul_penalty(f):
    if f <= 2: return 0
    if f == 3: return -0.5
    return -1

def entry_points(r):
    team=ROLES[r['role']][2]
    base=4 if team=='black' and r['outcome']=='win' else 3 if team=='red' and r['outcome']=='win' else 1
    return base+foul_penalty(int(r.get('fouls',0) or 0))+float(r.get('extra',0) or 0)

def player_results(data,p): return [r for r in data['results'] if r.get('player')==p]
def role_results(data,p,role): return [r for r in data['results'] if r.get('player')==p and r.get('role')==role]

def role_rating(entries):
    if not entries:return None
    n=len(entries); pts=sum(entry_points(e) for e in entries)/n; extra=sum(float(e.get('extra',0) or 0) for e in entries)/n; fouls=sum(int(e.get('fouls',0) or 0) for e in entries)/n; wins=sum(e['outcome']=='win' for e in entries)
    return {'rating':pts+.5*extra+.1*n-.2*fouls,'games':n,'wins':wins,'winrate':round(100*wins/n),'avg_extra':extra,'avg_fouls':fouls,'avg_pts':pts}

def nominations(data):
    out={}
    for role,title in NOMINATIONS:
        best=None; bs=None
        for p in data['players']:
            st=role_rating(role_results(data,p,role))
            if st and (bs is None or st['rating']>bs['rating']): best,bs=p,st
        out[role]=(title,best,bs)
    return out

def special_nominations(data):
    players=data['players']
    def top(metric,minv=1):
        vals={p:metric(p) for p in players}; c=[p for p in players if vals[p]>=minv]
        if not c:return None,0
        m=max(vals[p] for p in c); c.sort(key=lambda p:(-vals[p],p.lower())); return c[0],m
    activist,av=top(lambda p:len(player_results(data,p)))
    champion,wv=top(lambda p:sum(r['outcome']=='win' for r in player_results(data,p)))
    leader,xv=top(lambda p:sum(float(r.get('extra',0) or 0) for r in player_results(data,p)),.5)
    return [('🚀','Активист',activist,f'{av} игр' if activist else ''),('🏆','Чемпион',champion,f'{wv} побед' if champion else ''),('◆','Лидер',leader,f'{fmt(xv)} доп. баллов' if leader else ''),('◈','Тактик турнира',None,'')]

def tactical_score(data,p):
    rs=player_results(data,p); n=len(rs)
    if n<4:return None
    wins=sum(r['outcome']=='win' for r in rs); wr=wins/n
    extras=sum(float(r.get('extra',0) or 0) for r in rs)/n
    avpts=sum(entry_points(r) for r in rs)/n
    avgf=sum(int(r.get('fouls',0) or 0) for r in rs)/n
    candidates=[]
    for q in data['players']:
        qr=player_results(data,q)
        if len(qr)>=4: candidates.append((sum(float(r.get('extra',0) or 0) for r in qr)/len(qr),sum(entry_points(r) for r in qr)/len(qr)))
    maxextra=max((x[0] for x in candidates),default=1) or 1
    # 45% win rate, 25% extra/game, 20% avg points/game, 10% discipline.
    s=min(wr/0.45,1)*45 + min(extras/maxextra,1)*25 + min(avpts/4,1)*20 + max(0,min(1,1-avgf/4))*10
    return {'score':round(min(100,s),1),'games':n,'wins':wins,'winrate':round(wr*100),'avg_extra':extras,'avg_pts':avpts,'avg_fouls':avgf}

def tactical(data):
    arr=[]
    for p in data['players']:
        st=tactical_score(data,p)
        if st: arr.append((p,st))
    arr.sort(key=lambda x:(-x[1]['score'],-x[1]['wins'],-x[1]['games'],x[0].lower()))
    return arr

def totals(data):
    return {p:sum(entry_points(r) for r in player_results(data,p)) for p in data['players']}

def stats(data,p):
    rs=player_results(data,p); wins=sum(r['outcome']=='win' for r in rs); pts=sum(entry_points(r) for r in rs); fouls=sum(int(r.get('fouls',0) or 0) for r in rs); extra=sum(float(r.get('extra',0) or 0) for r in rs)
    roles={role:role_rating(role_results(data,p,role)) for role,_ in NOMINATIONS}
    return {'games':len(rs),'wins':wins,'losses':len(rs)-wins,'winrate':round(100*wins/len(rs)) if rs else 0,'points':pts,'avg_points':pts/len(rs) if rs else 0,'fouls':fouls,'extra':extra,'roles':roles}

def photo_path(p):
    # Player photo is the number/name stored in player object, e.g. 1 or 1.png.
    raw=p.get('photo','') if isinstance(p,dict) else ''
    if not raw:return None
    raw=os.path.basename(str(raw).strip())
    if '.' not in raw: raw=raw+'.png'
    ext=os.path.splitext(raw)[1].lower()
    if ext not in ('.png','.jpg','.jpeg','.webp'):return None
    full=os.path.join(IMAGE_DIR,raw)
    return '/images/'+quote(raw) if os.path.isfile(full) else None

def pname(p): return p.get('name','') if isinstance(p,dict) else p
def pphoto(p): return p.get('photo','') if isinstance(p,dict) else ''
def player_by_name(data,name):
    for p in data['players']:
        if pname(p)==name:return p
    return None

def normalize_players(data):
    # Backward compatibility with old string-only player list.
    data['players']=[{'name':p,'photo':''} if isinstance(p,str) else {'name':str(p.get('name','')).strip(),'photo':str(p.get('photo','')).strip()} for p in data.get('players',[]) if (p if isinstance(p,str) else p.get('name','')).strip()]

def nav(active=''):
    links=[('/', 'Обзор','home'),('/players','Игроки','players'),('/rating','Рейтинг','rating'),('/rounds','Игры','rounds'),('/nominations','Номинации','nominations'),('/tactics','Тактика','tactics'),('/admin','Ведущему','admin')]
    return ''.join(f'<a class="navlink {"active" if active==k else ""}" href="{u}">{t}</a>' for u,t,k in links)

CSS='''
:root{--bg:#f5f6f8;--surface:#fff;--ink:#121417;--muted:#707782;--line:#e4e7eb;--accent:#111827;--green:#16804b;--red:#b42318;--shadow:0 8px 30px rgba(15,23,42,.06)}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif}a{color:inherit;text-decoration:none}.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}.topin{max-width:1180px;margin:auto;min-height:68px;padding:0 20px;display:flex;align-items:center;gap:22px}.brand{font-weight:800;letter-spacing:-.03em;white-space:nowrap}.brand span{display:block;color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-top:2px}.nav{display:flex;gap:3px;overflow:auto}.navlink{padding:9px 11px;border-radius:8px;color:var(--muted);font-size:14px;white-space:nowrap}.navlink:hover,.navlink.active{background:#f0f2f5;color:var(--ink)}.container{max-width:1180px;margin:auto;padding:34px 20px 70px}.hero{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:26px}.eyebrow{font-size:12px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.12em}.hero h1{font-size:38px;line-height:1.05;margin:8px 0 8px;letter-spacing:-.045em}.hero p{margin:0;color:var(--muted)}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line);background:#fff;padding:10px 14px;border-radius:9px;font-weight:700;font-size:14px;cursor:pointer}.btn.primary{background:#111827;color:#fff;border-color:#111827}.btn.danger{color:#b42318}.btn.small{padding:7px 10px;font-size:12px}.grid{display:grid;gap:14px}.grid4{grid-template-columns:repeat(4,1fr)}.grid3{grid-template-columns:repeat(3,1fr)}.grid2{grid-template-columns:repeat(2,1fr)}.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:var(--shadow)}.stat{padding:18px}.stat .num{font-size:28px;font-weight:800;letter-spacing:-.04em}.stat .label{font-size:12px;color:var(--muted);margin-top:3px}.section{margin-top:26px}.sectionhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.section h2{font-size:17px;margin:0;letter-spacing:-.02em}.muted{color:var(--muted)}.tablewrap{overflow:auto}.table{width:100%;border-collapse:collapse;min-width:650px}.table th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding:11px 10px;border-bottom:1px solid var(--line)}.table td{padding:12px 10px;border-bottom:1px solid #eef0f2;font-size:14px}.table tr:last-child td{border-bottom:0}.rank{font-weight:800;width:42px}.points{font-weight:800}.tag{display:inline-flex;padding:4px 8px;border-radius:7px;font-size:12px;font-weight:700}.black{background:#f8e9ec;color:#9b1c31}.red{background:#eaf2ff;color:#2457a6}.win{color:var(--green);font-weight:700}.loss{color:var(--red)}.player{display:flex;align-items:center;gap:11px}.avatar{width:42px;height:42px;border-radius:50%;object-fit:cover;background:#eef0f2;display:grid;place-items:center;font-weight:800;color:#707782;flex:none}.avatar.big{width:92px;height:92px;font-size:28px}.playername{font-weight:750}.playername small{display:block;color:var(--muted);font-weight:500;font-size:11px;margin-top:2px}.nomgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.nom{border:1px solid var(--line);border-radius:12px;padding:17px}.nom .icon{font-size:22px}.nom .title{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin:8px 0}.nom .winner{font-size:18px;font-weight:800}.bar{height:7px;background:#eef0f2;border-radius:10px;overflow:hidden}.bar i{display:block;height:100%;background:#111827;border-radius:10px}.searchbar{display:flex;gap:8px;margin-bottom:12px}.input,.select{width:100%;border:1px solid var(--line);background:#fff;border-radius:9px;padding:11px 12px;font:inherit}.formgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.field label{display:block;font-size:12px;color:var(--muted);font-weight:700;margin-bottom:6px}.field.full{grid-column:1/-1}.toggle{display:flex;gap:7px;flex-wrap:wrap}.toggle label{cursor:pointer}.toggle input{display:none}.toggle span{display:block;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px}.toggle input:checked+span{background:#111827;color:#fff;border-color:#111827}.notice{padding:12px 14px;border-radius:9px;background:#f7f7f8;border:1px solid var(--line);color:var(--muted);font-size:13px}.formula{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#111827;color:#fff;padding:16px;border-radius:10px;overflow:auto}.profile{display:flex;align-items:center;gap:18px}.profile h1{margin:0 0 4px;font-size:30px}.metricgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:18px}.metric{border:1px solid var(--line);border-radius:10px;padding:13px}.metric b{font-size:20px}.metric span{display:block;font-size:11px;color:var(--muted);margin-top:2px}.round{display:flex;justify-content:space-between;align-items:center;gap:12px}.round strong{font-size:16px}.adminsection{margin-top:16px}.dangerbox{border-color:#f0c7c7}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{display:inline-flex;gap:8px;align-items:center;background:#f4f5f6;border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:12px}.chip a{color:#b42318;font-weight:800}.empty{text-align:center;padding:30px;color:var(--muted)}footer{max-width:1180px;margin:auto;padding:0 20px 40px;color:#9aa0a8;font-size:12px}@media(max-width:900px){.grid4,.nomgrid{grid-template-columns:repeat(2,1fr)}.grid3{grid-template-columns:1fr}.metricgrid{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.topin{padding:0 12px;gap:10px;min-height:60px}.brand{font-size:14px}.navlink{padding:8px 8px;font-size:12px}.container{padding:24px 12px 50px}.hero{display:block}.hero h1{font-size:30px}.actions{margin-top:16px}.grid4,.grid2,.nomgrid,.formgrid{grid-template-columns:1fr}.metricgrid{grid-template-columns:repeat(2,1fr)}.card{padding:15px}.profile h1{font-size:25px}}
'''


def layout(title,body,active=''):
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#111827"><title>{esc(title)}</title><style>{CSS}</style></head><body><header class="top"><div class="topin"><a class="brand" href="/">СПОРТИВНАЯ МАФИЯ<span>школьный клуб</span></a><nav class="nav">{nav(active)}</nav></div></header><main class="container">{body}</main><footer>Турнир «Спортивная мафия» · система очков: 4/3 за победу, 1 за поражение · 0–2 фола без штрафа, 3 = −0.5, 4+ = −1.</footer></body></html>'''

def avatar(p,big=False):
    ph=photo_path(p); cls='avatar big' if big else 'avatar'; n=pname(p); initials=esc((''.join(x[0] for x in n.split()[:2]))[:2].upper() or '?')
    return f'<img class="{cls}" src="{ph}" alt="" onerror="this.style.display=\'none\'">' if ph else f'<div class="{cls}">{initials}</div>'

def player_link(p): return f'<a href="/player?name={quote(pname(p))}">{avatar(p)}<span class="playername">{esc(pname(p))}</span></a>'

def home(data):
    normalize_players(data); ts=totals(data); order=sorted(data['players'],key=lambda p:(-ts.get(pname(p),0),pname(p).lower())); played=len(data['results']); games=len(data['rounds']); wins=sum(r['outcome']=='win' for r in data['results']); top=order[0] if order else None
    rows=''.join(f'<tr><td class="rank">{i+1}</td><td>{player_link(p)}</td><td class="points">{fmt(ts[pname(p)])}</td><td>{stats(data,pname(p))["games"]}</td><td>{stats(data,pname(p))["winrate"]}%</td></tr>' for i,p in enumerate(order[:10])) or '<tr><td colspan="5" class="empty">Добавьте игроков и результаты в панели ведущего.</td></tr>'
    noms=''
    for role,(title,best,st) in nominations(data).items():
        icon=ROLES[role][0]
        noms+=f'<div class="nom"><div class="icon">{icon}</div><div class="title">{esc(title)}</div><div class="winner">{esc(best) if best else "—"}</div><div class="muted" style="font-size:12px;margin-top:5px">{("Рейтинг "+fmt(st["rating"])) if st else "Пока нет данных"}</div></div>'
    body=f'''<section class="hero"><div><div class="eyebrow">{esc(data["settings"].get("subtitle","Школьный клуб"))}</div><h1>{esc(data["settings"].get("title","Спортивная мафия"))}</h1><p>Турнирная таблица, статистика игроков и номинации — в одном месте.</p></div><div class="actions"><a class="btn primary" href="/players">Все игроки</a><a class="btn" href="/admin">Панель ведущего</a></div></section>
    <div class="grid grid4"><div class="card stat"><div class="num">{len(data['players'])}</div><div class="label">игроков</div></div><div class="card stat"><div class="num">{games}</div><div class="label">игр</div></div><div class="card stat"><div class="num">{played}</div><div class="label">результатов</div></div><div class="card stat"><div class="num">{wins}</div><div class="label">побед записано</div></div></div>
    <section class="section card"><div class="sectionhead"><h2>Турнирная таблица</h2><a class="btn small" href="/rating">Полный рейтинг →</a></div><div class="tablewrap"><table class="table"><thead><tr><th>#</th><th>Игрок</th><th>Очки</th><th>Игр</th><th>Win rate</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    <section class="section"><div class="sectionhead"><h2>Ролевые номинации</h2><a class="btn small" href="/nominations">Все номинации →</a></div><div class="nomgrid">{noms}</div></section>'''
    if top: body+=f'<section class="section card"><div class="sectionhead"><h2>Лидер сейчас</h2></div><div class="profile">{avatar(top,True)}<div><h1 style="font-size:24px">{esc(pname(top))}</h1><div class="muted">{fmt(ts[pname(top)])} очков · {stats(data,pname(top))["games"]} игр · {stats(data,pname(top))["winrate"]}% побед</div></div></div></section>'
    return layout(data['settings'].get('title','Спортивная мафия'),body,'home')

def players_page(data):
    normalize_players(data); ts=totals(data)
    cards=''.join(f'<a class="card" href="/player?name={quote(pname(p))}" style="display:block"><div class="player">{avatar(p,True)}<div><div class="playername" style="font-size:17px">{esc(pname(p))}</div><small>{stats(data,pname(p))["games"]} игр · {stats(data,pname(p))["winrate"]}% побед · <b>{fmt(ts[pname(p)])}</b> очков</small></div></div></a>' for p in data['players']) or '<div class="card empty">Игроков пока нет.</div>'
    body=f'''<section class="hero"><div><div class="eyebrow">Состав</div><h1>Игроки</h1><p>Профили, роли, результаты и личная статистика.</p></div><div class="actions"><a class="btn primary" href="/admin">+ Добавить через панель</a></div></section><div class="searchbar"><input id="q" class="input" placeholder="Поиск игрока…" oninput="filterPlayers()"></div><div id="players" class="grid grid3">{cards}</div><script>function filterPlayers(){{let q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('#players>a').forEach(x=>x.style.display=x.innerText.toLowerCase().includes(q)?'block':'none')}}</script>'''
    return layout('Игроки',body,'players')

def player_page(data,name):
    normalize_players(data); p=player_by_name(data,name)
    if not p:return layout('Игрок не найден','<div class="card empty">Игрок не найден. <a href="/players">Вернуться к игрокам →</a></div>','players')
    s=stats(data,name); rs=list(reversed(player_results(data,name))); rows=''.join(f'<tr><td>{esc(r.get("round_name",r.get("round_id","—")))}</td><td><span class="tag {ROLES[r["role"]][2]}">{ROLES[r["role"]][0]} {ROLES[r["role"]][1]}</span></td><td class="{"win" if r["outcome"]=="win" else "loss"}">{"Победа" if r["outcome"]=="win" else "Поражение"}</td><td>{r.get("fouls",0)}</td><td>+{fmt(r.get("extra",0))}</td><td class="points">{fmt(entry_points(r))}</td></tr>' for r in rs) or '<tr><td colspan="6" class="empty">Результатов пока нет.</td></tr>'
    roleblocks=''.join(f'<div class="card"><b>{ROLES[role][0]} {esc(title)}</b><div style="margin-top:12px">{("Рейтинг <strong>"+fmt(st["rating"])+"</strong> · "+str(st["games"])+" игр · "+str(st["winrate"])+"% побед") if st else "Нет игр в роли"}</div></div>' for role,title in NOMINATIONS for st in [s['roles'][role]])
    body=f'''<section class="hero"><div><a class="muted" href="/players">← Все игроки</a><div class="profile" style="margin-top:15px">{avatar(p,True)}<div><div class="eyebrow">Профиль игрока</div><h1>{esc(name)}</h1><p>{s['games']} игр · {s['winrate']}% побед</p></div></div></div></section><div class="metricgrid"><div class="metric"><b>{fmt(s['points'])}</b><span>очки</span></div><div class="metric"><b>{s['games']}</b><span>игр</span></div><div class="metric"><b>{s['wins']}</b><span>побед</span></div><div class="metric"><b>{fmt(s['avg_points'])}</b><span>среднее/игру</span></div><div class="metric"><b>{fmt(s['fouls'])}</b><span>фолов</span></div></div><section class="section"><div class="sectionhead"><h2>Статистика по ролям</h2></div><div class="grid grid2">{roleblocks}</div></section><section class="section card"><div class="sectionhead"><h2>История игр</h2></div><div class="tablewrap"><table class="table"><thead><tr><th>Игра</th><th>Роль</th><th>Исход</th><th>Фолы</th><th>Доп.</th><th>Очки</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''
    return layout(name,body,'players')

def rating_page(data):
    normalize_players(data); ts=totals(data); order=sorted(data['players'],key=lambda p:(-ts[pname(p)],-stats(data,pname(p))['winrate'],pname(p).lower()))
    rows=''.join(f'<tr><td class="rank">{i+1}</td><td>{player_link(p)}</td><td class="points">{fmt(ts[pname(p)])}</td><td>{stats(data,pname(p))["games"]}</td><td>{stats(data,pname(p))["wins"]}</td><td>{stats(data,pname(p))["winrate"]}%</td><td>{fmt(stats(data,pname(p))["extra"])}</td></tr>' for i,p in enumerate(order)) or '<tr><td colspan="7" class="empty">Нет данных.</td></tr>'
    body=f'''<section class="hero"><div><div class="eyebrow">Leaderboard</div><h1>Общий рейтинг</h1><p>Очки считаются по официальной системе турнира.</p></div></section><div class="notice">Победа чёрных/Дона = 4, победа красных = 3, поражение = 1. Фолы: 0–2 = 0, 3 = −0.5, 4+ = −1. Дополнительные баллы задаёт ведущий.</div><section class="section card"><div class="tablewrap"><table class="table"><thead><tr><th>#</th><th>Игрок</th><th>Очки</th><th>Игр</th><th>Побед</th><th>Win rate</th><th>Доп.</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''
    return layout('Рейтинг',body,'rating')

def rounds_page(data):
    cards=''.join(f'<div class="card round"><div><strong>{esc(r["name"])}</strong><div class="muted" style="font-size:12px;margin-top:4px">{sum(x.get("round_id")==r["id"] for x in data["results"])} результатов</div></div><a class="btn small" href="/round?id={quote(r["id"])}">Открыть</a></div>' for r in data['rounds']) or '<div class="card empty">Игры ещё не добавлены.</div>'
    body=f'<section class="hero"><div><div class="eyebrow">История</div><h1>Игры</h1><p>Все раунды турнира и результаты.</p></div></section><div class="grid grid2">{cards}</div>'
    return layout('Игры',body,'rounds')

def round_page(data,rid):
    r=next((x for x in data['rounds'] if x['id']==rid),None)
    if not r:return layout('Игра не найдена','<div class="card empty">Игра не найдена.</div>','rounds')
    rs=[x for x in data['results'] if x.get('round_id')==rid]
    rows=''.join(f'<tr><td>{player_link(player_by_name(data,x["player"]) or {"name":x["player"]})}</td><td><span class="tag {ROLES[x["role"]][2]}">{ROLES[x["role"]][0]} {ROLES[x["role"]][1]}</span></td><td class="{"win" if x["outcome"]=="win" else "loss"}">{"Победа" if x["outcome"]=="win" else "Поражение"}</td><td>{fmt(entry_points(x))}</td></tr>' for x in rs)
    body=f'<section class="hero"><div><a class="muted" href="/rounds">← Все игры</a><h1>{esc(r["name"])}</h1><p>{len(rs)} записей</p></div></section><section class="card"><div class="tablewrap"><table class="table"><thead><tr><th>Игрок</th><th>Роль</th><th>Исход</th><th>Очки</th></tr></thead><tbody>{rows or "<tr><td colspan=4 class=empty>Результатов нет.</td></tr>"}</tbody></table></div></section>'
    return layout(r['name'],body,'rounds')

def nominations_page(data):
    blocks=''.join(f'<div class="card"><div class="eyebrow">{ROLES[role][0]}</div><h2 style="margin:7px 0">{esc(title)}</h2><div style="font-size:21px;font-weight:800">{esc(best) if best else "Пока нет кандидата"}</div><div class="muted" style="margin-top:7px">{("Рейтинг "+fmt(st["rating"])+" · "+str(st["games"])+" игр · "+str(st["winrate"])+"% побед") if st else ""}</div></div>' for role,(title,best,st) in nominations(data).items())
    sp=special_nominations(data); tac=tactical(data); tactical_w=tac[0] if tac else None
    blocks+=''.join(f'<div class="card"><div class="eyebrow">{em}</div><h2 style="margin:7px 0">{esc(title)}</h2><div style="font-size:21px;font-weight:800">{esc(best) if best else "Пока нет кандидата"}</div><div class="muted" style="margin-top:7px">{esc(stat)}</div></div>' for em,title,best,stat in sp[:3])
    blocks+=f'<div class="card"><div class="eyebrow">◈</div><h2 style="margin:7px 0">Тактик турнира</h2><div style="font-size:21px;font-weight:800">{esc(tactical_w[0]) if tactical_w else "Пока нет кандидата"}</div><div class="muted" style="margin-top:7px">{fmt(tactical_w[1]["score"]) + "/100" if tactical_w else "Минимум 4 игры"}</div></div>'
    body=f'<section class="hero"><div><div class="eyebrow">Награды</div><h1>Номинации</h1><p>Автоматические ролевые и специальные номинации.</p></div></section><div class="grid grid4">{blocks}</div><section class="section card"><h2>Как считается ролевой рейтинг</h2><p class="muted">Средние очки + 0.5 × средние дополнительные баллы + 0.1 × количество игр − 0.2 × средние фолы. Доп. баллы и фолы учитываются только в выбранной роли.</p></section>'
    return layout('Номинации',body,'nominations')

def tactics_page(data):
    arr=tactical(data); rows=''.join(f'<tr><td class="rank">{i+1}</td><td>{player_link(player_by_name(data,p) or {"name":p})}</td><td class="points">{fmt(st["score"])}</td><td>{st["games"]}</td><td>{st["winrate"]}%</td><td>{fmt(st["avg_extra"])}</td><td>{fmt(st["avg_pts"])}</td><td>{fmt(st["avg_fouls"])}</td></tr>' for i,(p,st) in enumerate(arr)) or '<tr><td colspan="8" class="empty">Нужно минимум 4 игры.</td></tr>'
    body=f'''<section class="hero"><div><div class="eyebrow">Performance index</div><h1>Тактик турнира</h1><p>Прозрачный показатель на основе уже имеющихся данных турнира.</p></div></section><section class="card"><h2>Алгоритм</h2><p class="muted">Минимум 4 игры. Итог — до 100 баллов:</p><div class="formula">45% — процент побед (целевой порог 45%)\n25% — средние доп. баллы за игру\n20% — средние очки за игру (ориентир 4)\n10% — дисциплина (средние фолы)</div><p class="muted" style="margin-bottom:0">Доп. баллы и средние очки нормализуются относительно лучших кандидатов. При равенстве: больше побед → больше игр → больше очков. Это performance-based индекс, а не оценка конкретных игровых решений.</p></section><section class="section card"><div class="tablewrap"><table class="table"><thead><tr><th>#</th><th>Игрок</th><th>Score</th><th>Игр</th><th>Win rate</th><th>Доп./игру</th><th>Очки/игру</th><th>Фолы/игру</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''
    return layout('Тактик турнира',body,'tactics')


def admin_page(data,msg=''):
    normalize_players(data)
    players=''.join(f'<span class="chip">{esc(pname(p))}<a href="/del_player?name={quote(pname(p))}" onclick="return confirm(\'Удалить игрока и его результаты?\')">×</a></span>' for p in data['players']) or '<span class="muted">Пока нет игроков.</span>'
    rounds=''.join(f'<span class="chip">{esc(r["name"])}<a href="/del_round?id={quote(r["id"])}" onclick="return confirm(\'Удалить игру и её результаты?\')">×</a></span>' for r in data['rounds']) or '<span class="muted">Пока нет игр.</span>'
    popts=''.join(f'<option value="{esc(pname(p))}">{esc(pname(p))}</option>' for p in data['players'])
    ropts=''.join(f'<option value="{esc(r["id"])}">{esc(r["name"])}</option>' for r in data['rounds'])
    rows=''.join(f'<tr><td>{esc(r.get("round_name","—"))}</td><td>{esc(r.get("player",""))}</td><td>{ROLES[r["role"]][0]} {ROLES[r["role"]][1]}</td><td>{"Победа" if r["outcome"]=="win" else "Поражение"}</td><td>{r.get("fouls",0)}</td><td>+{fmt(r.get("extra",0))}</td><td class="points">{fmt(entry_points(r))}</td><td><a class="btn small danger" href="/del_result?id={quote(r["id"])}" onclick="return confirm(\'Удалить результат?\')">Удалить</a></td></tr>' for r in reversed(data['results'][-30:])) or '<tr><td colspan="8" class="empty">Результатов пока нет.</td></tr>'
    body=f'''<section class="hero"><div><div class="eyebrow">Закрытая панель</div><h1>Ведущему</h1><p>Управление составом, играми и результатами.</p></div><div class="actions"><a class="btn" href="/">Открыть сайт</a><a class="btn" href="/export">Экспорт JSON</a><a class="btn" href="/logout">Выйти</a></div></section>{f'<div class="notice" style="margin-bottom:14px">{esc(msg)}</div>' if msg else ''}<section class="card adminsection"><h2>Игроки</h2><form method="post" action="/add_player" class="formgrid" style="margin-top:14px"><div class="field"><label>Имя</label><input class="input" name="name" required placeholder="Например, Арман"></div><div class="field"><label>Фото</label><input class="input" name="photo" placeholder="1.png или просто 1"><small class="muted">Файл положи в папку images/</small></div><div class="field full"><button class="btn primary">Добавить игрока</button></div></form><div class="chips" style="margin-top:15px">{players}</div></section>
    <section class="card adminsection"><h2>Игры / раунды</h2><form method="post" action="/add_round" class="row" style="margin-top:14px"><input class="input" name="name" required placeholder="Игра 1" style="max-width:400px"><button class="btn primary">Добавить игру</button></form><div class="chips" style="margin-top:15px">{rounds}</div></section>
    <section class="card adminsection"><h2>Внести результат</h2><form method="post" action="/add_result" style="margin-top:14px"><div class="formgrid"><div class="field"><label>Игрок</label><select class="select" name="player" required>{popts}</select></div><div class="field"><label>Игра</label><select class="select" name="round_id" required>{ropts}</select></div><div class="field"><label>Роль</label><select class="select" name="role"><option value="civilian">Мирный</option><option value="sheriff">Шериф</option><option value="mafia">Мафия</option><option value="don">Дон</option></select></div><div class="field"><label>Исход</label><select class="select" name="outcome"><option value="win">Победа</option><option value="loss">Поражение</option></select></div><div class="field"><label>Фолы (0–8)</label><input class="input" type="number" name="fouls" min="0" max="8" value="0"></div><div class="field"><label>Доп. баллы</label><input class="input" type="number" name="extra" step="0.5" value="0"></div><div class="field full"><button class="btn primary">Сохранить результат</button></div></div></form></section>
    <section class="card adminsection"><div class="sectionhead"><h2>Последние результаты</h2><span class="muted">последние 30</span></div><div class="tablewrap"><table class="table"><thead><tr><th>Игра</th><th>Игрок</th><th>Роль</th><th>Исход</th><th>Фолы</th><th>Доп.</th><th>Очки</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>
    <section class="card adminsection"><h2>Настройки</h2><form method="post" action="/settings" class="formgrid" style="margin-top:14px"><div class="field"><label>Название</label><input class="input" name="title" value="{esc(data['settings'].get('title','Спортивная мафия'))}"></div><div class="field"><label>Подзаголовок</label><input class="input" name="subtitle" value="{esc(data['settings'].get('subtitle','Школьный клуб'))}"></div><div class="field full"><button class="btn">Сохранить название</button></div></form><hr style="border:0;border-top:1px solid var(--line);margin:20px 0"><form method="post" action="/set_pass" class="row"><input class="input" type="password" name="pass" minlength="3" required placeholder="Новый пароль" style="max-width:320px"><button class="btn">Сменить пароль</button></form></section>
    <section class="card adminsection dangerbox"><h2>Опасная зона</h2><p class="muted">Сброс удалит игроков, игры и результаты. Перед этим скачай JSON-бэкап.</p><form method="post" action="/reset" onsubmit="return confirm('Точно удалить весь турнир?')"><button class="btn danger">Сбросить турнир</button></form></section>'''
    return layout('Панель ведущего',body,'admin')

def login_page(msg=''):
    body=f'<div class="card" style="max-width:420px;margin:70px auto"><div class="eyebrow">Закрытая зона</div><h1 style="margin:8px 0 18px">Вход ведущего</h1><form method="post" action="/login"><input class="input" type="password" name="pass" placeholder="Пароль" autofocus required><button class="btn primary" style="width:100%;margin-top:10px">Войти</button></form>{f"<div class=\"notice\" style=\"margin-top:12px\">{esc(msg)}</div>" if msg else ""}<p class="muted" style="font-size:12px">При первом запуске пароль: mafia. Сразу смени его.</p></div>'
    return layout('Вход',body)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_html(self,s,code=200,cookie=None):
        b=s.encode('utf-8'); self.send_response(code); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store');
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(b)
    def redirect(self,url='/'):
        self.send_response(303); self.send_header('Location',url); self.end_headers()
    def form(self):
        try:n=int(self.headers.get('Content-Length','0')); return {k:v[0] for k,v in parse_qs(self.rfile.read(n).decode()).items()}
        except Exception:return {}
    def authed(self):
        c=self.headers.get('Cookie',''); tok=None
        for part in c.split(';'):
            if part.strip().startswith('sess='):tok=part.strip()[5:]
        return bool(tok and tok in SESSIONS)
    def do_GET(self):
        u=urlparse(self.path); path=u.path; q=parse_qs(u.query)
        with LOCK:data=load_data(); normalize_players(data)
        if path.startswith('/images/'):
            name=os.path.basename(path[8:]); full=os.path.join(IMAGE_DIR,name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in ('.png','.jpg','.jpeg','.webp'):
                with open(full,'rb') as f:b=f.read()
                self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(full)[0] or 'application/octet-stream'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','public,max-age=3600'); self.end_headers(); self.wfile.write(b); return
            self.send_error(404); return
        if path=='/':return self.send_html(home(data))
        if path=='/players':return self.send_html(players_page(data))
        if path=='/player':return self.send_html(player_page(data,q.get('name',[''])[0]))
        if path=='/rating':return self.send_html(rating_page(data))
        if path=='/rounds':return self.send_html(rounds_page(data))
        if path=='/round':return self.send_html(round_page(data,q.get('id',[''])[0]))
        if path=='/nominations':return self.send_html(nominations_page(data))
        if path=='/tactics':return self.send_html(tactics_page(data))
        if path=='/admin':return self.send_html(admin_page(data) if self.authed() else login_page())
        if path=='/logout':
            self.send_html(home(data),cookie='sess=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'); return
        if path=='/export' and self.authed():
            b=json.dumps(data,ensure_ascii=False,indent=2).encode(); self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename=mafia_data.json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if path in ('/del_player','/del_round','/del_result'):
            if not self.authed():return self.redirect('/admin')
            with LOCK:
                d=load_data(); normalize_players(d)
                if path=='/del_player':
                    n=q.get('name',[''])[0]; d['players']=[p for p in d['players'] if pname(p)!=n]; d['results']=[r for r in d['results'] if r.get('player')!=n]
                elif path=='/del_round':
                    rid=q.get('id',[''])[0]; d['rounds']=[r for r in d['rounds'] if r.get('id')!=rid]; d['results']=[r for r in d['results'] if r.get('round_id')!=rid]
                else:
                    rid=q.get('id',[''])[0]; d['results']=[r for r in d['results'] if r.get('id')!=rid]
                save_data(d)
            return self.redirect('/admin')
        return self.redirect('/')
    def do_POST(self):
        path=urlparse(self.path).path; f=self.form()
        if path=='/login':
            with LOCK:d=load_data()
            if secrets.compare_digest(f.get('pass',''),str(d.get('pass',''))):
                tok=secrets.token_hex(24); SESSIONS.add(tok); return self._login_redirect(tok)
            return self.send_html(login_page('Неверный пароль'))
        if not self.authed():return self.redirect('/admin')
        with LOCK:
            d=load_data(); normalize_players(d)
            if path=='/add_player':
                name=f.get('name','').strip(); photo=f.get('photo','').strip()
                if name and len(d['players'])<100 and not player_by_name(d,name):d['players'].append({'name':name,'photo':photo});save_data(d);return self.redirect('/admin')
            if path=='/add_round':
                name=f.get('name','').strip()
                if name:d['rounds'].append({'id':secrets.token_hex(5),'name':name});save_data(d)
                return self.redirect('/admin')
            if path=='/add_result':
                player=f.get('player','').strip();rid=f.get('round_id','');role=f.get('role');outcome=f.get('outcome')
                rnd=next((r for r in d['rounds'] if r['id']==rid),None)
                try:fouls=max(0,min(8,int(f.get('fouls','0') or 0)));extra=float(f.get('extra','0') or 0)
                except: fouls,extra=0,0
                if player_by_name(d,player) and rnd and role in ROLES and outcome in ('win','loss'):
                    # One result per player per round: replace instead of silently creating duplicates.
                    d['results']=[r for r in d['results'] if not (r.get('player')==player and r.get('round_id')==rid)]
                    d['results'].append({'id':secrets.token_hex(7),'player':player,'round_id':rid,'round_name':rnd['name'],'role':role,'outcome':outcome,'fouls':fouls,'extra':extra});save_data(d)
                return self.redirect('/admin')
            if path=='/set_pass':
                p=f.get('pass','');
                if len(p)>=3:d['pass']=p;save_data(d)
                return self.redirect('/admin')
            if path=='/settings':
                d['settings']['title']=f.get('title','Спортивная мафия').strip()[:80] or 'Спортивная мафия';d['settings']['subtitle']=f.get('subtitle','Школьный клуб').strip()[:80] or 'Школьный клуб';save_data(d);return self.redirect('/admin')
            if path=='/reset':
                pw=d.get('pass','mafia');d=json.loads(json.dumps(DEFAULT));d['pass']=pw;save_data(d);return self.redirect('/admin')
        return self.redirect('/admin')
    def _login_redirect(self,tok):
        self.send_response(303);self.send_header('Location','/admin');self.send_header('Set-Cookie',f'sess={tok}; Path=/; HttpOnly; SameSite=Lax');self.end_headers()

if __name__=='__main__':
    print(f'Sportivnaya Mafia running on 0.0.0.0:{PORT}'); ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
