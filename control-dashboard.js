// control-dashboard.js — MeshCore watch/send/kill control panel
// Hub client (TCP 7777): RX channel_message (watch), TX send_channel (send),
// KILL = stop bots + set .killed flag honored by watchdog.sh. Web UI on :3005.
import http from 'http';
import net from 'net';
import fs from 'fs';
import { exec } from 'child_process';

const PORT = parseInt(process.env.DASH_PORT || '3005', 10);
const HUB_HOST = '127.0.0.1', HUB_PORT = 7777;
const BOTDIR = '/home/joe/meshcore-bots';
const KILL_FLAG = BOTDIR + '/.killed';

let channels = [];
let hubConnected = false;
const recent = [];
const sseClients = new Set();

function pushEvent(ev) {
  ev.ts = ev.ts || new Date().toISOString();
  recent.push(ev); if (recent.length > 200) recent.shift();
  const line = 'data: ' + JSON.stringify(ev) + '\n\n';
  for (const res of sseClients) { try { res.write(line); } catch (e) {} }
}

let hubSock = null, buf = '';
function connectHub() {
  hubSock = net.createConnection({ host: HUB_HOST, port: HUB_PORT }, () => {
    hubSock.write(JSON.stringify({ action: 'register', name: 'control-ui' }) + '\n');
    pushEvent({ kind: 'sys', text: 'connected to hub' });
  });
  hubSock.on('data', d => {
    buf += d.toString(); let i;
    while ((i = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      let m; try { m = JSON.parse(line); } catch (e) { continue; }
      if (m.type === 'channel_message')
        pushEvent({ kind: 'msg', channelIdx: m.channelIdx, sender: m.senderName || '?', text: m.text || '' });
      else if (m.type === 'hub_state') { hubConnected = !!m.connected; channels = m.channels || channels; pushEvent({ kind: 'state', hubConnected, channels }); }
      else if (m.type === 'channels_update') { channels = m.channels || channels; pushEvent({ kind: 'state', hubConnected, channels }); }
      else if (m.type === 'hub_connected') { hubConnected = true; pushEvent({ kind: 'state', hubConnected, channels }); }
      else if (m.type === 'hub_disconnected') { hubConnected = false; pushEvent({ kind: 'state', hubConnected, channels }); }
    }
  });
  hubSock.on('close', () => { pushEvent({ kind: 'sys', text: 'hub link lost, retrying' }); hubSock = null; setTimeout(connectHub, 3000); });
  hubSock.on('error', () => { try { hubSock.destroy(); } catch (e) {} hubSock = null; });
}
function hubSend(obj) { if (hubSock) { try { hubSock.write(JSON.stringify(obj) + '\n'); return true; } catch (e) {} } return false; }

const PAGE = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MeshCore Control</title>
<style>
body{background:#121212;color:#e0e0e0;font-family:Arial,Helvetica,sans-serif;margin:0}
header{background:#1e1e1e;padding:10px 16px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #333}
h1{font-size:18px;margin:0}#hub{font-size:13px;padding:3px 8px;border-radius:4px;background:#333}
.on{background:#1b5e20}.off{background:#7f1d1d}
#killbtn{margin-left:auto;background:#b91c1c;color:#fff;border:0;padding:8px 16px;border-radius:6px;font-weight:bold;cursor:pointer}
#resumebtn{background:#1b5e20;color:#fff;border:0;padding:8px 16px;border-radius:6px;cursor:pointer;display:none}
#feed{height:62vh;overflow-y:auto;padding:12px 16px;font-family:monospace;font-size:13px}
.row{padding:2px 0;border-bottom:1px solid #222}.t{color:#666}.ch{color:#4fc3f7}.snd{color:#aaa}
.me{color:#81c784}.sys{color:#ffb74d}.sent{color:#ce93d8}
footer{display:flex;gap:8px;padding:12px 16px;background:#1a1a1a;border-top:1px solid #333}
select,input{background:#2a2a2a;color:#e0e0e0;border:1px solid #444;border-radius:4px;padding:8px}
#txt{flex:1}#sendbtn{background:#1565c0;color:#fff;border:0;padding:8px 18px;border-radius:6px;cursor:pointer}
#banner{display:none;background:#7f1d1d;color:#fff;text-align:center;padding:6px;font-weight:bold}
</style></head><body>
<header><h1>MeshCore Control</h1><span id="hub" class="off">hub: …</span>
<button id="resumebtn">RESUME BOTS</button><button id="killbtn">KILL BOTS</button></header>
<div id="banner">BOTS KILLED — watchdog held off. Manual send still works.</div>
<div id="feed"></div>
<footer><select id="chan"></select><input id="txt" placeholder="message…" autocomplete="off">
<button id="sendbtn">Send</button></footer>
<script>
const feed=document.getElementById('feed'),chan=document.getElementById('chan'),txt=document.getElementById('txt');
const hub=document.getElementById('hub'),banner=document.getElementById('banner');
const killbtn=document.getElementById('killbtn'),resumebtn=document.getElementById('resumebtn');
let chans=[];
function add(cls,html){const d=document.createElement('div');d.className='row';d.innerHTML=html;feed.appendChild(d);
  const near=feed.scrollHeight-feed.scrollTop-feed.clientHeight<80;if(near)feed.scrollTop=feed.scrollHeight;}
function tm(s){return (s||'').replace(/.*T/,'').replace(/\..*/,'');}
function chName(i){const c=chans.find(c=>(c.channelIdx!==undefined?c.channelIdx:c.idx)===i);return c?(c.name):('ch'+i);}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function setChans(cs){const seen=new Set();chans=(cs||[]).filter(c=>{const i=(c.channelIdx!==undefined?c.channelIdx:c.idx);if(!c.name||seen.has(i))return false;seen.add(i);return true;});const cur=chan.value;chan.innerHTML='';chans.forEach(c=>{const idx=(c.channelIdx!==undefined?c.channelIdx:c.idx);const o=document.createElement('option');o.value=idx;o.textContent=idx+': '+c.name;chan.appendChild(o);});if(cur)chan.value=cur;}
function setKilled(k){banner.style.display=k?'block':'none';killbtn.style.display=k?'none':'inline-block';resumebtn.style.display=k?'inline-block':'none';}
const es=new EventSource('/events');
es.onmessage=e=>{const m=JSON.parse(e.data);
 if(m.kind==='msg') add('','<span class="t">'+tm(m.ts)+'</span> <span class="ch">['+chName(m.channelIdx)+']</span> <span class="snd">'+esc(m.sender)+':</span> '+esc(m.text));
 else if(m.kind==='sent') add('','<span class="t">'+tm(m.ts)+'</span> <span class="ch">['+chName(m.channelIdx)+']</span> <span class="me">YOU:</span> <span class="sent">'+esc(m.text)+'</span>');
 else if(m.kind==='sys') add('','<span class="t">'+tm(m.ts)+'</span> <span class="sys">• '+esc(m.text)+'</span>');
 else if(m.kind==='state'){hub.textContent='hub: '+(m.hubConnected?'connected':'down');hub.className=m.hubConnected?'on':'off';setChans(m.channels);}
 else if(m.kind==='killstate'){setKilled(m.killed);}};
function send(){const t=txt.value.trim();if(!t)return;fetch('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channelIdx:parseInt(chan.value),text:t})});txt.value='';}
killbtn.onclick=()=>{if(confirm('KILL all bots? They stop transmitting and the watchdog will not restart them until RESUME.')){fetch('/kill',{method:'POST'}).then(()=>setKilled(true));}};
resumebtn.onclick=()=>{fetch('/resume',{method:'POST'}).then(()=>setKilled(false));};
sendbtn.onclick=send;txt.addEventListener('keydown',e=>{if(e.key==='Enter')send();});
</script></body></html>`;

const server = http.createServer((req, res) => {
  if (req.url === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(PAGE); return; }
  if (req.url === '/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive' });
    res.write('retry: 3000\n\n');
    res.write('data: ' + JSON.stringify({ kind: 'state', hubConnected, channels }) + '\n\n');
    for (const ev of recent.slice(-50)) res.write('data: ' + JSON.stringify(ev) + '\n\n');
    res.write('data: ' + JSON.stringify({ kind: 'killstate', killed: fs.existsSync(KILL_FLAG) }) + '\n\n');
    sseClients.add(res); req.on('close', () => sseClients.delete(res)); return;
  }
  if (req.method === 'POST') {
    let body = ''; req.on('data', c => body += c); req.on('end', () => {
      let p = {}; try { p = JSON.parse(body || '{}'); } catch (e) {}
      if (req.url === '/send') {
        const ch = parseInt(p.channelIdx), text = (p.text || '').toString();
        if (isNaN(ch) || !text) { res.writeHead(400); res.end('bad'); return; }
        const ok = hubSend({ action: 'send_channel', channelIdx: ch, text });
        pushEvent({ kind: 'sent', channelIdx: ch, text });
        res.writeHead(ok ? 200 : 503); res.end(ok ? 'ok' : 'hub offline'); return;
      }
      if (req.url === '/kill') {
        fs.writeFileSync(KILL_FLAG, new Date().toISOString() + '\n');
        exec("pkill -f 'node .*bot-.*[.]js'", () => {});
        pushEvent({ kind: 'sys', text: 'KILL: bots stopped + watchdog gated' });
        res.writeHead(200); res.end(JSON.stringify({ killed: true })); return;
      }
      if (req.url === '/resume') {
        try { fs.unlinkSync(KILL_FLAG); } catch (e) {}
        pushEvent({ kind: 'sys', text: 'RESUME: flag cleared; watchdog restores bots within ~5 min' });
        res.writeHead(200); res.end(JSON.stringify({ killed: false })); return;
      }
      res.writeHead(404); res.end();
    });
    return;
  }
  res.writeHead(404); res.end();
});

connectHub();
server.listen(PORT, '0.0.0.0', () => console.log('control-dashboard on http://0.0.0.0:' + PORT));
