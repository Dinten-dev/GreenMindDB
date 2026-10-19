"""Small authenticated HPK comparison, independent of frontend rollout versions."""

import secrets
import uuid

from fastapi.responses import HTMLResponse


def response(device_id):
    device = str(uuid.UUID(str(device_id)))
    return _response(PAGE.replace("__DEVICE__", device))


def index_response():
    return _response(INDEX)


def _response(template):
    nonce = secrets.token_urlsafe(24)
    page = template.replace("__NONCE__", nonce)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; connect-src 'self'; "
            f"script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


INDEX = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>GreenMind · HPK-Vergleich</title>
<style>body{font:16px system-ui;color:#17362b;background:#f6f8f6;padding:24px;max-width:900px}
li{margin:20px 0}a{color:#174d34}small{display:block;margin-top:6px}</style>
<h1>HPK-Signalvergleich</h1><p>Direct-Sensor wählen. Originalaufnahmen bleiben erhalten.</p>
<p id="status" role="status">Sensoren laden …</p><ul id="devices"></ul>
<script nonce="__NONCE__">'use strict';
async function init(){try{const r=await fetch('/api/v1/direct-ingest/devices',{credentials:'same-origin',cache:'no-store'});
 if(!r.ok)throw new Error(r.status===401?'Bitte zuerst bei GreenMind anmelden.':'Sensorliste momentan nicht verfügbar.');
 const rows=await r.json();for(const d of rows){if(!/^[0-9a-f-]{36}$/i.test(d.id))continue;
 const li=document.createElement('li'),a=document.createElement('a'),s=document.createElement('small');
 a.href='/api/v1/visualization/direct/'+encodeURIComponent(d.id)+'/hpk';
 a.textContent='Sensor '+(d.hardware_id||d.id)+' · HPK vergleichen';
 s.textContent=d.last_seen?'Zuletzt empfangen: '+new Date(d.last_seen).toLocaleString():'Noch keine Aufnahme';
 li.append(a,s);document.getElementById('devices').append(li);}
 document.getElementById('status').textContent=rows.length?'':'Keine Direct-Sensoren freigegeben.';
 }catch(e){document.getElementById('status').textContent=e.message;}}init();
</script></html>"""


PAGE = """<!doctype html>
<html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>GreenMind · HPK-Signalvergleich</title>
<style>
body{font:16px system-ui;background:#f6f8f6;color:#17362b;margin:0;padding:24px;max-width:1100px}
h1{font-size:26px}label{display:block;margin:16px 0 6px}select,input,button{font:inherit;padding:9px;max-width:100%;box-sizing:border-box}
button{background:#17362b;color:white;border:0;border-radius:5px;cursor:pointer}button:disabled{opacity:.5}
canvas{display:block;background:white;width:100%;height:230px;border:1px solid #ccd8ce;border-radius:5px}
small{display:block;line-height:1.5}a{color:#174d34;margin-right:24px}#status{min-height:24px}section{margin:20px 0}
</style>
<h1>HPK-Signalvergleich</h1>
<p>380-Hz-Original und abgeleitete HPK-Filterspur. Originalaufnahmen bleiben erhalten.</p>
<label for="recording">Aufnahme der letzten 24 Stunden</label><select id="recording" disabled></select>
<label for="offset">Beginn im gewählten Aufnahmeabschnitt (Sekunden)</label>
<input id="offset" type="number" value="0" min="0" step="1"><button id="compare" disabled>30 Sekunden vergleichen</button>
<p id="status" role="status" aria-live="polite">Aufnahmen laden …</p>
<section><h2>Original · 380 Hz</h2><canvas id="raw" aria-label="Originalsignal in Millivolt"></canvas></section>
<section><h2>HPK-Filterspur · 100 Hz</h2><canvas id="filtered" aria-label="Gefiltertes Signal in Millivolt"></canvas></section>
<small>Beide Kurven zeigen mV relativ zum jeweiligen Median, mit gleicher Skala und Zeitachse.
Lücken werden nicht verbunden. Filterzustand beginnt an Lücken und Zehn-Minuten-Grenzen neu.
Die Filterkette verzögert das Signal um etwa 20 ms. Messaufbau: nicht geprüft.</small>
<p><a id="original" hidden>Original-WAV</a><a id="derived" hidden>HPK-WAV herunterladen</a></p>
<small>HPK-WAV: 100 Hz; gespeicherter int16-Wert = mV × 10. Tiefpass, 50-Hz-Netzbrummfilter,
Mittelung. Kein Hochpass. Der Übergang von 380 auf 400 Hz ist eine zusätzliche Rechenstufe.</small>
<script nonce="__NONCE__">
'use strict';
const base='/api/v1/visualization/direct/__DEVICE__', $=id=>document.getElementById(id);
let recordings=[], graphs=null;
async function get(path){const r=await fetch(path,{credentials:'same-origin',cache:'no-store'});
 if(!r.ok)throw new Error(r.status===401?'Bitte zuerst bei GreenMind anmelden.':r.status===503?'Leser momentan belegt. Bitte erneut versuchen.':'Aufnahme nicht verfügbar ('+r.status+').');
 return r.json();}
function median(values){const s=[...values].sort((a,b)=>a-b),m=Math.floor(s.length/2);return s.length%2?s[m]:(s[m-1]+s[m])/2;}
function draw(id,points,color,range,start,end,rate){
 const canvas=$(id),w=canvas.clientWidth,h=230,dpr=window.devicePixelRatio||1;canvas.width=w*dpr;canvas.height=h*dpr;
 const c=canvas.getContext('2d');c.scale(dpr,dpr);c.font='12px system-ui';c.fillStyle='#17362b';
 const left=60,right=w-12,top=15,bottom=h-32,center=median(points.map(p=>p.value));
 const x=t=>left+(t-start)/(end-start)*(right-left),y=v=>top+(range-v)/(2*range)*(bottom-top);
 c.strokeStyle='#dfe6df';for(const v of [-range,0,range]){c.beginPath();c.moveTo(left,y(v));c.lineTo(right,y(v));c.stroke();c.fillText(v.toFixed(1)+' mV',4,y(v)+4);}
 for(const seconds of [0,10,20,30]){c.fillText(seconds+' s',x(start+seconds)-7,h-9);}
 c.strokeStyle=color;c.lineWidth=1;c.beginPath();let prev=null;
 for(const p of points){const t=Date.parse(p.timestamp)/1000,px=x(t),py=y(p.value-center);
 if(prev===null||t-prev>1.5/rate)c.moveTo(px,py);else c.lineTo(px,py);prev=t;}c.stroke();
}
function repaint(){if(!graphs)return;const [raw,filtered,start]=graphs;
 const centered=[raw,filtered].flatMap(g=>{const m=median(g.data.map(p=>p.value));return g.data.map(p=>Math.abs(p.value-m));});
 const range=Math.max(1,...centered)*1.05;draw('raw',raw.data,'#37769c',range,start,start+30,raw.sample_rate);
 draw('filtered',filtered.data,'#24824b',range,start,start+30,filtered.sample_rate);}
async function compare(){const rec=recordings[Number($('recording').value)];if(!rec)return;
 $('compare').disabled=true;$('status').textContent='Original prüfen und Filter berechnen …';
 try{const offset=Number($('offset').value);if(!Number.isFinite(offset)||offset<0||offset>=rec.duration_seconds)throw new Error('Beginn liegt ausserhalb der Aufnahme.');
 const start=Date.parse(rec.started_at)/1000+offset;
 const query=new URLSearchParams({at:new Date(start*1000).toISOString(),seconds:'30'});
 const raw=await get(base+'/waveform?'+query);query.set('processing','hpk');const filtered=await get(base+'/waveform?'+query);
 if(!raw.data.length||!filtered.data.length)throw new Error('Für diesen Abschnitt fehlen Messwerte.');
 graphs=[raw,filtered,start];repaint();
 const wav=base+'/wav/'+encodeURIComponent(rec.segment_id)+'/'+rec.revision+'/'+rec.run;
 $('original').href=wav;$('derived').href=wav+'?processing=hpk';$('original').hidden=false;$('derived').hidden=false;
 $('status').textContent='Original: '+raw.data.length+' Werte; HPK: '+filtered.data.length+' Werte. Downloads enthalten den gesamten gewählten Aufnahmeabschnitt.';
 }catch(e){graphs=null;$('original').hidden=true;$('derived').hidden=true;for(const id of ['raw','filtered']){const c=$(id);c.getContext('2d').clearRect(0,0,c.width,c.height);}$('status').textContent=e.message;}
 finally{$('compare').disabled=false;}}
async function init(){try{recordings=await get(base+'/recordings?range=24h');if(!recordings.length)throw new Error('Keine Aufnahme der letzten 24 Stunden.');
 recordings.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=new Date(r.started_at).toLocaleString()+' · '+r.duration_seconds.toFixed(1)+' s';$('recording').append(o);});
 $('recording').disabled=false;$('compare').disabled=false;$('status').textContent='Aufnahme wählen und vergleichen.';
 }catch(e){$('status').textContent=e.message;}}
 $('compare').addEventListener('click',compare);$('recording').addEventListener('change',()=>{$('offset').value=0;});
 window.addEventListener('resize',repaint);init();
</script></html>"""
