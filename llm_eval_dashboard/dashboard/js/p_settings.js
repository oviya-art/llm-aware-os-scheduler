route("settings",async view=>{
  const sel=(id,opts,cur)=>`<select id="${id}">${opts.map(([v,l])=>`<option value="${v}" ${String(v)===String(cur)?"selected":""}>${esc(l)}</option>`).join("")}</select>`;
  const draw=()=>{view.innerHTML=`<div class="top"><h1>Settings</h1></div>
   <div class="grid g2"><div class="card"><h3>${icon("sun")}Appearance</h3>
     <div class="setrow"><div><b>Theme</b><span>Dark or light</span></div><div class="chips" id="th"></div></div>
     <div class="setrow"><div><b>Motion</b><span>Animated previews and transitions</span></div><button class="switch" id="mo" role="switch" aria-checked="${settings.motion}" aria-label="Motion"></button></div></div>
    <div class="card"><h3>${icon("arena")}Arena defaults</h3>
     <div class="setrow"><div><b>Scenario</b></div>${sel("ds",SCN().map(k=>[k,scnName(k)]),settings.scenario)}</div>
     <div class="setrow"><div><b>Left scheduler</b></div>${sel("dl",SCHED.map(s=>[s,LABEL[s]]),settings.left)}</div>
     <div class="setrow"><div><b>Right scheduler</b></div>${sel("dr",SCHED.map(s=>[s,LABEL[s]]),settings.right)}</div>
     <div class="setrow"><div><b>Playback speed</b></div>${sel("dsp",[5,15,40,120].map(v=>[v,v+"x"]),settings.speed)}</div></div>
    <div class="card"><h3>${icon("cpu")}Server</h3><div class="setrow"><div><b id="st">Checking</b><span>New workloads need <code>python server.py</code></span></div><button class="btn" id="re">${icon("restart")}Check again</button></div></div>
    <div class="card"><h3>${icon("trash")}Saved data</h3><div class="setrow"><div><b>Run history and preferences</b><span>Stored only in this browser</span></div><button class="btn" id="rs">Reset</button></div></div></div>`;
   chips($("#th"),[{k:"dark",label:"Dark"},{k:"light",label:"Light"}],settings.theme,k=>{settings.theme=k;saveSettings()});
   $("#mo").onclick=e=>{settings.motion=!settings.motion;e.target.setAttribute("aria-checked",settings.motion);saveSettings()};
   [["ds","scenario"],["dl","left"],["dr","right"],["dsp","speed"]].forEach(([id,k])=>$("#"+id).onchange=e=>{settings[k]=k==="speed"?+e.target.value:e.target.value;saveSettings();toast("Saved")});
   const st=async()=>{$("#st").textContent="Checking";const ok=await pingServer();$("#st").textContent=ok?"Server connected":"Server not running"};st();$("#re").onclick=st;
   $("#rs").onclick=()=>{["settings","history","atab","arenaOpen"].forEach(Store.del);Object.assign(settings,DEFAULTS);applyTheme();draw();toast("Reset to defaults")};};
  draw();
});
