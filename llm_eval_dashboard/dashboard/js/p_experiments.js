/* Experiments Page: browser for the multi-seed simulation benchmark runs (360 main + ablation + sweep) */
route("experiments", async view => {
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
      <div class="sub">Every multi-seed simulation run behind the Analytics numbers</div>
    </div>
  </div>
  <div id="body"></div>`;

  const filtered = () => {
    const t = q.trim().toLowerCase();
    let rows = RUNS.filter(r => r.kind === kind && (scen === "all" || r.scenario === scen) && (sch === "all" || r.scheduler === sch) && (!t || (scnName(r.scenario) + " " + schedLabel(r.scheduler) + " seed " + r.seed).toLowerCase().includes(t)));
    rows.sort((a, b) => { const x = a[sort.k], y = b[sort.k]; return (typeof x === "string" ? x.localeCompare(y) : x - y) * sort.d });
    return rows;
  };

  async function render() {
    const body = $("#body");
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
      <div class="row" style="justify-content:flex-end"><button class="btn" data-close>Close</button><button class="btn primary" id="rp" ${can ? "" : "disabled"} title="${can ? "" : "Only the main benchmark runs have a replay"}">${icon("play")}Open scenario in arena</button></div>`);
    $("#rp", m.el).onclick = () => {
      settings.scenario = r.scenario; saveSettings();
      Store.set("arena_mode", "sim");
      m.close();
      go("arena");
    };
  }

  render();
});
