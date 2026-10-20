/* Core: constants, settings, icons, router, overlays. Plain scripts so the app also opens from file://. */
const SCHED=["fcfs","rr","priority","cfs","edf","token_laxity"];
const LABEL={fcfs:"FCFS",rr:"Round Robin",priority:"Static Priority",cfs:"CFS-like",edf:"EDF",token_laxity:"Token-Laxity",
  tls_full:"TLS full",tls_no_planner:"TLS no planner",tls_no_midtoken:"TLS no mid-token",tls_no_aging:"TLS no guard"};
const SHORT={fcfs:"FCFS",rr:"RR",priority:"Priority",cfs:"CFS",edf:"EDF",token_laxity:"TLS"};
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const cssv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const col=s=>cssv("--c-"+s)||cssv("--brand");
const fmt=(x,d=1)=>x==null||isNaN(x)?"–":Number(x).toFixed(d);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const S=(sk,s,m)=>BENCH.summary[sk][s][m].mean, CI=(sk,s,m)=>BENCH.summary[sk][s][m];
const SCN=()=>Object.keys(BENCH.scenarios);
const scnName=k=>BENCH.scenarios[k]?.title||k;
const scnShort={light:"Light",mixed:"Mixed",heavy:"Heavy",stress:"Stress",tenant:"Tenant"};

/* ---------- settings (localStorage, never required) ---------- */
const DEFAULTS={theme:"dark",scenario:"heavy",left:"token_laxity",right:"cfs",speed:15,zoom:6,motion:true};
const Store={
  get(k,d){try{const v=localStorage.getItem("tls."+k);return v==null?d:JSON.parse(v)}catch{return d}},
  set(k,v){try{localStorage.setItem("tls."+k,JSON.stringify(v))}catch{}},
  del(k){try{localStorage.removeItem("tls."+k)}catch{}}
};
const settings=Object.assign({},DEFAULTS,Store.get("settings",{}));
function saveSettings(){Store.set("settings",settings);applyTheme()}
function applyTheme(){document.documentElement.dataset.theme=settings.theme;if(!settings.motion)document.documentElement.classList.add("calm");else document.documentElement.classList.remove("calm")}
applyTheme();
const reduced=()=>!settings.motion||matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- icons (24px stroke) ---------- */
const IC={
 dash:'<rect x="3" y="3" width="7" height="9" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/><rect x="3" y="16" width="7" height="5" rx="2"/>',
 arena:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
 chart:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
 flask:'<path d="M9 3h6M10 3v6L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3"/><path d="M7.5 15h9"/>',
 shield:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/>',
 sliders:'<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
 play:'<path d="M7 4.5v15l13-7.5z" fill="currentColor"/>', pause:'<path d="M8 5v14M16 5v14" stroke-width="3"/>',
 restart:'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
 plus:'<path d="M12 5v14M5 12h14"/>', x:'<path d="M6 6l12 12M18 6 6 18"/>',
 search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', dl:'<path d="M12 4v12m0 0-4-4m4 4 4-4M4 20h16"/>',
 sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
 moon:'<path d="M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z"/>', check:'<path d="m5 12 5 5 9-10"/>',
 alert:'<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17.5v.01"/>', chev:'<path d="m6 9 6 6 6-6"/>',
 zap:'<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>', cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', users:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 7M18 14.8c1.9.7 3.2 2.4 3.5 5"/>',
 turtle:'<path d="M3 17h18M5 17a7 7 0 0 1 14 0"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>', trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>', chat:'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
 gauge:'<path d="M4 18a9 9 0 1 1 16 0"/><path d="m12 14 4-5"/>'
};
const icon=(n,cls="")=>`<svg class="${cls}" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n]||""}</svg>`;
const LOGO=`<svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#9D8FFF"/><stop offset="1" stop-color="#5B49F2"/></linearGradient></defs><rect width="34" height="34" rx="10" fill="url(#lg)"/><rect x="7" y="9" width="12" height="4" rx="2" fill="#fff"/><rect x="12" y="15" width="15" height="4" rx="2" fill="#fff" opacity=".85"/><rect x="7" y="21" width="9" height="4" rx="2" fill="#fff" opacity=".6"/><circle cx="24" cy="23" r="2.2" fill="#3DDC97"/></svg>`;

/* ---------- helpers ---------- */
function el(tag,attrs={},html=""){const e=document.createElement(tag);for(const k in attrs)e.setAttribute(k,attrs[k]);e.innerHTML=html;return e}
function loadScript(src){return new Promise((res,rej)=>{const s=document.createElement("script");s.src=src;s.onload=res;s.onerror=()=>rej(new Error("failed to load "+src));document.head.appendChild(s)})}
async function replay(key){if(window.REPLAY&&REPLAY[key])return REPLAY[key];await loadScript(`data/replay_${key}.js`);return REPLAY[key]}
async function apiRun(params){const r=await fetch("/api/run?"+new URLSearchParams(params));let j={};try{j=await r.json()}catch{}if(!r.ok)throw new Error(j.error||("server "+r.status));return j}
let serverUp=null;
async function pingServer(){if(location.protocol==="file:")serverUp=false;else try{const r=await fetch("/api/health",{cache:"no-store"});serverUp=r.ok}catch{serverUp=false}const d=$("#srv-dot"),t=$("#srv-txt");if(d){d.className="dot "+(serverUp?"on":"off");t.textContent=serverUp?"Live server connected":"Offline: saved results only"}return serverUp}

function tip(html,ev){const t=$("#tip");if(!html){t.style.display="none";return}t.innerHTML=html;t.style.display="block";
  t.style.left=Math.min(ev.clientX+14,innerWidth-t.offsetWidth-8)+"px";t.style.top=Math.min(ev.clientY+14,innerHeight-t.offsetHeight-8)+"px"}
function onHover(node,fn){node.addEventListener("mousemove",e=>tip(fn(e),e));node.addEventListener("mouseleave",()=>tip(null))}
function toast(msg,kind="ok"){const t=el("div",{class:"toast "+kind,role:"status"},icon(kind==="ok"?"check":"alert")+`<span>${esc(msg)}</span>`);$("#toasts").appendChild(t);setTimeout(()=>t.remove(),3800)}
function modal(html,{onClose}={}){const s=el("div",{class:"scrim",role:"dialog","aria-modal":"true"});const m=el("div",{class:"modal"},html);s.appendChild(m);
  const close=()=>{s.remove();document.removeEventListener("keydown",k);onClose&&onClose()};const k=e=>{if(e.key==="Escape")close()};
  s.addEventListener("mousedown",e=>{if(e.target===s)close()});document.addEventListener("keydown",k);document.body.appendChild(s);
  $$("[data-close]",m).forEach(b=>b.onclick=close);const f=$("input,select,button",m);f&&f.focus();return{close,el:m}}

/* segmented chips: opts [{k,label}] */
function chips(node,opts,cur,on){node.className="chips";node.setAttribute("role","group");
  node.innerHTML=opts.map(o=>`<button data-k="${o.k}" aria-pressed="${o.k===cur}">${esc(o.label)}</button>`).join("");
  $$("button",node).forEach(b=>b.onclick=()=>{$$("button",node).forEach(x=>x.setAttribute("aria-pressed",x===b));on(b.dataset.k)})}
const scenarioChips=(node,cur,on)=>chips(node,SCN().map(k=>({k,label:scnShort[k]||k})),cur,on);

/* ---------- router ---------- */
const ROUTES={};let cleanup=null,current=null;
const NAV=[["dashboard","Dashboard","dash"],["chat","Live Chat","chat"],["arena","Live arena","arena"],["analytics","Analytics","chart"],["experiments","Experiments","flask"]];
function route(path,fn){ROUTES[path]=fn}
async function navigate(){
  const name=(location.hash.replace(/^#\/?/,"")||"dashboard").split("?")[0];const fn=ROUTES[name]||ROUTES.dashboard;
  if(cleanup){try{cleanup()}catch{}cleanup=null}
  $$(".nav a").forEach(a=>a.setAttribute("aria-current",a.dataset.r===name?"page":"false"));
  $("#view").replaceWith(el("main",{id:"view",class:"view"}));
  const nv=$("#view");current=name;document.title=(NAV.find(n=>n[0]===name)?.[1]||"Dashboard")+" | TokenLaxity";scrollTo(0,0);
  try{cleanup=await fn(nv)||null}catch(e){console.error(e);nv.innerHTML=`<div class="card empty">${icon("alert")}<b>Could not load this screen</b><span>${esc(e.message)}</span></div>`}
}
function go(path){location.hash="#/"+path}

/* ---------- boot ---------- */
function boot(){
  document.body.innerHTML=`<div class="app"><nav class="side" aria-label="Main"><a class="logo" href="#/dashboard">${LOGO}TokenLaxity</a>
   <div class="nav">${NAV.map(([r,l,i])=>`<a href="#/${r}" data-r="${r}">${icon(i)}<span>${l}</span></a>`).join("")}</div>
   <div class="bottom"><div class="status"><span id="srv-dot" class="dot"></span><span id="srv-txt">Checking server</span></div>
   <button class="btn" id="theme" aria-label="Toggle theme"></button></div></nav>
   <main id="view" class="view"></main></div><div class="toasts" id="toasts" aria-live="polite"></div><div class="tip" id="tip" role="tooltip"></div>`;
  const themeBtn=()=>{$("#theme").innerHTML=icon(settings.theme==="dark"?"sun":"moon")+(settings.theme==="dark"?"Light theme":"Dark theme")};
  $("#theme").onclick=()=>{settings.theme=settings.theme==="dark"?"light":"dark";saveSettings();themeBtn()};themeBtn();
  addEventListener("hashchange",navigate);pingServer();navigate();
}
