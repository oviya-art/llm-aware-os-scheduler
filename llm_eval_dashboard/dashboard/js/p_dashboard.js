/* Dashboard Overview Page: Live Ollama Workload Engine vs Discrete-Time Simulation Scorecard */
route("dashboard", async view => {
  let mode = Store.get("dash_mode", "live"); // "live" | "sim"
  let dead = false;
  let raf = 0;
  let livePollTimer = null;

  view.innerHTML = `
  <div class="row" style="margin-bottom:16px;justify-content:flex-end">
    <div id="dash-mode-chips"></div>
  </div>
  <div id="dash-body"></div>`;

  chips($("#dash-mode-chips"), [
    { k: "live", label: "⚡ Live Ollama Engine" },
    { k: "sim", label: "🧪 Simulation Scorecard" }
  ], mode, m => {
    mode = m;
    Store.set("dash_mode", m);
    switchMode();
  });

  function switchMode() {
    if (dead) return;
    if (livePollTimer) {
      clearInterval(livePollTimer);
      livePollTimer = null;
    }
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
    if (mode === "live") renderLiveDashboard();
    else renderSimDashboard();
  }

  // =========================================================================
  // 1. Live Ollama Overview Mode
  // =========================================================================
  async function renderLiveDashboard() {
    const container = $("#dash-body");
    if (!container || dead) return;

    container.innerHTML = `
    <!-- Hero Section -->
    <section class="hero">
      <div>
        <h1>Real Local Ollama.<br><span>Application-Level Scheduling.</span></h1>
        <p>A deterministic, thread-safe request scheduler for latency-sensitive local AI inference.</p>
        <div class="row">
          <a class="btn primary lg" href="#/arena">${icon("arena")}Open Live Arena</a>
          <a class="btn lg" href="#/chat">${icon("chat")}Live Chat & Queue</a>
          <button class="btn lg" id="btn-dash-burst">${icon("zap")}Run Burst Load</button>
        </div>
      </div>
      <div class="preview" aria-label="Live Ollama engine status">
        <div class="bar">
          <i></i><i></i><i></i>
          <span style="margin-left:8px">Local Ollama Controller (Port 11434)</span>
          <b><span class="live" id="dash-live-dot"></span><span id="dash-live-txt">Connected</span></b>
        </div>
        <div style="padding:18px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
            <span style="font-weight:700;font-size:13px;color:var(--text)">Active Inference Slot</span>
            <span class="pill ok" id="dash-slot-pill">Idle</span>
          </div>
          <div id="dash-active-prompt" style="font-size:13px;color:var(--mute);background:var(--surface2);padding:10px 12px;border-radius:10px;border:1px solid var(--line);min-height:50px">
            No active inference request. Scheduler is ready to dispatch.
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:14px;font-size:12px;color:var(--dim)">
            <span>Active Policy: <b id="dash-active-pol" style="color:var(--brand-2)">FCFS</b></span>
            <span>Queued Tasks: <b id="dash-active-q" style="color:var(--text)">0</b></span>
          </div>
        </div>
      </div>
    </section>

    <!-- Scorecard Header -->
    <div class="top" style="margin:28px 0 16px">
      <div>
        <h2 style="font-size:22px;margin:0">Live Inference Scorecard</h2>
        <span class="sub" style="color:var(--mute)">Source: Real Ollama Measurements (time.perf_counter)</span>
      </div>
      <div class="row" style="margin-left:auto">
        <span class="badge ok" style="padding:5px 12px">
          ${icon("check")}Real Metrics
        </span>
      </div>
    </div>

    <!-- Live Stat Cards -->
    <div class="grid g4" id="live-dash-stats" style="margin-bottom:20px">
      <div class="card stat">
        <div class="lbl">${icon("cpu")}Completed Requests</div>
        <div class="val" id="d-completed-reqs">0</div>
        <div class="delta" id="d-reqs-sub" style="font-size:12px;color:var(--dim)">0 total submitted</div>
      </div>
      <div class="card stat">
        <div class="lbl">${icon("zap")}Mean TTFO</div>
        <div class="val"><span id="d-mean-ttfo">0</span><small>ms</small></div>
        <div class="delta" style="font-size:12px;color:var(--mute)">Median: <b id="d-med-ttfo">0</b> ms</div>
      </div>
      <div class="card stat">
        <div class="lbl">${icon("clock")}Mean Queue Wait</div>
        <div class="val"><span id="d-mean-wait">0</span><small>ms</small></div>
        <div class="delta" style="font-size:12px;color:var(--mute)">Median: <b id="d-med-wait">0</b> ms</div>
      </div>
      <div class="card stat">
        <div class="lbl">${icon("check")}SLA Attainment</div>
        <div class="val" id="d-sla-attain" style="color:var(--ok)">100%</div>
        <div class="delta" id="d-sla-sub" style="font-size:12px;color:var(--mute)">0 missed deadlines</div>
      </div>
    </div>

    <!-- Leaderboard and Recent Live Runs -->
    <div class="grid g21" style="margin-top:18px">
      <!-- Policy Distribution -->
      <div class="card">
        <h3>${icon("sliders")}Live Policy Distribution <small>Completed requests by policy</small></h3>
        <div id="dash-policy-bars" style="display:flex;flex-direction:column;gap:12px;margin-top:14px">
          <div style="color:var(--dim);font-size:13px;text-align:center;padding:24px 0">Waiting for live request executions...</div>
        </div>
      </div>

      <!-- Recent Live Requests List -->
      <div class="card">
        <h3>${icon("clock")}Recent Completed Requests <small id="d-recent-count">Last runs</small></h3>
        <div id="dash-recent-list" style="display:flex;flex-direction:column;gap:8px;margin-top:10px">
          <div style="color:var(--dim);font-size:13px;text-align:center;padding:24px 0">No recent completed requests.</div>
        </div>
      </div>
    </div>`;

    // Wire up Burst Load button
    const btnBurst = $("#btn-dash-burst");
    if (btnBurst) {
      btnBurst.onclick = async () => {
        btnBurst.disabled = true;
        btnBurst.textContent = "Injecting 4 Prompts...";
        try {
          const res = await fetch("/api/live/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
          });
          if (res.ok) {
            toast("Injected 4 benchmark test prompts into scheduler queue");
            fetchLiveData();
          }
        } catch (e) {
          toast(e.message, "err");
        } finally {
          btnBurst.disabled = false;
          btnBurst.innerHTML = `${icon("zap")}Run Burst Load`;
        }
      };
    }

    async function fetchLiveData() {
      if (dead || mode !== "live") return;
      try {
        const [mRes, tlRes] = await Promise.all([
          fetch("/api/live/metrics", { cache: "no-store" }),
          fetch("/api/live/timeline", { cache: "no-store" })
        ]);

        if (mRes.ok && tlRes.ok) {
          const m = await mRes.json();
          const tl = await tlRes.json();

          // Update Scorecards
          $("#d-completed-reqs").textContent = m.completed_requests || 0;
          $("#d-reqs-sub").textContent = `${m.total_requests || 0} total submitted, ${m.failed_requests || 0} failed`;
          $("#d-mean-ttfo").textContent = fmt(m.mean_ttfo_ms, 0);
          $("#d-med-ttfo").textContent = fmt(m.median_ttfo_ms, 0);
          $("#d-mean-wait").textContent = fmt(m.mean_wait_ms, 0);
          $("#d-med-wait").textContent = fmt(m.median_wait_ms, 0);

          const slaEl = $("#d-sla-attain");
          if (slaEl) {
            slaEl.textContent = fmt(m.deadline_attainment_pct, 1) + "%";
            slaEl.style.color = (m.deadline_attainment_pct >= 80 || m.completed_requests === 0) ? "var(--ok)" : "var(--late)";
          }
          const slaSub = $("#d-sla-sub");
          if (slaSub) {
            const missedCount = (m.completed_requests || 0) - Math.round(((m.completed_requests || 0) * (m.deadline_attainment_pct || 100)) / 100);
            slaSub.textContent = `${Math.max(0, missedCount)} missed SLA`;
          }

          // Update Hero Status
          const dot = $("#dash-live-dot"), txt = $("#dash-live-txt");
          if (dot && txt) {
            dot.className = "live";
            txt.textContent = "Ollama Active";
          }
          const pill = $("#dash-slot-pill");
          if (pill) {
            if (tl.is_busy && tl.active_request) {
              pill.className = "pill bad";
              pill.textContent = "Inferencing";
            } else {
              pill.className = "pill ok";
              pill.textContent = "Idle";
            }
          }
          const promptBox = $("#dash-active-prompt");
          if (promptBox) {
            if (tl.is_busy && tl.active_request) {
              promptBox.innerHTML = `
                <div style="font-weight:700;color:var(--text);margin-bottom:4px">
                  ${esc(tl.active_request.request_id)} <span class="badge" style="text-transform:capitalize">${esc(tl.active_request.priority)}</span>
                </div>
                <div>${esc(tl.active_request.prompt_preview || 'Generating...')}</div>`;
            } else {
              promptBox.textContent = "No active request. Scheduler queue is ready.";
            }
          }
          $("#dash-active-pol").textContent = (LABEL[tl.active_policy] || tl.active_policy || "FCFS").toUpperCase();
          $("#dash-active-q").textContent = tl.queued_count || 0;

          // Update Policy Bars
          renderPolicyBars(m.runs_by_policy || {}, m.completed_requests || 0);

          // Update Recent List
          renderRecentList(m.history || []);
        }
      } catch (e) {
        console.warn("Live dashboard poll error:", e);
      }
    }

    function renderPolicyBars(byPolicy, totalCompleted) {
      const el = $("#dash-policy-bars");
      if (!el) return;
      const keys = ["fcfs", "priority", "edf", "token_laxity"];
      const hasAny = keys.some(k => byPolicy[k] && byPolicy[k].sample_size > 0);
      if (!hasAny) {
        el.innerHTML = `<div style="color:var(--dim);font-size:13px;text-align:center;padding:24px 0">No requests executed yet. Run a workload to see policy statistics.</div>`;
        return;
      }

      el.innerHTML = keys.map(k => {
        const d = byPolicy[k] || { sample_size: 0, deadline_met_pct: 100 };
        const count = d.sample_size || 0;
        const met = d.deadline_met_pct || 0;
        const share = totalCompleted > 0 ? Math.round((count / totalCompleted) * 100) : 0;
        return `
        <div>
          <div style="display:flex;justify-content:space-between;font-size:12.5px;font-weight:700;margin-bottom:4px">
            <span><i class="sw" style="background:${col(k)}"></i>${LABEL[k]}</span>
            <span style="color:var(--mute)">${count} runs (${share}%) • <b style="color:var(--ok)">${met}% met</b></span>
          </div>
          <div style="height:8px;background:var(--line);border-radius:4px;overflow:hidden">
            <div style="width:${share}%;background:${col(k)};height:100%;border-radius:4px"></div>
          </div>
        </div>`;
      }).join("");
    }

    function renderRecentList(history) {
      const el = $("#dash-recent-list");
      if (!el) return;
      if (!history || history.length === 0) {
        el.innerHTML = `<div style="color:var(--dim);font-size:13px;text-align:center;padding:24px 0">No completed requests yet.</div>`;
        return;
      }
      el.innerHTML = history.slice(0, 5).map(r => {
        const ok = !r.deadline_missed;
        const dur = (Number(r.queue_wait_ms || 0) + Number(r.generation_duration_ms || 0));
        return `
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:var(--surface2);border-radius:8px;border:1px solid var(--line);font-size:12px">
          <div>
            <div style="font-weight:700;color:var(--text)">${esc(r.request_id)}</div>
            <div style="color:var(--mute);font-size:11px">${esc(LABEL[r.policy] || r.policy)} • ${fmt(dur, 0)} ms</div>
          </div>
          <span class="badge ${ok ? 'ok' : 'bad'}">${ok ? '✓ Met' : '✕ Missed'}</span>
        </div>`;
      }).join("");
    }

    await fetchLiveData();
    livePollTimer = setInterval(fetchLiveData, 2000);
  }

  // =========================================================================
  // 2. Discrete-Time Simulation Scorecard (Teammate implementation preserved)
  // =========================================================================
  async function renderSimDashboard() {
    const container = $("#dash-body");
    if (!container || dead) return;

    let cur = settings.scenario;
    container.innerHTML = `
    <section class="hero">
      <div>
        <h1>Smooth tokens.<br><span>Even when the CPU isn't.</span></h1>
        <p>A scheduler that treats every token as a deadline.</p>
        <div class="row">
          <a class="btn primary lg" href="#/arena">${icon("play")}Watch it live</a>
          <button class="btn lg" id="new-run">${icon("plus")}New run</button>
        </div>
      </div>
      <div class="preview" aria-label="Live preview of two schedulers">
        <div class="bar"><i></i><i></i><i></i><span style="margin-left:8px">Heavy load, 4 cores</span><b><span class="live"></span>replaying</b></div>
        <div id="pv"><div class="empty" style="padding:70px 20px">${icon("cpu")}<span>Loading replay</span></div></div>
      </div>
    </section>

    <div class="top" style="margin:26px 0 16px">
      <h2 style="font-size:22px;margin-right:auto">Simulation Scorecard</h2>
      <div id="scn"></div>
    </div>
    <div class="grid g4" id="stats"></div>
    <div class="grid g21" style="margin-top:18px">
      <div class="card"><h3>${icon("zap")}Requests on time<small id="lb-sub"></small></h3><div class="rank" id="rank"></div></div>
      <div class="card"><h3>${icon("gauge")}SLA misses by scenario<small>% of requests</small></h3><div id="heat"></div></div>
    </div>`;

    $("#new-run").onclick = () => go("arena");

    const stat = (ic, label, val, unit, delta, good, spark) => `<div class="card stat"><div class="lbl">${icon(ic)}${label}</div><div class="val">${val}<small>${unit}</small></div>
      <div class="delta"><span class="badge ${good ? "ok" : "warn"}">${delta}</span></div><div class="spark" data-sp="${spark}"></div></div>`;

    function drawStats() {
      const T = "token_laxity", B = SCHED.filter(s => s !== T), best = (m, hi) => B.reduce((a, s) => (hi ? S(cur, s, m) > S(cur, a, m) : S(cur, s, m) < S(cur, a, m)) ? s : a, B[0]);
      const cards = [
        ["clock", "On time", 100 - S(cur, T, "request_miss_rate"), "%", "request_miss_rate", true, d => 100 - d],
        ["zap", "Tokens late", S(cur, T, "token_miss_rate"), "%", "token_miss_rate", false],
        ["gauge", "p99 gap", S(cur, T, "p99_itl_ms"), "ms", "p99_itl_ms", false],
        ["cpu", "Background cost", S(cur, T, "bg_mean_slowdown"), "x", "bg_mean_slowdown", false]];
      $("#stats").innerHTML = cards.map(([ic, l, v, u, m, hi], i) => {
        const bs = best(m, false), bv = m === "request_miss_rate" ? 100 - S(cur, bs, m) : S(cur, bs, m);
        const hiBetter = m === "request_miss_rate", bb = hiBetter ? B.reduce((a, s) => S(cur, s, m) < S(cur, a, m) ? s : a, B[0]) : bs, bbv = hiBetter ? 100 - S(cur, bb, m) : S(cur, bb, m);
        const diff = v - bbv, good = hiBetter ? diff >= 0 : diff <= 0;
        return stat(ic, l, fmt(v, m === "p99_itl_ms" ? 0 : 1), u, `${diff >= 0 ? "+" : ""}${fmt(diff, m === "p99_itl_ms" ? 0 : 1)}${u === "%" ? " pts" : u} vs ${SHORT[bb]}`, good, i)
      }).join("");
      const ms = ["request_miss_rate", "token_miss_rate", "p99_itl_ms", "bg_mean_slowdown"];
      $$("[data-sp]").forEach(n => { const m = ms[+n.dataset.sp], pts = SCN().map(k => m === "request_miss_rate" ? 100 - S(k, T, m) : S(k, T, m)); spark(n, pts, col(T), { w: 260, h: 40, hi: SCN().indexOf(cur) }) });
      // leaderboard
      const rows = SCHED.map(s => ({ s, ok: 100 - S(cur, s, "request_miss_rate") })).sort((a, b) => b.ok - a.ok);
      $("#lb-sub").textContent = scnName(cur);
      $("#rank").innerHTML = rows.map((r, i) => `<div class="r ${r.s === T ? "me" : ""}"><span class="pos">${i + 1}</span><span><i class="sw" style="background:${col(r.s)}"></i>${LABEL[r.s]}</span><span class="track"><i style="width:0;background:${col(r.s)}" data-w="${r.ok}"></i></span><span class="v">${fmt(r.ok, 0)}%</span></div>`).join("");
      requestAnimationFrame(() => $$("#rank .track i").forEach(i => i.style.width = i.dataset.w + "%"));
    }

    scenarioChips($("#scn"), cur, k => { cur = k; settings.scenario = k; saveSettings(); drawStats() });
    drawStats(); heat($("#heat"), "request_miss_rate", { w: 560 });

    try {
      const R = await replay("heavy"); if (dead || mode !== "sim") return;
      const A = "token_laxity", Bk = "cfs";
      $("#pv").innerHTML = [A, Bk].map(k => `<div class="lane"><header><span><i class="sw" style="background:${col(k)}"></i>${LABEL[k]}</span><span class="pill" id="pl-${k}"></span></header><canvas data-k="${k}" height="130"></canvas></div>`).join("") + `<div style="height:10px"></div>`;
      let t = 14000, last = performance.now();
      const loop = ts => {
        if (dead || mode !== "sim") return; const dt = ts - last; last = ts; t += dt * (reduced() ? 0 : 6); if (t > 58000) t = 14000;
        [A, Bk].forEach(k => {
          const c = $(`canvas[data-k=${k}]`); Viz.timeline(c, R, k, t, { win: 5000, rowH: 24, labels: false }); const n = Viz.counts(R, k, t); const p = $("#pl-" + k);
          p.className = "pill " + (n.pct > 10 ? "bad" : "ok"); p.textContent = fmt(n.pct, 1) + "% tokens late"
        });
        raf = requestAnimationFrame(loop)
      };
      if (reduced()) { [A, Bk].forEach(k => { Viz.timeline($(`canvas[data-k=${k}]`), R, k, t, { win: 5000, rowH: 24, labels: false }); const n = Viz.counts(R, k, t); const p = $("#pl-" + k); p.className = "pill " + (n.pct > 10 ? "bad" : "ok"); p.textContent = fmt(n.pct, 1) + "% tokens late" }) }
      else raf = requestAnimationFrame(loop);
    } catch (e) { $("#pv").innerHTML = `<div class="empty">${icon("alert")}<b>Preview unavailable</b><span>${esc(e.message)}</span></div>` }
  }

  // Initial render
  switchMode();

  return () => {
    dead = true;
    if (livePollTimer) clearInterval(livePollTimer);
    if (raf) cancelAnimationFrame(raf);
  };
});
