#!/usr/bin/env python3
"""imgenfeedback — локальный просмотрщик сгенерированных картинок с полем фидбека.

Показывает последние PNG-картинки, рядом с каждой — textarea для фидбека + технический
провенанс из манифестов (модель, LoRA, сид, промпт). Фидбек пишется в
data/feedback.jsonl (append-only, переживает перезагрузку).

Переменные окружения:
  IMGF_IMG_DIR  — папка с картинками и манифестами *.jsonl (по умолчанию comfyui-remote/outputs)
  IMGF_PORT     — порт (по умолчанию 8899)

Запуск:  ./venv/bin/python app.py
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_from_directory

IMG_DIR = Path(os.environ.get(
    "IMGF_IMG_DIR", "/home/art/projects/comfyui-remote/outputs")).expanduser()
DATA_DIR = Path(__file__).resolve().parent / "data"
FEEDBACK = DATA_DIR / "feedback.jsonl"
DATA_DIR.mkdir(exist_ok=True)
FEEDBACK.touch(exist_ok=True)

app = Flask(__name__)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.png$")
MAX_MB = 8


# ── индекс картинок + провенанс из манифестов ──
def build_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for p in sorted(IMG_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        index[p.name] = {"mtime": p.stat().st_mtime}
    # подтягиваем провенанс (последняя запись по имени файла выигрывает)
    for mf in IMG_DIR.glob("*.jsonl"):
        try:
            for line in mf.open():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                f = r.get("file")
                if f and f in index:
                    index[f].update({"meta": r, "manifest": mf.name})
        except OSError:
            continue
    return index


def load_feedback() -> dict[str, list[dict]]:
    fb: dict[str, list[dict]] = {}
    for line in FEEDBACK.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        fb.setdefault(r["file"], []).append(r)
    return fb


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>imgenfeedback — фидбек по картинкам</title>
<style>
  body{background:#141416;color:#ddd;font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:18px}
  h1{font-size:18px;margin:0 0 4px} .sub{color:#888;font-size:12px;margin-bottom:14px}
  .bar{position:sticky;top:0;background:#141416e8;padding:8px 0;margin-bottom:12px;z-index:5}
  input,select,button{background:#222;border:1px solid #3a3a3a;color:#ddd;padding:6px 10px;border-radius:5px}
  button{cursor:pointer} button:hover{background:#2f2f2f}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
  .card{background:#1c1c1f;border:1px solid #2a2a2e;border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
  .card img{width:100%;display:block;background:#000;cursor:zoom-in}
  .meta{padding:8px 10px;border-top:1px solid #2a2a2e}
  .fname{font-size:12px;color:#9ad;font-family:ui-monospace,Menlo,monospace;word-break:break-all}
  .tech{font-size:11px;color:#777;margin-top:4px;cursor:pointer}
  .tech pre{display:none;white-space:pre-wrap;font-size:10px;color:#8a8;background:#111;padding:8px;border-radius:5px;max-height:230px;overflow:auto}
  .tech.open pre{display:block}
  textarea{width:100%;box-sizing:border-box;min-height:56px;background:#101013;color:#dcd;border:1px solid #333;
           border-radius:5px;padding:7px;font:13px/1.4 inherit;resize:vertical}
  .fbrow{display:flex;gap:6px;align-items:center;margin-top:6px}
  .st{font-size:11px;color:#6a6} .hist{font-size:11px;color:#a86;margin-top:5px;white-space:pre-wrap}
  #lb{position:fixed;inset:0;background:#000c;display:none;align-items:center;justify-content:center;z-index:99}
  #lb img{max-width:96vw;max-height:96vh} #lb span{position:absolute;top:10px;right:16px;color:#fff;font-size:26px;cursor:pointer}
</style></head><body>
<h1>imgenfeedback — фидбек по картинкам</h1>
<div class="sub">всего картинок: <b id="tot"></b> · с фидбеком: <b id="fbtot"></b> · клик по картинке = увеличить</div>
<div class="bar">
  <input id="q" placeholder="поиск по имени файла" size="24">
  <select id="f"><option value="">все префиксы</option></select>
  <label>показать <input id="n" type="number" value="120" min="12" max="584" style="width:70px"></label>
  <label><input type="checkbox" id="onlyfb"> только с фидбеком</label>
  <button onclick="location.reload()">обновить</button>
</div>
<div class="grid" id="g"></div>
<div id="lb"><span onclick="this.parentNode.style.display='none'">&times;</span><img id="lbimg"></div>
<script>
let DATA=[],FB={},SHOWN=0;
async function boot(){
  DATA=await (await fetch('/api/index')).json();
  FB=await (await fetch('/api/feedback')).json();
  document.getElementById('tot').textContent=DATA.length;
  document.getElementById('fbtot').textContent=Object.keys(FB).length;
  const pref=[...new Set(DATA.map(d=>d.name.split('_')[0]))].sort();
  document.getElementById('f').innerHTML='<option value="">все префиксы</option>'+pref.map(p=>`<option>${p}</option>`).join('');
  render();
}
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const f=document.getElementById('f').value;
  const n=+document.getElementById('n').value;
  const only=document.getElementById('onlyfb').checked;
  let list=DATA.filter(d=>{
    if(f && !d.name.startsWith(f+'_')) return false;
    if(q && !d.name.toLowerCase().includes(q)) return false;
    if(only && !(FB[d.name]||[]).length) return false;
    return true;
  }).slice(0,n);
  SHOWN=list.length;
  document.getElementById('g').innerHTML=list.map(d=>{
    const m=d.meta||{};
    const tech={model:m.model,loras:JSON.stringify(m.loras||[]),cfg:m.cfg,steps:m.steps,seed:m.seed,sampler:m.sampler+'/'+m.scheduler,size:(m.w||'?')+'x'+(m.h||'?'),prompt:m.prompt,negative:m.negative};
    const h=(FB[d.name]||[]).map(x=>x.text).join('\\n---\\n');
    return `<div class="card">
      <img src="/img/${d.name}" onclick="zoom('${d.name}')" loading="lazy">
      <div class="meta">
        <div class="fname">${d.name}</div>
        <div class="tech" onclick="this.classList.toggle('open')">провенанс ▾<pre>${esc(JSON.stringify(tech,null,1))}</pre></div>
        <textarea id="t_${d.name}" placeholder="твой фидбек…">${esc(h.slice(-1200))}</textarea>
        <div class="fbrow"><button onclick="save('${d.name}')">сохранить</button><span class="st" id="s_${d.name}"></span></div>
        ${(FB[d.name]||[]).length?`<div class="hist">${esc(h)}</div>`:''}
      </div></div>`;}).join('');
}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function zoom(n){const i=document.getElementById('lbimg');i.src='/img/'+n;document.getElementById('lb').style.display='flex';}
async function save(name){
  const t=document.getElementById('t_'+name).value.trim();
  const r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:name,text:t})});
  const j=await r.json();
  const st=document.getElementById('s_'+name);
  st.textContent=j.ok?'сохранено ✓':'ошибка';
  st.style.color=j.ok?'#6a6':'#c66';
  if(j.ok){
    FB[name]=[{file:name,text:t,ts:j.ts}];
    let card=document.getElementById('hist_'+name);
    if(!card){card=document.createElement('div');card.className='hist';card.id='hist_'+name;
      document.getElementById('s_'+name).parentNode.parentNode.appendChild(card);}
    card.textContent=t;
  }
}
['q','f','n','onlyfb'].forEach(id=>{const e=document.getElementById(id);
  e.addEventListener('input',render); e.addEventListener('change',render);});
boot();
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/index")
def api_index():
    return jsonify(build_index())


@app.get("/api/feedback")
def api_feedback():
    return jsonify(load_feedback())


@app.post("/api/feedback")
def api_save():
    data = request.get_json(silent=True) or {}
    name, text = data.get("file", ""), (data.get("text") or "").strip()
    if not SAFE_NAME.match(name):
        abort(400)
    if not (IMG_DIR / name).exists():
        abort(404)
    if len(text) > 8000:
        abort(400)
    rec = {"file": name, "text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with FEEDBACK.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return jsonify(ok=True, ts=rec["ts"])


@app.get("/img/<path:name>")
def img(name):
    if not SAFE_NAME.match(name):
        abort(400)
    return send_from_directory(IMG_DIR, name, max_age=86400)


if __name__ == "__main__":
    port = int(os.environ.get("IMGF_PORT", "8899"))
    print(f"imgenfeedback -> http://0.0.0.0:{port}")
    print(f"  картинки: {IMG_DIR}")
    print(f"  фидбек:   {FEEDBACK}")
    if not IMG_DIR.is_dir():
        print("  ВНИМАНИЕ: папка с картинками не найдена, задай IMGF_IMG_DIR")
    app.run(host="0.0.0.0", port=port, threaded=True)
