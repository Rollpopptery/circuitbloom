#!/usr/bin/env python3
"""
svg_server.py — KiCad SVG server, port 8087.

Thin HTTP layer only — all business logic is delegated to:
    board_capture.py   — GET /board.svg
    freerouting.py     — GET /route
    kicad_push.py      — GET /push
    routes.py          — all other endpoints

Usage:
    python svg_server.py
    python svg_server.py --port 8087
    python svg_server.py --socket ipc:///tmp/kicad/api-41011.sock
"""

import http.server
import os
import sys
import urllib.parse
import argparse

sys.path.insert(0, os.path.dirname(__file__))

import server_state as ss
from board_capture import capture_board_handler
from freerouting import run_freerouting_handler
from kicad_push import push_to_kicad_handler
from routes import build_get_routes, build_prefix_routes, build_post_routes

# ── HTML page ─────────────────────────────────────────────────────────────────
PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Circuit Bloom 2</title>
<style>
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg:#111416; --bg2:#181c1f; --bg3:#1f2427;
  --border:#2a3035; --border2:#3a4248;
  --text:#c8d0d6; --text2:#8a9aa4;
  --accent:#4af0b0; --accent2:#1a6644;
  --warn:#f0a44a; --err:#f05a4a; --ok:#4af0b0;
  --panel-w:420px;
}
body { background:var(--bg); font-family:'Courier New',monospace; color:var(--text); display:flex; flex-direction:column; height:100vh; overflow:hidden; }
.toolbar { display:flex; align-items:center; gap:6px; padding:8px 14px; background:var(--bg2); border-bottom:1px solid var(--border); flex-shrink:0; flex-wrap:wrap; }
.toolbar-title { font-size:13px; letter-spacing:3px; color:var(--text2); margin-right:6px; text-transform:uppercase; }
.tb-btn { font-family:'Courier New',monospace; font-size:13px; padding:5px 14px; cursor:pointer; border:1px solid var(--border2); background:var(--bg3); color:var(--text); transition:background 0.1s,border-color 0.1s,color 0.1s; }
.tb-btn:hover { background:#252c30; border-color:#4a5860; }
.tb-btn.active { background:var(--accent2); border-color:var(--accent); color:var(--accent); }
.tb-btn.api-btn { border-color:var(--accent2); color:var(--accent); }
.tb-btn.api-btn:hover,.tb-btn.api-btn.active { background:var(--accent2); }
#message { font-size:13px; color:var(--text2); min-width:0; flex:1; text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.main { display:flex; flex:1; overflow:hidden; position:relative; }
#svg-container { flex:1; overflow:hidden; background:var(--bg); cursor:grab; }
#svg-container.dragging { cursor:grabbing; }
#svg-container svg { display:block; width:100%; height:100%; }
#svg-target { width:100%; height:100%; }
#api-panel { width:var(--panel-w); background:var(--bg2); border-left:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; transform:translateX(100%); transition:transform 0.3s cubic-bezier(0.4,0,0.2,1); position:absolute; right:0; top:0; bottom:0; z-index:10; }
#api-panel.open { transform:translateX(0); }
.panel-header { padding:14px 16px 10px; border-bottom:1px solid var(--border); flex-shrink:0; }
.panel-header h2 { font-size:14px; letter-spacing:2px; color:var(--accent); text-transform:uppercase; }
.panel-header p { font-size:12px; color:var(--text2); margin-top:3px; }
.panel-body { flex:1; overflow-y:auto; padding:12px 16px; display:flex; flex-direction:column; gap:10px; }
.panel-body::-webkit-scrollbar { width:5px; }
.panel-body::-webkit-scrollbar-thumb { background:var(--border2); }
.field-row { display:flex; flex-direction:column; gap:4px; }
.field-label { font-size:11px; color:var(--text2); letter-spacing:1px; text-transform:uppercase; }
select,input[type=text] { font-family:'Courier New',monospace; font-size:13px; background:var(--bg3); border:1px solid var(--border2); color:var(--text); padding:6px 9px; width:100%; outline:none; }
select:focus,input[type=text]:focus { border-color:var(--accent2); }
.method-badge { display:inline-block; font-size:11px; padding:2px 6px; border:1px solid; margin-right:6px; letter-spacing:1px; }
.method-GET { color:var(--accent); border-color:var(--accent2); }
.method-POST { color:var(--warn); border-color:#6a4a1a; }
.endpoint-desc { font-size:13px; color:var(--text2); padding:4px 0 2px; }
.params-section { display:flex; flex-direction:column; gap:8px; }
.param-row { display:flex; gap:8px; align-items:flex-start; }
.param-name { font-size:12px; color:var(--text2); width:90px; flex-shrink:0; text-align:right; padding-top:7px; }
.param-desc { font-size:11px; color:#5a6870; margin-top:2px; }
.url-preview { font-size:11px; color:var(--text2); background:var(--bg); border:1px solid var(--border); padding:7px 9px; word-break:break-all; line-height:1.6; }
.url-preview .url-path { color:var(--accent); }
.url-preview .url-params { color:var(--text); }
.run-btn { font-family:'Courier New',monospace; font-size:14px; letter-spacing:1px; padding:8px 0; cursor:pointer; border:1px solid var(--accent2); background:transparent; color:var(--accent); text-transform:uppercase; width:100%; transition:background 0.1s; }
.run-btn:hover { background:var(--accent2); }
.response-box { flex-shrink:0; border-top:1px solid var(--border); padding:10px 16px; max-height:220px; overflow-y:auto; }
.response-label { font-size:11px; color:var(--text2); letter-spacing:1px; text-transform:uppercase; margin-bottom:5px; }
.response-text { font-size:12px; color:var(--text); white-space:pre-wrap; word-break:break-all; line-height:1.5; }
.response-text.ok { color:var(--ok); }
.response-text.err { color:var(--err); }
.divider { height:1px; background:var(--border); margin:2px 0; }
</style>
</head>
<body>
<div class="toolbar">
  <span class="toolbar-title">Circuit Bloom 2</span>
  <button class="tb-btn" onclick="getKicad()">Get KiCad</button>
  <button class="tb-btn" onclick="refreshSvg()">Refresh</button>
  <button class="tb-btn" id="btnLabels" onclick="toggleLabels()">Labels: ON</button>
  <button class="tb-btn" onclick="resetView()">Reset</button>
  <button class="tb-btn" onclick="pushKicad()">Push KiCad</button>
  <button class="tb-btn api-btn" id="btnApi" onclick="togglePanel()">API ▶</button>
  <span id="message">—</span>
</div>
<div class="main">
  <div id="svg-container"><div id="svg-target"></div></div>
  <div id="api-panel">
    <div class="panel-header"><h2>API Explorer</h2><p>Select an endpoint, set parameters, run.</p></div>
    <div class="panel-body" id="panel-body">
      <div class="field-row">
        <span class="field-label">Endpoint</span>
        <select id="ep-select" onchange="onEndpointChange()"></select>
      </div>
      <div id="ep-desc" class="endpoint-desc"></div>
      <div class="divider"></div>
      <div class="params-section" id="params-section"></div>
      <div class="url-preview" id="url-preview"></div>
      <button class="run-btn" onclick="runEndpoint()">▶ Run</button>
    </div>
    <div class="response-box" id="response-box" style="display:none">
      <div class="response-label">Response</div>
      <pre class="response-text" id="response-text"></pre>
    </div>
  </div>
</div>
<script>
var labelsOn=true,vb=null,drag=null,panelOpen=false,manifest=[],currentEp=null;
window.addEventListener('DOMContentLoaded',function(){loadManifest();});
function loadManifest(){fetch('/api').then(function(r){return r.json();}).then(function(d){manifest=d;buildDropdown();onEndpointChange();}).catch(function(e){setMessage('manifest failed: '+e.message,'#f05a4a');});}
function buildDropdown(){var sel=document.getElementById('ep-select');sel.innerHTML='';var groups={};manifest.forEach(function(ep){var g=ep.group||'Other';if(!groups[g])groups[g]=[];groups[g].push(ep);});Object.keys(groups).forEach(function(gn){var og=document.createElement('optgroup');og.label='── '+gn+' ──';groups[gn].forEach(function(ep){var opt=document.createElement('option');opt.value=manifest.indexOf(ep);opt.textContent=ep.method+'  '+ep.path;og.appendChild(opt);});sel.appendChild(og);});}
function onEndpointChange(){var sel=document.getElementById('ep-select');var idx=parseInt(sel.value);if(isNaN(idx)||idx<0||idx>=manifest.length)return;currentEp=manifest[idx];var descEl=document.getElementById('ep-desc');descEl.innerHTML='<span class="method-badge method-'+currentEp.method+'">'+currentEp.method+'</span>'+(currentEp.desc||'');var ps=document.getElementById('params-section');ps.innerHTML='';(currentEp.params||[]).forEach(function(param){var row=document.createElement('div');row.className='param-row';var label=document.createElement('span');label.className='param-name';label.textContent=param.name;var wrap=document.createElement('div');wrap.style.flex='1';wrap.style.display='flex';wrap.style.flexDirection='column';wrap.style.gap='2px';var inp=document.createElement('input');inp.type='text';inp.id='param-'+param.name;inp.value=param.default!==undefined?param.default:'';inp.addEventListener('input',updateUrlPreview);var hint=document.createElement('span');hint.className='param-desc';hint.textContent=param.desc||'';wrap.appendChild(inp);wrap.appendChild(hint);row.appendChild(label);row.appendChild(wrap);ps.appendChild(row);});updateUrlPreview();document.getElementById('response-box').style.display='none';}
function updateUrlPreview(){if(!currentEp)return;var url=buildUrl();var preview=document.getElementById('url-preview');var qIdx=url.indexOf('?');if(qIdx>=0){preview.innerHTML='<span class="url-path">'+url.substring(0,qIdx)+'</span><span class="url-params">'+url.substring(qIdx)+'</span>';}else{preview.innerHTML='<span class="url-path">'+url+'</span>';}}
function buildUrl(){if(!currentEp)return'';var path=currentEp.path;if(currentEp.suffix_param){var val=getParam(currentEp.suffix_param);path=path+(val||'');var op=(currentEp.params||[]).filter(function(p){return p.name!==currentEp.suffix_param;});var qs=op.map(function(p){var v=getParam(p.name);return v!==''?encodeURIComponent(p.name)+'='+encodeURIComponent(v):null;}).filter(Boolean).join('&');return path+(qs?'?'+qs:'');}
if(currentEp.method==='POST')return path;var qs=(currentEp.params||[]).map(function(p){var v=getParam(p.name);return v!==''?encodeURIComponent(p.name)+'='+encodeURIComponent(v):null;}).filter(Boolean).join('&');return path+(qs?'?'+qs:'');}
function getParam(name){var el=document.getElementById('param-'+name);return el?el.value.trim():'';}
function runEndpoint(){if(!currentEp)return;var url=buildUrl();var method=currentEp.method;var returns=currentEp.returns||'json';setMessage('running '+method+' '+url+' ...','#4a8aaf');var fetchOpts={method:method};if(method==='POST'){fetchOpts.headers={'Content-Type':'application/json'};fetchOpts.body=buildPostBody();}
fetch(url,fetchOpts).then(function(r){if(returns==='svg'||r.headers.get('Content-Type')==='image/svg+xml'){return r.text().then(function(t){return{type:'svg',data:t,ok:r.ok};});}return r.text().then(function(t){return{type:'json',data:t,ok:r.ok};});}).then(function(result){if(result.type==='svg'){showResponse('SVG received ('+result.data.length+' bytes) — viewer refreshed',true);loadSvgText(result.data);setMessage('done — SVG loaded','#4af0b0');}else{var text=result.data;try{text=JSON.stringify(JSON.parse(result.data),null,2);}catch(e){}showResponse(text,result.ok);setMessage(result.ok?'ok':'error',result.ok?'#4af0b0':'#f05a4a');if(result.ok&&currentEp.returns==='svg'){refreshSvg();}}}).catch(function(e){showResponse('fetch error: '+e.message,false);setMessage('error','#f05a4a');});}
function buildPostBody(){if(!currentEp||!currentEp.body_template){var obj={};(currentEp.params||[]).forEach(function(p){var v=getParam(p.name);if(p.name==='attrs'){try{v=JSON.parse(v);}catch(e){}}obj[p.name]=v;});return JSON.stringify(obj);}var tmpl=currentEp.body_template;(currentEp.params||[]).forEach(function(p){tmpl=tmpl.replace('{'+p.name+'}',getParam(p.name));});return tmpl;}
function showResponse(text,ok){var box=document.getElementById('response-box');var pre=document.getElementById('response-text');box.style.display='block';pre.textContent=text;pre.className='response-text '+(ok?'ok':'err');}
function togglePanel(){panelOpen=!panelOpen;document.getElementById('api-panel').classList.toggle('open',panelOpen);var btn=document.getElementById('btnApi');btn.textContent=panelOpen?'API ◀':'API ▶';btn.classList.toggle('active',panelOpen);}
function setMessage(msg,col){var el=document.getElementById('message');el.textContent=msg;el.style.color=col||'#8a9aa4';}
function loadSvg(url,msg){setMessage(msg||'loading...','#4a8aaf');fetch(url).then(function(r){if(!r.ok)return r.text().then(function(t){throw new Error(t);});return r.text();}).then(function(t){loadSvgText(t);}).catch(function(e){setMessage('error: '+e.message,'#f05a4a');});}
function loadSvgText(t){var tgt=document.getElementById('svg-target');tgt.innerHTML=t;var svg=tgt.querySelector('svg');if(!svg)return;var p=svg.getAttribute('viewBox').split(' ').map(Number);vb={x:p[0],y:p[1],w:p[2],h:p[3]};svg.dataset.origViewbox=svg.getAttribute('viewBox');applyLabels();attachPanZoom(svg);setMessage('scroll to zoom · drag to pan','#8a9aa4');}
function getKicad(){loadSvg('/board.svg','capturing from KiCad...');}
function refreshSvg(){loadSvg('/svg','refreshing...');}
function pushKicad(){setMessage('pushing to KiCad...','#4a8aaf');fetch('/push').then(function(r){return r.json();}).then(function(d){if(d.ok)setMessage('pushed: '+d.message,'#4af0b0');else setMessage('push failed: '+(d.error||d.message),'#f05a4a');}).catch(function(e){setMessage('push error: '+e.message,'#f05a4a');});}
function applyLabels(){var svg=document.querySelector('#svg-target svg');if(svg)svg.classList.toggle('labels-hidden',!labelsOn);}
function toggleLabels(){labelsOn=!labelsOn;document.getElementById('btnLabels').textContent='Labels: '+(labelsOn?'ON':'OFF');document.getElementById('btnLabels').classList.toggle('active',labelsOn);applyLabels();}
function resetView(){var svg=document.querySelector('#svg-target svg');if(!svg)return;var p=svg.dataset.origViewbox.split(' ').map(Number);vb={x:p[0],y:p[1],w:p[2],h:p[3]};svg.setAttribute('viewBox',svg.dataset.origViewbox);}
function attachPanZoom(svg){var c=document.getElementById('svg-container');svg.addEventListener('wheel',function(e){e.preventDefault();var s=e.deltaY>0?1.15:0.87,r=svg.getBoundingClientRect();vb.x+=vb.w*((e.clientX-r.left)/r.width)*(1-s);vb.y+=vb.h*((e.clientY-r.top)/r.height)*(1-s);vb.w*=s;vb.h*=s;svg.setAttribute('viewBox',vb.x+' '+vb.y+' '+vb.w+' '+vb.h);},{passive:false});svg.addEventListener('mousedown',function(e){drag={x:e.clientX,y:e.clientY,vbx:vb.x,vby:vb.y};c.classList.add('dragging');e.preventDefault();});window.addEventListener('mousemove',function(e){if(!drag)return;var r=svg.getBoundingClientRect();vb.x=drag.vbx-(e.clientX-drag.x)/r.width*vb.w;vb.y=drag.vby-(e.clientY-drag.y)/r.height*vb.h;svg.setAttribute('viewBox',vb.x+' '+vb.y+' '+vb.w+' '+vb.h);});window.addEventListener('mouseup',function(){drag=null;c.classList.remove('dragging');});}
</script>
</body>
</html>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _params(path: str) -> dict:
    return urllib.parse.parse_qs(path.split('?', 1)[1]) if '?' in path else {}


# ── HTTP handler ──────────────────────────────────────────────────────────────

class SVGHandler(http.server.BaseHTTPRequestHandler):

    socket_path    = None
    _get_routes    = None
    _prefix_routes = None
    _post_routes   = None

    @classmethod
    def _load_routes(cls):
        if cls._get_routes is None:
            cls._get_routes    = build_get_routes()
            cls._prefix_routes = build_prefix_routes()
            cls._post_routes   = build_post_routes()

    def do_GET(self):
        self._load_routes()
        path   = self.path.split('?')[0]
        params = _params(self.path)

        if path == '/health':
            return self._respond(200, 'application/json', b'{"ok":true}')

        if path == '/board.svg':
            code, ct, body = capture_board_handler(SVGHandler.socket_path)
            return self._respond(code, ct, body)

        if path == '/route':
            code, ct, body = run_freerouting_handler(params)
            return self._respond(code, ct, body)

        if path == '/push':
            code, ct, body = push_to_kicad_handler(SVGHandler.socket_path)
            return self._respond(code, ct, body)

        handler = self._get_routes.get(path)
        if handler:
            with ss.board_lock:
                code, ct, body = handler(
                    ss.current_board, ss.current_corridors, params)
            return self._respond(code, ct, body)

        for prefix, handler in self._prefix_routes.items():
            if path.startswith(prefix):
                suffix = path[len(prefix):]
                with ss.board_lock:
                    code, ct, body = handler(
                        ss.current_board, ss.current_corridors, params, suffix)
                return self._respond(code, ct, body)

        self._respond(200, 'text/html', PAGE_HTML.encode())

    def do_POST(self):
        self._load_routes()
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        path   = self.path.split('?')[0]

        entry = self._post_routes.get(path)
        if entry:
            handler, needs_body = entry
            with ss.board_lock:
                code, ct, resp = handler(ss.current_board, body) \
                    if needs_body else handler(ss.current_board)
        else:
            code, ct, resp = 404, 'application/json', \
                b'{"ok":false,"error":"unknown endpoint"}'

        self._respond(code, ct, resp)

    def _respond(self, code, content_type, body: bytes):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    parser = argparse.ArgumentParser(description="Circuit Bloom 2")
    parser.add_argument('--port',   type=int, default=8087)
    parser.add_argument('--socket', default=None)
    args = parser.parse_args()

    SVGHandler.socket_path = args.socket

    server = http.server.HTTPServer(('0.0.0.0', args.port), SVGHandler)
    print(f'\n  KiCad SVG Server  →  http://localhost:{args.port}\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')
        server.shutdown()


if __name__ == '__main__':
    run()