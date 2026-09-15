#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Спортивная мафия — простой сервер без внешних библиотек."""
import json, os, secrets, threading, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get('PORT', 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.environ.get('DATA_FILE', os.path.join(DATA_DIR, 'mafia_data.json'))
IMAGE_DIR = os.path.join(BASE_DIR, 'images')
os.makedirs(IMAGE_DIR, exist_ok=True)
LOCK = threading.Lock()

ROLES = {
    'mafia': ('Мафия', 'black'),
    'don': ('Дон', 'black'),
    'sheriff': ('Шериф', 'red'),
    'civilian': ('Мирный', 'red'),
}
NOMINATIONS = [('mafia','Лучшая мафия'),('don','Лучший дон'),('sheriff','Лучший шериф'),('civilian','Лучший мирный')]


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding='utf-8') as f:
                d = json.load(f)
            d.setdefault('pass', 'mafia'); d.setdefault('players', []); d.setdefault('rounds', []); d.setdefault('results', [])
            return d
        except Exception:
            pass
    return {'pass':'mafia','players':[],'rounds':[],'results':[]}


def save_data(d):
    os.makedirs(os.path.dirname(DATA_FILE) or '.', exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def foul_penalty(f):
    f = max(0, min(4, int(f or 0)))
    if f <= 2: return 0
    if f == 3: return -0.5
    return -1


def entry_points(r):
    team = ROLES[r['role']][1]
    if r.get('outcome') == 'win':
        base = 4 if team == 'black' else 3
    else:
        base = 1
    return base + foul_penalty(r.get('fouls', 0)) + max(0, min(2, float(r.get('extra', 0) or 0)))


def fmt(n):
    n = float(n)
    return str(int(n)) if n.is_integer() else f'{n:.2f}'.rstrip('0').rstrip('.')


def esc(s):
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')


def role_rating(entries):
    n = len(entries)
    if not n: return None
    pts = sum(entry_points(e) for e in entries) / n
    extra = sum(float(e.get('extra',0) or 0) for e in entries) / n
    fouls = sum(int(e.get('fouls',0) or 0) for e in entries) / n
    wins = sum(e.get('outcome') == 'win' for e in entries)
    return {'rating': pts + .5*extra + .1*n - .2*fouls, 'games':n, 'wins':wins,
            'winrate':100*wins/n, 'avg_extra':extra, 'avg_fouls':fouls, 'avg_pts':pts}


def nominations(data):
    out=[]
    for role,title in NOMINATIONS:
        best=None; beststat=None
        for p in data['players']:
            es=[r for r in data['results'] if r.get('player')==p and r.get('role')==role]
            st=role_rating(es)
            if st and (beststat is None or (st['rating'],st['wins'],st['games']) > (beststat['rating'],beststat['wins'],beststat['games'])):
                best,beststat=p,st
        out.append((title,best,beststat))
    return out


def special_nominations(data):
    ps=data['players']
    def gs(p): return [r for r in data['results'] if r.get('player')==p]
    games={p:gs(p) for p in ps}
    wins={p:sum(r.get('outcome')=='win' for r in games[p]) for p in ps}
    extras={p:sum(float(r.get('extra',0) or 0) for r in games[p]) for p in ps}
    activist=max(ps,key=lambda p:(len(games[p]),wins[p],p.lower())) if ps else None
    champion=max(ps,key=lambda p:(wins[p],len(games[p]),p.lower())) if ps else None
    leader=max(ps,key=lambda p:(extras[p],wins[p],len(games[p]),p.lower())) if ps and max(extras.values(),default=0)>=.5 else None
    candidates=[]
    for p in ps:
        e=games[p]
        if len(e)<4: continue
        wr=sum(r.get('outcome')=='win' for r in e)/len(e)
        ae=sum(float(r.get('extra',0) or 0) for r in e)/len(e)
        ap=sum(entry_points(r) for r in e)/len(e)
        af=sum(int(r.get('fouls',0) or 0) for r in e)/len(e)
        candidates.append((p,wr,ae,ap,af,len(e),wins[p]))
    tact=None; tact_score=None
    if candidates:
        best_extra=max(c[2] for c in candidates) or 1
        for p,wr,ae,ap,af,n,w in candidates:
            discipline=max(0,1-af/4)
            score=100*(.45*wr + .25*(ae/best_extra) + .20*(ap/4) + .10*discipline)
            key=(score,w,n,p.lower())
            if tact_score is None or key>tact_score:
                tact_score=key; tact=p
    return [
        ('Активист',activist,f'{len(games[activist])} игр' if activist else ''),
        ('Чемпион',champion,f'{wins[champion]} побед' if champion else ''),
        ('Лидер',leader,f'{fmt(extras[leader])} доп. баллов' if leader else ''),
        ('Тактик турнира',tact,f'{fmt(tact_score[0])}/100 · {tact_score[2]} игр' if tact else ''),
    ]


def image_for_player(data, name):
    try: idx=data['players'].index(name)+1
    except ValueError: return None
    for ext in ('.png','.jpg','.jpeg','.webp'):
        p=os.path.join(IMAGE_DIR,str(idx)+ext)
        if os.path.isfile(p): return '/images/'+str(idx)+ext
    return None


CSS = r'''<style>
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#070707;color:#f3f3f3;font-family:Inter,Segoe UI,Arial,sans-serif}body{position:relative;overflow-x:hidden}body:before{content:"";position:fixed;inset:0;background:linear-gradient(rgba(5,5,6,.78),rgba(5,5,6,.92)),url('/images/1.png') center/cover no-repeat;z-index:-5}body:after{content:"♠   ♦       ♣      ♥       ♠       ♦";position:fixed;inset:-10% -10%;font-family:Georgia,serif;font-size:70px;line-height:2.4;letter-spacing:32px;color:rgba(190,22,43,.12);transform:rotate(-8deg);z-index:-4;pointer-events:none}.top{position:sticky;top:0;z-index:20;background:rgba(7,7,8,.93);border-bottom:1px solid #45131a;backdrop-filter:blur(12px)}.bar{max-width:1150px;margin:auto;padding:14px 16px;display:flex;justify-content:space-between;align-items:center;gap:10px}.brand{font-weight:900;letter-spacing:2px;font-size:16px}.nav{display:flex;gap:7px;flex-wrap:wrap}.wrap{max-width:1150px;margin:auto;padding:18px 14px 60px}.card{position:relative;background:rgba(12,12,13,.93);border:1px solid #42131a;border-radius:8px;padding:18px;margin-bottom:14px;box-shadow:0 16px 40px rgba(0,0,0,.38);overflow:hidden}.card:before,.card:after{content:"";position:absolute;width:55px;height:78px;border:1px solid rgba(190,25,45,.25);border-radius:6px;background:rgba(255,255,255,.012);pointer-events:none}.card:before{right:-17px;top:-26px;transform:rotate(18deg)}.card:after{left:-22px;bottom:-42px;transform:rotate(-14deg)}h1{font-size:22px;margin:0 0 5px}h2{font-size:14px;color:#e34254;letter-spacing:1.1px;margin:0 0 12px;text-transform:uppercase}.btn{display:inline-block;text-decoration:none;color:#eee;background:#121212;border:1px solid #551720;border-radius:6px;padding:8px 12px;font-size:13px;cursor:pointer}.btn:hover{background:#241014;border-color:#a42a3a}.primary{background:#a71f31;border-color:#c53042;font-weight:700}.danger{background:#260b10;border-color:#701b28}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px}.field{min-width:160px;flex:1}.field label{display:block;font-size:11px;color:#999;margin-bottom:5px}.input,.select{width:100%;padding:9px 10px;border-radius:6px;border:1px solid #4b151d;background:#0c0c0d;color:#fff;font-size:13px}.input:focus,.select:focus{outline:none;border-color:#d03548}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:620px}th,td{padding:9px 7px;border-bottom:1px solid #261116;text-align:left;font-size:12px;white-space:nowrap}th{font-size:10px;letter-spacing:.8px;text-transform:uppercase;color:#d33a4b}.player{display:flex;align-items:center;gap:8px}.avatar{width:34px;height:34px;border-radius:5px;object-fit:cover;border:1px solid #63202a;background:#171112}.avatar.empty{display:grid;place-items:center;color:#c52b3e;font-weight:800}.pts{font-weight:800;color:#ff4b5e}.muted{color:#888;font-size:11px}.tag{display:inline-block;padding:3px 7px;border-radius:4px;font-size:11px;font-weight:700}.black{background:#250a0f;color:#ff8995;border:1px solid #6e1a25}.red{background:#171313;color:#efb7be;border:1px solid #5a242b}.nomgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:9px}.nom{background:#101010;border:1px solid #3b151b;border-radius:7px;padding:14px;min-height:112px}.nom .title{color:#e34254;text-transform:uppercase;font-size:11px;letter-spacing:.8px;margin-bottom:7px}.nom .name{font-size:16px;font-weight:900}.nom .stats{color:#888;font-size:11px;margin-top:5px;line-height:1.45}.algorithm{display:grid;gap:6px}.algorithm div{background:#0f0f10;border-left:2px solid #a62435;padding:8px 10px;color:#999;font-size:11px}.algorithm b{color:#eee}.notice{padding:9px 11px;border:1px solid #45151b;background:#110b0d;color:#aaa;border-radius:6px;font-size:12px}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{padding:5px 8px;background:#111;border:1px solid #42151b;border-radius:5px;font-size:12px}.chip a{color:#e34254;text-decoration:none;margin-left:7px;font-weight:800}.preview{text-align:center;background:#10090b;border:1px dashed #6a202a;border-radius:7px;padding:11px;margin:10px 0;font-size:13px}.preview b{font-size:20px;color:#ff4054}@media(max-width:650px){.bar{align-items:flex-start;flex-direction:column}.nav{width:100%}.nav .btn{flex:1;text-align:center}.wrap{padding:12px 8px 45px}.card{padding:14px}h1{font-size:19px}}
</style>'''


def layout(title, body, nav=''):
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>{CSS}</head><body><div class="top"><div class="bar"><div class="brand">СПОРТИВНАЯ МАФИЯ</div><div class="nav">{nav}</div></div></div><main class="wrap">{body}</main></body></html>'''


def nav_admin(): return '<a class="btn" href="/">Таблица</a><a class="btn danger" href="/logout">Выйти</a>'
def nav_public(): return '<a class="btn" href="/admin">Ведущему</a>'


def role_cell(r, data):
    name,team=ROLES[r['role']]
    img=image_for_player(data,r['player'])
    return f'<span class="tag {team}">{name}</span><br><span class="pts">{fmt(entry_points(r))}</span><div class="muted">{"Победа" if r["outcome"]=="win" else "Поражение"} · {r.get("fouls",0)} ф. · +{fmt(r.get("extra",0) or 0)} доп.</div>'


def public_page(data):
    totals={p:sum(entry_points(r) for r in data['results'] if r.get('player')==p) for p in data['players']}
    table={p:{} for p in data['players']}
    for r in data['results']: table.setdefault(r['player'],{})[r['round_id']]=r
    order=sorted(data['players'],key=lambda p:(-totals[p],p.lower()))
    rows=[]
    for i,p in enumerate(order,1):
        img=image_for_player(data,p)
        av=f'<img class="avatar" src="{img}" alt="">' if img else f'<span class="avatar empty">{esc(p[:1].upper())}</span>'
        cells=''.join(f'<td>{role_cell(table[p][rnd["id"]],data) if rnd["id"] in table[p] else "<span class=muted>—</span>"}</td>' for rnd in data['rounds'])
        rows.append(f'<tr><td>{i}</td><td><div class="player">{av}<b>{esc(p)}</b></div></td>{cells}<td class="pts">{fmt(totals[p])}</td></tr>')
    head=''.join(f'<th>{esc(r["name"])}</th>' for r in data['rounds'])
    table_html=f'<div class="tablewrap"><table><thead><tr><th>#</th><th>Игрок</th>{head}<th>Очки</th></tr></thead><tbody>{"".join(rows) or "<tr><td colspan=20 class=muted>Игроков пока нет</td></tr>"}</tbody></table></div>'

    ns=nominations(data)
    nomcards=[]
    for title,b,st in ns:
        if b:
            nomcards.append(f'<div class="nom"><div class="title">{title}</div><div class="name">{esc(b)}</div><div class="stats">Игр: {st["games"]} · Побед: {st["wins"]} · Win rate: {st["winrate"]:.0f}%<br>Средние очки: {fmt(st["avg_pts"])} · доп.: {fmt(st["avg_extra"])} · фолы: {fmt(st["avg_fouls"])}</div></div>')
        else: nomcards.append(f'<div class="nom"><div class="title">{title}</div><div class="muted">Пока нет кандидата</div></div>')
    for title,b,stat in special_nominations(data):
        nomcards.append(f'<div class="nom"><div class="title">{title}</div><div class="name">{esc(b) if b else "—"}</div><div class="stats">{esc(stat) if stat else "Недостаточно данных"}</div></div>')

    alg='''<div class="algorithm">
    <div><b>Лучшая мафия / Лучший дон / Лучший шериф / Лучший мирный.</b> Рейтинг = средние очки + 0.5 × средние доп. баллы + 0.1 × количество игр − 0.2 × средние фолы. Учитываются только игры в выбранной роли.</div>
    <div><b>Активист.</b> Максимальное количество сыгранных игр. При равенстве — больше побед.</div>
    <div><b>Чемпион.</b> Максимальное количество побед. При равенстве — больше игр.</div>
    <div><b>Лидер.</b> Максимальная сумма дополнительных баллов. Минимум 0.5 доп. балла.</div>
    <div><b>Тактик турнира.</b> Минимум 4 игры: 45% win rate, 25% доп. баллы за игру, 20% средние очки за игру, 10% дисциплина по фолам. Итог 0–100.</div>
    <div><b>Очки за игру.</b> Победа чёрных = 4, победа красных = 3, поражение = 1. Фолы: 0–2 = 0, 3 = −0.5, 4 = −1. Доп. баллы: 0–2.</div></div>'''
    body=f'<div class="card"><h1>Турнирная таблица</h1>{table_html}</div><div class="card"><h2>Номинации</h2><div class="nomgrid">{"".join(nomcards)}</div></div><div class="card"><h2>Как считаются номинации</h2>{alg}</div>'
    return layout('Спортивная мафия',body,nav_public())


def admin_page(data,msg=''):
    po=''.join(f'<option value="{esc(p)}">{esc(p)}</option>' for p in data['players']) or '<option value="">Сначала добавьте игрока</option>'
    ro=''.join(f'<option value="{esc(r["id"])}">{esc(r["name"])}</option>' for r in data['rounds']) or '<option value="">Сначала добавьте игру</option>'
    players=''.join(f'<span class="chip">{esc(p)}<a href="/del_player?name={esc(p)}" onclick="return confirm(\'Удалить игрока и его результаты?\')">×</a></span>' for p in data['players']) or '<span class="muted">Пока пусто</span>'
    rounds=''.join(f'<span class="chip">{esc(r["name"])}<a href="/del_round?id={esc(r["id"])}" onclick="return confirm(\'Удалить игру и её результаты?\')">×</a></span>' for r in data['rounds']) or '<span class="muted">Пока пусто</span>'
    results=[]
    for r in reversed(data['results'][-20:]):
        rn,team=ROLES[r['role']]
        results.append(f'<tr><td>{esc(r.get("round_name",""))}</td><td>{esc(r["player"])}</td><td><span class="tag {team}">{rn}</span></td><td>{"Победа" if r["outcome"]=="win" else "Поражение"}</td><td>{r.get("fouls",0)}</td><td>+{fmt(r.get("extra",0) or 0)}</td><td class="pts">{fmt(entry_points(r))}</td><td><a class="btn danger" href="/del_result?id={esc(r["id"])}" onclick="return confirm(\'Удалить запись?\')">×</a></td></tr>')
    body=f'''<div class="card"><h2>Добавить игрока</h2><form method="post" action="/add_player" class="row"><input class="input" style="flex:1;min-width:200px" name="name" placeholder="Имя игрока" required><button class="btn primary">Добавить</button></form><div class="chips" style="margin-top:9px">{players}</div></div>
<div class="card"><h2>Добавить игру</h2><form method="post" action="/add_round" class="row"><input class="input" style="flex:1;min-width:200px" name="name" placeholder="Например: Игра 1" required><button class="btn primary">Добавить</button></form><div class="chips" style="margin-top:9px">{rounds}</div></div>
<div class="card"><h2>Внести результат</h2><form method="post" action="/add_result"><div class="grid"><div class="field"><label>Игрок</label><select class="select" name="player">{po}</select></div><div class="field"><label>Игра</label><select class="select" name="round_id">{ro}</select></div><div class="field"><label>Роль</label><select class="select" name="role"><option value="civilian">Мирный</option><option value="sheriff">Шериф</option><option value="mafia">Мафия</option><option value="don">Дон</option></select></div><div class="field"><label>Исход</label><select class="select" name="outcome"><option value="win">Победа</option><option value="loss">Поражение</option></select></div><div class="field"><label>Фолы, максимум 4</label><input class="input" type="number" name="fouls" min="0" max="4" value="0"></div><div class="field"><label>Доп. баллы, максимум 2</label><input class="input" type="number" name="extra" min="0" max="2" step="0.5" value="0"></div></div><div class="preview">Начислится: <b id="pts">3</b></div><button class="btn primary" style="width:100%">Сохранить результат</button><div class="msg">{esc(msg)}</div></form></div>
<div class="card"><h2>Последние результаты</h2><div class="tablewrap"><table><thead><tr><th>Игра</th><th>Игрок</th><th>Роль</th><th>Исход</th><th>Фолы</th><th>Доп.</th><th>Очки</th><th></th></tr></thead><tbody>{"".join(results) or '<tr><td colspan=8 class="muted">Записей пока нет</td></tr>'}</tbody></table></div></div>
<div class="card"><h2>Настройки</h2><div class="row"><form method="post" action="/set_pass" class="row"><input class="input" name="pass" type="password" placeholder="Новый пароль" required><button class="btn">Сменить пароль</button></form><a class="btn" href="/export">Скачать данные</a><form method="post" action="/reset" onsubmit="return confirm('Сбросить весь турнир?')"><button class="btn danger">Сбросить турнир</button></form></div></div>
<script>const roles=document.querySelector('[name=role]'),out=document.querySelector('[name=outcome]'),f=document.querySelector('[name=fouls]'),e=document.querySelector('[name=extra]');function calc(){{let black=roles.value==='mafia'||roles.value==='don';let base=out.value==='win'?(black?4:3):1;let fv=Math.max(0,Math.min(4,parseInt(f.value)||0));let pen=fv<=2?0:(fv===3?-0.5:-1);let ex=Math.max(0,Math.min(2,parseFloat(e.value)||0));let x=base+pen+ex;document.getElementById('pts').textContent=Number.isInteger(x)?x:x.toFixed(2)}}[roles,out,f,e].forEach(x=>x.addEventListener('input',calc));calc();</script>'''
    return layout('Панель ведущего',body,nav_admin())


def login_page(msg=''):
    body=f'<div class="card" style="max-width:380px;margin:55px auto"><h2>Вход для ведущего</h2><form method="post" action="/login"><input class="input" type="password" name="pass" placeholder="Пароль" autofocus required><button class="btn primary" style="width:100%;margin-top:9px">Войти</button><div class="msg">{esc(msg)}</div></form></div>'
    return layout('Вход',body,'<a class="btn" href="/">Таблица</a>')

SESSIONS=set()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self,html,code=200,cookie=None):
        b=html.encode('utf-8'); self.send_response(code); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(b)
    def _form(self):
        n=int(self.headers.get('Content-Length','0')); return {k:v[0] for k,v in parse_qs(self.rfile.read(n).decode()).items()}
    def _authed(self):
        c=self.headers.get('Cookie',''); return any('sess='+s in c for s in SESSIONS)
    def _redirect(self,url,cookie=None):
        self.send_response(303); self.send_header('Location',url)
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers()
    def _image(self,path):
        rel=path[len('/images/'):]
        if not rel or '/' in rel or '..' in rel:return False
        fp=os.path.join(IMAGE_DIR,rel)
        if not os.path.isfile(fp):return False
        typ=mimetypes.guess_type(fp)[0]
        if typ not in ('image/png','image/jpeg','image/webp'):return False
        b=open(fp,'rb').read(); self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Cache-Control','public,max-age=3600'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return True
    def do_GET(self):
        u=urlparse(self.path); path=u.path; q=parse_qs(u.query)
        if path.startswith('/images/'):
            if not self._image(path): self.send_response(404); self.end_headers()
            return
        with LOCK:data=load_data()
        if path=='/': self._send(public_page(data)); return
        if path=='/admin': self._send(admin_page(data) if self._authed() else login_page()); return
        if path=='/logout': SESSIONS.difference_update({s for s in list(SESSIONS) if 'sess='+s in self.headers.get('Cookie','')}); self._send(public_page(data),cookie='sess=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'); return
        if path=='/export' and self._authed():
            b=json.dumps(data,ensure_ascii=False,indent=2).encode(); self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename=mafia_data.json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if not self._authed(): self._redirect('/admin'); return
        if path=='/del_player':
            name=q.get('name',[''])[0]
            with LOCK:
                data=load_data(); data['players']=[p for p in data['players'] if p!=name]; data['results']=[r for r in data['results'] if r.get('player')!=name]; save_data(data)
            self._redirect('/admin'); return
        if path=='/del_round':
            rid=q.get('id',[''])[0]
            with LOCK:
                data=load_data(); data['rounds']=[r for r in data['rounds'] if r.get('id')!=rid]; data['results']=[r for r in data['results'] if r.get('round_id')!=rid]; save_data(data)
            self._redirect('/admin'); return
        if path=='/del_result':
            rid=q.get('id',[''])[0]
            with LOCK:
                data=load_data(); data['results']=[r for r in data['results'] if r.get('id')!=rid]; save_data(data)
            self._redirect('/admin'); return
        self._redirect('/')
    def do_POST(self):
        u=urlparse(self.path); f=self._form()
        if u.path=='/login':
            with LOCK:data=load_data()
            if f.get('pass')==data.get('pass'):
                tok=secrets.token_hex(18); SESSIONS.add(tok); self._redirect('/admin', f'sess={tok}; Path=/; HttpOnly; SameSite=Lax')
                return
            self._send(login_page('Неверный пароль')); return
        if not self._authed(): self._redirect('/admin'); return
        with LOCK:
            data=load_data()
            if u.path=='/add_player':
                name=f.get('name','').strip()
                if name and name not in data['players'] and len(data['players'])<100:data['players'].append(name);save_data(data)
            elif u.path=='/add_round':
                name=f.get('name','').strip()
                if name:data['rounds'].append({'id':secrets.token_hex(5),'name':name});save_data(data)
            elif u.path=='/add_result':
                p=f.get('player','').strip(); rid=f.get('round_id',''); role=f.get('role'); outcome=f.get('outcome'); rnd=next((r for r in data['rounds'] if r['id']==rid),None)
                if p in data['players'] and rnd and role in ROLES and outcome in ('win','loss'):
                    fouls=max(0,min(4,int(f.get('fouls',0) or 0))); extra=max(0,min(2,float(f.get('extra',0) or 0)))
                    # One result per player per game: update existing entry instead of duplicating it.
                    old=next((r for r in data['results'] if r.get('player')==p and r.get('round_id')==rid),None)
                    obj={'id':old['id'] if old else secrets.token_hex(7),'player':p,'round_id':rid,'round_name':rnd['name'],'role':role,'outcome':outcome,'fouls':fouls,'extra':extra}
                    if old:data['results'][data['results'].index(old)]=obj
                    else:data['results'].append(obj)
                    save_data(data)
            elif u.path=='/set_pass':
                if len(f.get('pass',''))>=3:data['pass']=f['pass'];save_data(data)
            elif u.path=='/reset':
                data={'pass':data.get('pass','mafia'),'players':[],'rounds':[],'results':[]};save_data(data)
        self._redirect('/admin')

if __name__=='__main__':
    print(f'Sportivnaya Mafia: 0.0.0.0:{PORT}')
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
