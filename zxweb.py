import argparse
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>ZX Finder</title><style>
:root{--bg:#060914;--t:#eaf0ff;--m:#8d99ba;--l:rgba(255,255,255,.1);--a:#7c5cff;--b:#00d4ff;--ok:#2be38b;--w:#ffc857}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;min-height:100vh;color:var(--t);font:15px system-ui,sans-serif;background:radial-gradient(700px 420px at 8% -5%,rgba(124,92,255,.38),transparent),radial-gradient(700px 420px at 100% 0,rgba(0,212,255,.2),transparent),var(--bg)}
.w{max-width:960px;margin:0 auto;padding:22px 14px 60px}
.logo{font-size:32px;font-weight:800;letter-spacing:-1px}.logo b{background:linear-gradient(90deg,var(--a),var(--b));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--m);margin:4px 0 18px}
.box{display:flex;gap:8px;padding:8px;border:1px solid var(--l);border-radius:18px;background:rgba(255,255,255,.05);backdrop-filter:blur(14px);box-shadow:0 10px 40px rgba(124,92,255,.15)}
input[type=text]{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--t);font-size:17px;padding:10px}
button{border:0;cursor:pointer;font:inherit}
.go{padding:0 22px;border-radius:12px;color:#fff;font-weight:700;background:linear-gradient(135deg,var(--a),var(--b))}
.opts{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.opts label{display:flex;gap:6px;align-items:center;padding:7px 12px;border:1px solid var(--l);border-radius:999px;color:var(--m);font-size:13px}
.opts input[type=text]{width:62px;font-size:13px;padding:0;border-bottom:1px solid var(--l)}
.bar{height:5px;border-radius:9px;background:var(--l);overflow:hidden}
.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--a),var(--b));transition:width .2s}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}
.s{padding:12px;border:1px solid var(--l);border-radius:14px;background:rgba(255,255,255,.04)}
.s b{display:block;font-size:22px}.s span{font-size:12px;color:var(--m)}
.tabs{display:flex;gap:8px;margin:16px 0 10px;flex-wrap:wrap}
.tabs button{padding:8px 14px;border-radius:999px;background:none;border:1px solid var(--l);color:var(--m)}
.tabs button.on{color:#fff;background:rgba(124,92,255,.3);border-color:var(--a)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}
.c{padding:14px;border:1px solid var(--l);border-radius:16px;background:rgba(255,255,255,.05);animation:in .3s both}
@keyframes in{from{opacity:0;transform:translateY(8px) scale(.98)}}
.c.f{border-color:rgba(43,227,139,.5)}
.h{display:flex;gap:10px;align-items:center}
.av{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-weight:800;background:linear-gradient(135deg,var(--a),var(--b))}
.n{font-weight:700}.u{color:var(--m);font-size:13px;word-break:break-all}
.tt{margin:8px 0;color:var(--m);font-size:13px;word-break:break-word}
.bd{margin-left:auto;font-size:11px;padding:3px 8px;border-radius:99px;white-space:nowrap}
.f .bd{background:rgba(43,227,139,.18);color:var(--ok)}.m .bd{background:rgba(255,200,87,.16);color:var(--w)}
.act{display:flex;gap:8px}
.act a,.act button{flex:1;text-align:center;padding:9px;border-radius:10px;border:1px solid var(--l);background:rgba(255,255,255,.06);color:var(--t);text-decoration:none;font-size:13px}
.note{color:var(--m);font-size:12px;margin-top:22px;line-height:1.6}
#msg{color:var(--w);margin:8px 0}
</style></head><body><div class="w">
<div class="logo"><b>ZX</b> Finder</div>
<div class="sub" id="sub">Type a username or a full name (e.g. saad yousuf)</div>
<div class="box"><input id="q" type="text" placeholder="username or full name" autocapitalize="off" autocomplete="off"><button class="go" id="go">Search</button></div>
<div class="opts">
<label><input type="checkbox" id="deep">Deep scan <span id="dn"></span></label>
<label><input type="checkbox" id="cal" checked>Calibrate</label>
<label><input type="checkbox" id="vars" checked>Variations</label>
<label>Year/digits <input id="ex" type="text" placeholder="2001"></label></div>
<div class="bar"><i id="pb"></i></div><div id="msg"></div>
<div class="stats">
<div class="s"><b id="sf">0</b><span>Found</span></div><div class="s"><b id="sm">0</b><span>Check manually</span></div>
<div class="s"><b id="sc">0</b><span>Checked</span></div><div class="s"><b id="st">0.0</b><span>Seconds</span></div></div>
<div class="tabs"><button data-t="f" class="on">Found</button><button data-t="m">Manual</button><button data-t="a">All</button><button id="exp" style="margin-left:auto">Export CSV</button></div>
<div class="grid" id="g"></div>
<div class="note">Found means a profile page with that exact username exists. It can belong to someone else with the same name, so open it and check. Sites like TikTok, Instagram, Facebook and X hide the answer from bots, so they are listed under Manual.</div>
</div><script>
const $=s=>document.querySelector(s);let es,R=[],tab='f',T0=0,TOT=0,tk,raf;
fetch('/info').then(r=>r.json()).then(j=>{$('#dn').textContent='('+j.all+' sites)';if(j.all<=j.core)$('#deep').disabled=true});
function card(r){const d=document.createElement('div');d.className='c '+(r.status=='FOUND'?'f':'m');
const h=document.createElement('div');h.className='h';const av=document.createElement('div');av.className='av';av.textContent=r.site[0].toUpperCase();
const w=document.createElement('div'),n=document.createElement('div'),u=document.createElement('div');n.className='n';n.textContent=r.site;u.className='u';u.textContent='@'+r.user;w.append(n,u);
const b=document.createElement('span');b.className='bd';b.textContent=r.status=='FOUND'?'Found':'Check manually';h.append(av,w,b);
const t=document.createElement('div');t.className='tt';t.textContent=r.title||r.note||'';
const a=document.createElement('div');a.className='act';const o=document.createElement('a');o.textContent='Open';
if(/^https?:\/\//.test(r.url)){o.href=r.url;o.target='_blank';o.rel='noopener'}
const c=document.createElement('button');c.textContent='Copy';c.onclick=()=>{if(navigator.clipboard)navigator.clipboard.writeText(r.url);c.textContent='Copied'};
a.append(o,c);d.append(h,t,a);return d}
function draw(){cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>{const F=R.filter(r=>r.status=='FOUND'),M=R.filter(r=>r.status!='FOUND');
$('#sf').textContent=F.length;$('#sm').textContent=M.length;
const g=$('#g');g.replaceChildren(...(tab=='f'?F:tab=='m'?M:F.concat(M)).map(card))})}
function go(){const q=$('#q').value.trim();if(!q)return;if(es)es.close();R=[];TOT=0;$('#sc').textContent=0;$('#pb').style.width='0';$('#msg').textContent='';draw();
const p=new URLSearchParams({q,deep:+$('#deep').checked,cal:+$('#cal').checked,vars:+$('#vars').checked,extra:$('#ex').value});
T0=Date.now();clearInterval(tk);tk=setInterval(()=>$('#st').textContent=((Date.now()-T0)/1000).toFixed(1),100);
es=new EventSource('/scan?'+p);
es.onmessage=e=>{const m=JSON.parse(e.data);
if(m.type=='start'){TOT=m.total;$('#msg').textContent='Checking '+m.users+' usernames on '+m.sites+' sites ...'}
else if(m.type=='phase')$('#msg').textContent=m.text;
else if(m.type=='error'){$('#msg').textContent=m.text;fin()}
else if(m.type=='done'){$('#msg').textContent='Done. '+m.found+' found, '+m.maybe+' to check manually.';$('#pb').style.width='100%';fin()}
else{if(m.type=='result'){R.push(m);R.sort((x,y)=>x.status<y.status?-1:1);draw()}
if(TOT){$('#sc').textContent=m.done;$('#pb').style.width=(100*m.done/TOT)+'%'}}};
es.onerror=()=>fin()}
function fin(){if(es)es.close();clearInterval(tk)}
$('#go').onclick=go;$('#q').addEventListener('keydown',e=>{if(e.key=='Enter')go()});
document.querySelectorAll('.tabs button[data-t]').forEach(b=>b.onclick=()=>{tab=b.dataset.t;document.querySelectorAll('.tabs button[data-t]').forEach(x=>x.classList.toggle('on',x===b));draw()});
$('#exp').onclick=()=>{const rows=[['site','user','status','url','title']].concat(R.map(r=>[r.site,r.user,r.status,r.url,r.title]));
const csv=rows.map(x=>x.map(v=>'"'+String(v||'').replace(/"/g,'""')+'"').join(',')).join('\n');
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='zxfinder.csv';a.click()};
</script></body></html>"""


def make_handler(zx, core, deep, base_args):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return self._reply(403, "text/plain", b"forbidden")
            u = urlparse(self.path)
            if u.path == "/":
                self._reply(200, "text/html; charset=utf-8", PAGE.encode())
            elif u.path == "/info":
                self._reply(200, "application/json",
                            json.dumps({"core": len(core), "all": len(deep)}).encode())
            elif u.path == "/scan":
                self.scan(parse_qs(u.query))
            else:
                self._reply(404, "text/plain", b"not found")

        def scan(self, q):
            get = lambda k, d="": q.get(k, [d])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def send(obj):
                self.wfile.write(("data: %s\n\n" % json.dumps(obj)).encode())
                self.wfile.flush()

            opts = argparse.Namespace(threads=40, timeout=8, retries=1,
                                      proxy=base_args.proxy, stop=threading.Event())
            ex = None
            futures = []
            try:
                query = get("q").strip()[:80]
                use_deep = get("deep") == "1"
                if " " in query:
                    users = zx.name_usernames(query, get("extra"), 12 if use_deep else 40)
                    if not users:
                        return send({"type": "error",
                                     "text": "Type the name in English letters, e.g. saad yousuf"})
                else:
                    users = zx.variations(query.lstrip("@")) if get("vars") == "1" \
                        else [query.lstrip("@")]
                    if get("vars") == "1":
                        users = users[:12 if use_deep else 40]
                users = [u for u in users if zx.USERNAME_RE.match(u)]
                if not users:
                    return send({"type": "error",
                                 "text": "Use English letters, digits, . _ - only (max 40)."})
                sites = list(deep if use_deep else core)
                if get("cal") == "1":
                    send({"type": "phase", "text": "Calibrating sites ..."})
                    zx.calibrate(sites, opts)
                    sites = [s for s in sites if not s.broken]
                tasks = [(s, u) for i, u in enumerate(users) for s in sites
                         if s.reliable or i < 3]
                total = len(tasks)
                send({"type": "start", "total": total, "users": len(users), "sites": len(sites)})
                t0 = time.time()
                found = maybe = 0
                ex = ThreadPoolExecutor(opts.threads)
                futures = [ex.submit(zx.check_site, s, u, opts) for s, u in tasks]
                for i, fut in enumerate(as_completed(futures), 1):
                    r = fut.result()
                    if r.status in (zx.FOUND, zx.UNSURE):
                        if r.status == zx.FOUND:
                            found += 1
                        else:
                            maybe += 1
                        send({"type": "result", "site": r.site, "user": r.username,
                              "status": r.status, "url": r.url, "title": r.title,
                              "note": r.note, "done": i})
                    elif i % 8 == 0 or i == total:
                        send({"type": "tick", "done": i})
                send({"type": "done", "found": found, "maybe": maybe,
                      "secs": round(time.time() - t0, 1)})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                opts.stop.set()
                for f in futures:
                    f.cancel()
                if ex:
                    ex.shutdown(wait=False)

    return Handler


def serve(zx, core, deep, port, base_args):
    handler = make_handler(zx, core, deep, base_args)
    srv, chosen = None, port
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), handler)
            chosen = p
            break
        except OSError:
            continue
    if srv is None:
        print("  Could not open a local port.")
        return
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d" % chosen
    print("  ZX Finder is running.\n  Open in your browser:  %s\n  (Ctrl+C to stop)\n" % url)
    opener = shutil.which("termux-open-url")
    if opener:
        subprocess.Popen([opener, url])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
