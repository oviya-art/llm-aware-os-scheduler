/* Experiments Page: Benchmark Runs, Simulation Workloads, and Live Ollama Experiments */
route("experiments", async view => {
  let tab = Store.get("exp_tab", "runs"); // "runs" | "mine" | "live"
  let q = "", kind = "main", scen = "all", sch = "all", sort = { k: "request_miss_rate", d: 1 }, page = 0;
  const PER = 12;

  const COLS = [
    ["scenario", "Scenario", r => esc(scnName(r.scenario))],
    ["seed", "Seed", r => r.seed],
    ["scheduler", "Scheduler", r => schedLabel(r.scheduler)],
    ["request_miss_rate", "SLA misses", r => fmt(r.request_miss_rate, 1) + "%"],
    ["token_miss_rate", "Tokens late", r => fmt(r.token_miss_rate, 2) + "%"],
    ["p99_itl_ms", "p99 gap", r => fmt(r.p99_itl_ms, 0) + " ms"],
    ["bg_mean_slowdown", "Background", r => fmt(r.bg_mean_slowdown, 1) + "x"],
    ["jain_progress", "Fairness", r => fmt(r.jain_progress, 3)],
    ["offered_load", "Load", r => fmt(r.offered_load, 2)]
  ];
  const schedLabel = s => LABEL[s] || s.replace("util_bound=", "Bound ");

  view.innerHTML = `
  <div class="top">
    <div>
      <h1 style="display:flex;align-items:center;gap:10px">${icon("flask")}Experiments</h1>
      <div class="sub">Repeatable multi-seed benchmark suites and live Ollama experiment batches</div>
    </div>
    <div id="tabs" style="margin-left:auto"></div>
    <button class="btn primary" id="new">${icon("plus")}New workload</button>
  </div>
  <div id="body"></div>`;

  chips($("#tabs"), [
    { k: "runs", label: "Benchmark runs" },
    { k: "mine", label: "Simulation runs" },
    { k: "live", label: "⚡ Live Ollama Experiments" }
  ], tab, k => {
    tab = k;
    Store.set("exp_tab", k);
    render();
  });

  $("#new").onclick = () => {
    if (tab === "live") openLiveBatchModal();
    else newRunModal(d => {
      (window.REPLAY = window.REPLAY || {}).custom = d;
      Store.set("arenaOpen", { custom: true });
      Store.set("arena_mode", "sim");
      go("arena");
    });
  };

  const filtered = () => {
    const t = q.trim().toLowerCase();
    let rows = RUNS.filter(r => r.kind === kind && (scen === "all" || r.scenario === scen) && (sch === "all" || r.scheduler === sch) && (!t || (scnName(r.scenario) + " " + schedLabel(r.scheduler) + " seed " + r.seed).toLowerCase().includes(t)));
    rows.sort((a, b) => { const x = a[sort.k], y = b[sort.k]; return (typeof x === "string" ? x.localeCompare(y) : x - y) * sort.d });
    return rows;
  };

  async function render() {
    const body = $("#body");
    const newBtn = $("#new");
    if (newBtn) {
      newBtn.innerHTML = tab === "live" ? `${icon("play")}Run Live Batch` : `${icon("plus")}New workload`;
    }

    // Tab 1: Live Ollama Experiments
    if (tab === "live") {
      body.innerHTML = `
      <div class="row" style="margin-bottom:14px;justify-content:space-between">
        <div class="row">
          <span class="badge ok">${icon("check")}Live Ollama Experiments</span>
          <span style="font-size:13px;color:var(--mute)">Repeatable batches executed on local Ollama</span>
        </div>
        <div class="row">
          <a class="btn" href="/api/live/export?format=csv" download="live_experiments.csv">${icon("dl")}Export CSV</a>
          <a class="btn" href="/api/live/export?format=json" download="live_experiments.json">${icon("dl")}Export JSON</a>
          <button class="btn" id="btn-ref-exp">${icon("restart")}Refresh</button>
        </div>
      </div>

      <div class="card" style="margin-bottom:18px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:10px">
          <h3 style="margin:0">${icon("flask")}Live Evaluation Runs History</h3>
          <span id="lbl-live-exp-count" style="font-size:13px;color:var(--mute)">Loading runs...</span>
        </div>
        <div class="scroll">
          <table class="tbl" id="tbl-live-experiments">
            <thead>
              <tr>
                <th>Sub #</th>
                <th>Disp #</th>
                <th>Request ID</th>
                <th>Policy</th>
                <th>Priority</th>
                <th>Deadline SLA</th>
                <th>Queue Wait</th>
                <th>TTFO</th>
                <th>Duration</th>
                <th>SLA Met?</th>
                <th>Timestamp</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr><td colspan="12" style="text-align:center;color:var(--dim)">Fetching live experiment runs...</td></tr>
            </tbody>
          </table>
        </div>
      </div>`;

      const btnRef = $("#btn-ref-exp");
      if (btnRef) btnRef.onclick = () => loadLiveExperiments();

      await loadLiveExperiments();
      return;
    }

    // Tab 2: Saved Local Simulation Runs
    if (tab === "mine") {
      const h = Store.get("history", []);
      body.innerHTML = h.length ? `
        <div class="card">
          <h3>${icon("clock")}Your workloads<small>saved in this browser</small>
            <button class="btn" id="clr" style="margin-left:12px">${icon("trash")}Clear</button>
          </h3>
          <div class="scroll">
            <table class="tbl">
              <thead><tr><th>When</th><th>Setup</th><th>TLS misses</th><th>Best baseline</th><th></th></tr></thead>
              <tbody>${h.map(x => `<tr>
                <td>${new Date(x.ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
                <td>${x.params.cores} cores, ${x.params.requests} requests, ${x.params.rps}/s, ${esc(x.params.background.replace("_load", ""))}, seed ${x.params.seed}</td>
                <td><span class="badge ${x.tls <= x.bestVal ? "ok" : "warn"}">${fmt(x.tls, 0)}%</span></td>
                <td>${SHORT[x.best]} ${fmt(x.bestVal, 0)}%</td>
                <td><button class="btn" data-id="${x.id}">${icon("play")}Replay</button></td>
              </tr>`).join("")}</tbody>
            </table>
          </div>
        </div>` :
        `<div class="card empty">${icon("flask")}<b>No simulation runs yet</b><span>Run a custom workload from Arena and it shows up here.</span><button class="btn primary" id="e-new">${icon("plus")}New workload</button></div>`;

      $("#clr") && ($("#clr").onclick = () => { Store.del("history"); render(); toast("History cleared") });
      $("#e-new") && ($("#e-new").onclick = () => $("#new").click());
      $$("button[data-id]", body).forEach(b => b.onclick = async () => {
        const x = h.find(z => String(z.id) === b.dataset.id); b.disabled = true;
        try {
          const d = await apiRun(x.params); d.meta.title = "Custom run"; (window.REPLAY = window.REPLAY || {}).custom = d;
          Store.set("arenaOpen", { custom: true });
          Store.set("arena_mode", "sim");
          go("arena");
        } catch (e) {
          b.disabled = false;
          toast(serverUp === false ? "Start the server to replay: python server.py" : e.message, "err");
        }
      });
      return;
    }

    // Tab 3: Synthetic Multi-Seed Benchmark Runs
    const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / PER)); page = Math.min(page, pages - 1);
    const schOpts = [...new Set(RUNS.filter(r => r.kind === kind).map(r => r.scheduler))];
    body.innerHTML = `<div class="row" style="margin-bottom:14px">
      <div style="position:relative"><span style="position:absolute;left:11px;top:9px;color:var(--dim)">${icon("search")}</span><input type="search" id="q" placeholder="Search runs" value="${esc(q)}" style="padding-left:38px;width:230px" aria-label="Search runs"></div>
      <div class="chips" id="kind"></div>
      <select id="scen" aria-label="Scenario"><option value="all">All scenarios</option>${SCN().map(k => `<option value="${k}" ${k === scen ? "selected" : ""}>${esc(scnName(k))}</option>`).join("")}</select>
      <select id="sch" aria-label="Scheduler"><option value="all">All schedulers</option>${schOpts.map(s => `<option value="${s}" ${s === sch ? "selected" : ""}>${esc(schedLabel(s))}</option>`).join("")}</select>
      <span class="sub" style="margin-left:auto;color:var(--mute)">${rows.length} runs</span>
      <button class="btn" id="csv">${icon("dl")}Export CSV</button>
    </div>
    <div class="card" style="padding:8px 12px"><div class="scroll"><table class="tbl"><thead><tr>${COLS.map(c => `<th class="s" data-k="${c[0]}">${c[1]}${sort.k === c[0] ? (sort.d > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(page * PER, page * PER + PER).map((r, i) => `<tr class="click ${r.scheduler === "token_laxity" ? "me" : ""}" data-i="${page * PER + i}" tabindex="0">${COLS.map(c => `<td>${c[0] === "scheduler" ? `<i class="sw" style="background:${col(r.scheduler)}"></i>` : ""}${c[2](r)}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="9"><div class="empty">${icon("search")}<b>No runs match</b><span>Try clearing a filter.</span></div></td></tr>`}</tbody></table></div>
    <div class="pager"><button class="btn icon" id="pv" ${page === 0 ? "disabled" : ""} aria-label="Previous page">${icon("chev").replace('<svg', '<svg style="transform:rotate(90deg)"')}</button>Page ${page + 1} of ${pages}<button class="btn icon" id="nx" ${page >= pages - 1 ? "disabled" : ""} aria-label="Next page">${icon("chev").replace('<svg', '<svg style="transform:rotate(-90deg)"')}</button></div></div>`;

    chips($("#kind"), [{ k: "main", label: "Benchmark" }, { k: "ablation", label: "Ablation" }, { k: "sweep", label: "Sweep" }], kind, k => { kind = k; sch = "all"; page = 0; render() });
    $("#q").oninput = e => { q = e.target.value; page = 0; const p = e.target.selectionStart; render(); const n = $("#q"); n.focus(); n.setSelectionRange(p, p) };
    $("#scen").onchange = e => { scen = e.target.value; page = 0; render() }; $("#sch").onchange = e => { sch = e.target.value; page = 0; render() };
    $$("th.s", body).forEach(th => th.onclick = () => { const k = th.dataset.k; sort = sort.k === k ? { k, d: -sort.d } : { k, d: 1 }; render() });
    $("#pv").onclick = () => { page--; render() }; $("#nx").onclick = () => { page++; render() };
    $("#csv").onclick = () => {
      const keys = Object.keys(rows[0] || {}); if (!keys.length) { toast("Nothing to export", "err"); return }
      const csv = [keys.join(","), ...rows.map(r => keys.map(k => r[k]).join(","))].join("\n");
      const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "tokenlaxity_runs.csv" }); document.body.appendChild(a); a.click(); a.remove(); toast(`Exported ${rows.length} runs`);
    };
    $$("tr.click", body).forEach(tr => { const open = () => detail(rows[+tr.dataset.i]); tr.onclick = open; tr.onkeydown = e => { if (e.key === "Enter") open() } });
  }

  async function loadLiveExperiments() {
    try {
      const res = await fetch("/api/live/metrics", { cache: "no-store" });
      if (!res.ok) return;
      const m = await res.json();
      const runs = m.history || [];
      const lbl = $("#lbl-live-exp-count");
      if (lbl) lbl.textContent = `${runs.length} recorded runs`;

      const tbody = $("#tbl-live-experiments tbody");
      if (!tbody) return;
      if (runs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;color:var(--dim);padding:30px 0">No live experiments recorded yet. Click "Run Live Batch" to submit a repeatable prompt suite.</td></tr>`;
        return;
      }

      tbody.innerHTML = runs.map((r, i) => {
        const ok = !r.deadline_missed;
        const prioCol = r.priority === "high" ? "#D58CFF" : (r.priority === "low" ? "#E39BFF" : "#36D6C3");
        return `
        <tr>
          <td><span class="badge">#${r.arrival_index != null ? r.arrival_index : i + 1}</span></td>
          <td><span class="badge" style="background:rgba(124,107,255,.2);color:var(--brand-2)">#${r.dispatch_index != null ? r.dispatch_index : '–'}</span></td>
          <td><code>${esc(r.request_id)}</code></td>
          <td><b>${esc(LABEL[r.policy] || r.policy)}</b></td>
          <td><span class="badge" style="background:${prioCol}22;color:${prioCol};text-transform:capitalize">${esc(r.priority)}</span></td>
          <td>${fmt(r.deadline_ms, 0)} ms</td>
          <td>${fmt(r.queue_wait_ms, 0)} ms</td>
          <td>${fmt(r.ttfo_ms, 0)} ms</td>
          <td>${fmt(r.generation_duration_ms, 0)} ms</td>
          <td><span class="badge ${ok ? 'ok' : 'bad'}">${ok ? '✓ Met' : '✕ Missed'}</span></td>
          <td style="color:var(--dim);font-size:12px">${esc(r.timestamp)}</td>
          <td><button class="btn" style="padding:3px 8px;font-size:11px" onclick="openLiveRunDetail('${esc(r.request_id)}')">Detail</button></td>
        </tr>`;
      }).join("");

      // Window helper for live run detail
      window.openLiveRunDetail = rid => {
        const r = runs.find(x => x.request_id === rid);
        if (!r) return;
        const items = [
          ["Request ID", r.request_id],
          ["Policy", LABEL[r.policy] || r.policy],
          ["Priority", r.priority],
          ["Model", r.model || "llama3.2:latest"],
          ["Queue Wait", fmt(r.queue_wait_ms, 0) + " ms"],
          ["TTFO", fmt(r.ttfo_ms, 0) + " ms"],
          ["Avg ICL", fmt(r.average_icl_ms, 1) + " ms"],
          ["P95 ICL", fmt(r.p95_icl_ms, 1) + " ms"],
          ["Generation Duration", fmt(r.generation_duration_ms, 0) + " ms"],
          ["Target SLA", fmt(r.deadline_ms, 0) + " ms"],
          ["SLA Status", r.deadline_missed ? "Missed SLA" : "Met SLA"],
          ["Total Tokens / Chunks", r.chunks || "–"]
        ];
        modal(`
          <button class="btn icon x" data-close aria-label="Close">${icon("x")}</button>
          <h2>Live Request Measurement</h2>
          <p style="color:var(--mute);margin:0 0 10px">${esc(r.prompt_preview || r.request_id)}</p>
          <div class="kv">${items.map(([a, b]) => `<div><small>${a}</small><b>${b}</b></div>`).join("")}</div>
          <div class="row" style="justify-content:flex-end;margin-top:14px">
            <button class="btn" data-close>Close</button>
          </div>
        `);
      };

    } catch (e) {
      console.warn("Error loading live experiments:", e);
    }
  }

  function openLiveBatchModal() {
    const m = modal(`
      <button class="btn icon x" data-close aria-label="Close">${icon("x")}</button>
      <h2>Run Live Ollama Batch Experiment</h2>
      <p style="color:var(--mute);margin:0 0 16px">Dispatches a repeatable prompt workload to local Ollama under your chosen scheduling policy.</p>
      <form id="frm-live-batch" class="grid" style="gap:14px">
        <label class="fld">
          Experiment Name
          <input type="text" name="name" value="Batch_${Date.now()}" required>
        </label>
        <div class="grid g2">
          <label class="fld">
            Scheduling Policy
            <select name="policy">
              <option value="fcfs">FCFS (FIFO)</option>
              <option value="priority" selected>Priority (High > Low)</option>
              <option value="edf">EDF (Earliest Deadline)</option>
              <option value="token_laxity">Token Laxity (TLS)</option>
            </select>
          </label>
          <label class="fld">
            Workload Preset
            <select name="preset">
              <option value="mixed" selected>Standard Mixed (4 prompts, mixed priority & SLAs)</option>
              <option value="urgent">Urgent Burst (3 high-priority, tight deadlines)</option>
            </select>
          </label>
        </div>
        <div class="row" style="justify-content:flex-end;margin-top:10px">
          <button type="button" class="btn" data-close>Cancel</button>
          <button type="submit" class="btn primary" id="btn-run-batch-go">${icon("play")}Run Live Experiment</button>
        </div>
      </form>
    `);

    $("#frm-live-batch", m.el).onsubmit = async e => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      const btn = $("#btn-run-batch-go", m.el);
      btn.disabled = true;
      btn.textContent = "Submitting Batch...";

      let customPrompts = null;
      if (f.preset === "urgent") {
        customPrompts = [
          { prompt: "List 3 scheduling algorithms (names only).", priority: "high", deadline_ms: 3000.0 },
          { prompt: "What is an interrupt in one sentence?", priority: "high", deadline_ms: 2500.0 },
          { prompt: "Define process state transition.", priority: "normal", deadline_ms: 6000.0 }
        ];
      }

      try {
        const res = await fetch("/api/live/batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ policy: f.policy, prompts: customPrompts })
        });
        const data = await res.json();
        if (res.ok) {
          toast(`Dispatched live experiment "${f.name}" with ${data.submitted_count} requests`);
          m.close();
          loadLiveExperiments();
        } else {
          toast(data.error || "Batch submission failed", "err");
          btn.disabled = false;
        }
      } catch (err) {
        toast("Error: " + err.message, "err");
        btn.disabled = false;
      }
    };
  }

  function detail(r) {
    const items = [
      ["SLA misses", fmt(r.request_miss_rate, 1) + "%"],
      ["Tokens late", fmt(r.token_miss_rate, 2) + "%"],
      ["Avg gap", fmt(r.avg_token_latency_ms, 1) + " ms"],
      ["p99 gap", fmt(r.p99_itl_ms, 0) + " ms"],
      ["First token", fmt(r.mean_ttft_ms, 0) + " ms"],
      ["Throughput", fmt(r.throughput_tok_s, 1) + " tok/s"],
      ["Fairness", fmt(r.jain_progress, 3)],
      ["Background", fmt(r.bg_mean_slowdown, 1) + "x"],
      ["Preemptions", r.preemptions],
      ["Switch cost", fmt(r.switch_overhead_pct, 2) + "%"],
      ["CPU busy", fmt(r.cpu_util, 0) + "%"],
      ["Makespan", fmt(r.makespan_s, 0) + " s"]
    ];
    const can = r.kind === "main";
    const m = modal(`<button class="btn icon x" data-close aria-label="Close">${icon("x")}</button><h2><i class="sw" style="background:${col(r.scheduler)}"></i>${esc(schedLabel(r.scheduler))}</h2><p style="color:var(--mute);margin:0">${esc(scnName(r.scenario))}, seed ${r.seed}, load ${fmt(r.offered_load, 2)}</p>
      <div class="kv">${items.map(([a, b]) => `<div><small>${a}</small><b>${b}</b></div>`).join("")}</div>
      <div class="row" style="justify-content:flex-end"><button class="btn" data-close>Close</button><button class="btn primary" id="rp" ${can ? "" : "disabled"} title="${can ? "" : "Only the main benchmark runs can be replayed"}">${icon("play")}Replay in arena</button></div>`);
    $("#rp", m.el).onclick = () => {
      Store.set("arenaOpen", { scenario: r.scenario, seed: r.seed });
      Store.set("arena_mode", "sim");
      m.close();
      go("arena");
    };
  }

  render();
});
