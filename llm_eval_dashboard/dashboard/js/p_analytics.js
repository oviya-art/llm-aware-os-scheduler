/* Analytics Page: Real Ollama Scheduling Evaluation vs Discrete-Time Simulation Benchmarks */
route("analytics", async view => {
  let source = Store.get("analytics_source", "live"); // "live" | "sim"
  let dead = false;
  let livePollTimer = null;

  view.innerHTML = `
  <div class="top">
    <div>
      <h1 style="display:flex;align-items:center;gap:10px">
        ${icon("chart")}Evaluation Analytics
      </h1>
      <div class="sub" id="analytics-sub-label">Real local Ollama latency evaluation & policy comparisons</div>
    </div>
    <div class="row" style="margin-left:auto">
      <div id="src-chips"></div>
    </div>
  </div>
  <div id="analytics-body"></div>`;

  chips($("#src-chips"), [
    { k: "live", label: "⚡ Live Ollama Evaluation" },
    { k: "sim", label: "🧪 Simulation Benchmarks" }
  ], source, s => {
    source = s;
    Store.set("analytics_source", s);
    switchSource();
  });

  function switchSource() {
    if (dead) return;
    if (livePollTimer) {
      clearInterval(livePollTimer);
      livePollTimer = null;
    }
    const sub = $("#analytics-sub-label");
    if (source === "live") {
      if (sub) sub.textContent = "Real local Ollama latency evaluation & policy comparisons";
      renderLiveAnalytics();
    } else {
      if (sub) sub.textContent = "Discrete-time multi-seed benchmark simulation data";
      renderSimAnalytics();
    }
  }

  // =========================================================================
  // 1. Live Ollama Analytics View
  // =========================================================================
  async function renderLiveAnalytics() {
    const container = $("#analytics-body");
    if (!container || dead) return;

    container.innerHTML = `
    <!-- Top Actions Bar -->
    <div class="row" style="margin-bottom:18px;justify-content:space-between">
      <span class="badge ok" style="padding:6px 12px;font-size:13px">
        ${icon("check")}Source: Real Ollama Inference (Application-Level Scheduling)
      </span>
      <div class="row">
        <a class="btn" id="btn-export-csv" href="/api/live/export?format=csv" download="live_ollama_metrics.csv">
          ${icon("dl")}Export CSV
        </a>
        <a class="btn" id="btn-export-json" href="/api/live/export?format=json" download="live_ollama_metrics.json">
          ${icon("dl")}Export JSON
        </a>
        <button class="btn icon" id="btn-refresh-analytics" title="Refresh metrics">
          ${icon("restart")}
        </button>
      </div>
    </div>

    <!-- Five Key Metric Scorecards -->
    <div class="grid g4" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr));margin-bottom:20px">
      <!-- Requests Volume -->
      <div class="card stat">
        <div class="lbl">${icon("cpu")}Total Requests</div>
        <div class="val" id="m-total-reqs">0</div>
        <div class="delta" id="m-reqs-sub" style="font-size:12px;color:var(--dim)">0 completed, 0 failed</div>
      </div>

      <!-- Queue Wait Time -->
      <div class="card stat">
        <div class="lbl">${icon("clock")}Queue Wait Time</div>
        <div class="val"><span id="m-mean-wait">0</span><small>ms</small></div>
        <div class="delta" style="font-size:12px;color:var(--mute)">Median: <b id="m-med-wait">0</b> ms</div>
      </div>

      <!-- Time To First Output (TTFO) -->
      <div class="card stat">
        <div class="lbl">${icon("zap")}Time To First Output</div>
        <div class="val"><span id="m-mean-ttfo">0</span><small>ms</small></div>
        <div class="delta" style="font-size:12px;color:var(--mute)">Median: <b id="m-med-ttfo">0</b> ms</div>
      </div>

      <!-- Response Generation Duration -->
      <div class="card stat">
        <div class="lbl">${icon("hourglass", "clock")}Generation Duration</div>
        <div class="val"><span id="m-mean-dur">0</span><small>ms</small></div>
        <div class="delta" style="font-size:12px;color:var(--mute)">Median: <b id="m-med-dur">0</b> ms</div>
      </div>

      <!-- Deadline Attainment -->
      <div class="card stat">
        <div class="lbl">${icon("check")}Deadline Attainment</div>
        <div class="val" id="m-sla-pct" style="color:var(--ok)">100%</div>
        <div class="delta" id="m-sla-sub" style="font-size:12px;color:var(--mute)">0% miss rate</div>
      </div>
    </div>

    <!-- Distribution & Reliability Notice Section -->
    <div class="grid g2" style="margin-bottom:20px">
      <!-- Percentile Latencies -->
      <div class="card">
        <h3>${icon("gauge")}High-Percentile Latencies (P95 / P99)<small id="lbl-sample-count">N = 0 samples</small></h3>
        <table class="tbl" style="margin-top:10px">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Mean</th>
              <th>Median (P50)</th>
              <th>P95</th>
              <th>P99</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Queue Wait Time</td>
              <td id="p-wait-mean">–</td>
              <td id="p-wait-p50">–</td>
              <td id="p-wait-p95">–</td>
              <td id="p-wait-p99">–</td>
            </tr>
            <tr>
              <td>Time To First Output (TTFO)</td>
              <td id="p-ttfo-mean">–</td>
              <td id="p-ttfo-p50">–</td>
              <td id="p-ttfo-p95">–</td>
              <td id="p-ttfo-p99">–</td>
            </tr>
            <tr>
              <td>Generation Duration</td>
              <td id="p-dur-mean">–</td>
              <td id="p-dur-p50">–</td>
              <td id="p-dur-p95">–</td>
              <td id="p-dur-p99">–</td>
            </tr>
            <tr>
              <td>Inter-Chunk Latency (ICL)</td>
              <td id="p-icl-mean">–</td>
              <td>–</td>
              <td id="p-icl-p95">–</td>
              <td id="p-icl-p99">–</td>
            </tr>
          </tbody>
        </table>

        <div id="p-sample-disclaimer" style="margin-top:14px;padding:10px 12px;border-radius:10px;font-size:12px;line-height:1.4">
          <!-- Populated dynamically based on sample size -->
        </div>
      </div>

      <!-- Simulation vs Live Isolation Callout -->
      <div class="card" style="display:flex;flex-direction:column;justify-content:space-between">
        <div>
          <h3>${icon("info")}Live Ollama vs Simulation Data Isolation</h3>
          <p style="color:var(--mute);font-size:13.5px;line-height:1.55;margin:8px 0 14px">
            <b>Important Methodology Note:</b> Live measurements recorded on this tab reflect real wall-clock latency (using <code>time.perf_counter</code>) of actual Ollama model inference on your machine.
          </p>
          <ul style="color:var(--mute);font-size:13px;line-height:1.6;margin:0 0 14px;padding-left:18px">
            <li><b>Real Request Scheduling:</b> Requests queue at the application layer and dispatch sequentially to Ollama.</li>
            <li><b>Discrete-Time Isolation:</b> Background-task slowdown and CPU core utilization metrics apply strictly to the discrete-time multi-core simulator, and are never mixed with real inference metrics.</li>
            <li><b>Zero Mock Metrics:</b> All stats shown here originate from backend-recorded HTTP dispatch and token arrival events.</li>
          </ul>
        </div>
        <div style="background:var(--surface2);padding:10px 14px;border-radius:10px;border:1px solid var(--line);font-size:12px;color:var(--dim)">
          Switch to the <b>Simulation Benchmarks</b> tab above to inspect synthetic multi-core traces, ablation studies, and Friedman significance tests.
        </div>
      </div>
    </div>

    <!-- Policy Comparison Section -->
    <div class="card" style="margin-bottom:20px">
      <h3>
        ${icon("sliders")}Policy Performance Comparison
        <small>Evaluated over executed real Ollama workloads</small>
      </h3>
      <p style="color:var(--mute);font-size:13px;margin:4px 0 14px">
        Note: Comparative evaluations are descriptive. Robust conclusions require submitting identical prompt workloads under each policy with sufficient sample size (N ≥ 30 per policy).
      </p>

      <div class="scroll">
        <table class="tbl" id="tbl-policy-comparison">
          <thead>
            <tr>
              <th>Policy</th>
              <th>Sample Size (N)</th>
              <th>SLA Met Rate</th>
              <th>SLA Miss Rate</th>
              <th>Mean Wait</th>
              <th>Median Wait</th>
              <th>Mean TTFO</th>
              <th>P95 ICL</th>
            </tr>
          </thead>
          <tbody>
            <tr><td colspan="8" style="text-align:center;color:var(--dim)">Loading policy breakdown...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Individual Request Measurements Table -->
    <div class="card">
      <div class="row" style="margin-bottom:14px;justify-content:space-between">
        <h3 style="margin:0">${icon("search")}Individual Request Measurements</h3>
        <input type="search" id="inp-search-runs" placeholder="Search by request ID, policy, priority..." style="width:260px;font-size:12.5px" aria-label="Search runs">
      </div>

      <div class="scroll" style="max-height:420px">
        <table class="tbl" id="tbl-live-runs">
          <thead>
            <tr>
              <th>Sub #</th>
              <th>Disp #</th>
              <th>Request ID</th>
              <th>Policy</th>
              <th>Priority</th>
              <th>Deadline SLA</th>
              <th>Wait Time</th>
              <th>TTFO</th>
              <th>Avg ICL</th>
              <th>Duration</th>
              <th>SLA Status</th>
              <th>Status</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            <tr><td colspan="13" style="text-align:center;color:var(--dim)">No live runs recorded yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>`;

    const btnRef = $("#btn-refresh-analytics");
    if (btnRef) btnRef.onclick = () => fetchMetrics();

    let allRuns = [];
    const inpSearch = $("#inp-search-runs");
    if (inpSearch) {
      inpSearch.oninput = () => renderRunsTable(allRuns, inpSearch.value);
    }

    async function fetchMetrics() {
      if (dead || source !== "live") return;
      try {
        const res = await fetch("/api/live/metrics", { cache: "no-store" });
        if (!res.ok) return;
        const m = await res.json();
        updateScorecards(m);
        updatePercentiles(m);
        updatePolicyTable(m.runs_by_policy || {});
        allRuns = m.history || [];
        renderRunsTable(allRuns, inpSearch ? inpSearch.value : "");
      } catch (err) {
        console.warn("Failed to fetch live metrics:", err);
      }
    }

    function updateScorecards(m) {
      $("#m-total-reqs").textContent = m.total_requests || 0;
      $("#m-reqs-sub").textContent = `${m.completed_requests || 0} completed, ${m.failed_requests || 0} failed (${m.queued_requests || 0} in queue)`;

      $("#m-mean-wait").textContent = fmt(m.mean_wait_ms, 0);
      $("#m-med-wait").textContent = fmt(m.median_wait_ms, 0);

      $("#m-mean-ttfo").textContent = fmt(m.mean_ttfo_ms, 0);
      $("#m-med-ttfo").textContent = fmt(m.median_ttfo_ms, 0);

      $("#m-mean-dur").textContent = fmt(m.mean_duration_ms, 0);
      $("#m-med-dur").textContent = fmt(m.median_duration_ms, 0);

      const slaVal = $("#m-sla-pct");
      if (slaVal) {
        slaVal.textContent = fmt(m.deadline_attainment_pct, 1) + "%";
        slaVal.style.color = (m.deadline_attainment_pct >= 80 || m.completed_requests === 0) ? "var(--ok)" : "var(--late)";
      }
      const slaSub = $("#m-sla-sub");
      if (slaSub) {
        slaSub.textContent = fmt(m.deadline_miss_rate_pct, 1) + "% miss rate";
      }
    }

    function updatePercentiles(m) {
      const counts = m.sample_counts || {};
      const completedN = counts.completed || 0;
      $("#lbl-sample-count").textContent = `N = ${completedN} completed runs`;

      $("#p-wait-mean").textContent = fmt(m.mean_wait_ms, 0) + " ms";
      $("#p-wait-p50").textContent = fmt(m.median_wait_ms, 0) + " ms";
      $("#p-wait-p95").textContent = fmt(m.p95_wait_ms, 0) + " ms";
      $("#p-wait-p99").textContent = fmt(m.p99_wait_ms, 0) + " ms";

      $("#p-ttfo-mean").textContent = fmt(m.mean_ttfo_ms, 0) + " ms";
      $("#p-ttfo-p50").textContent = fmt(m.median_ttfo_ms, 0) + " ms";
      $("#p-ttfo-p95").textContent = fmt(m.p95_ttfo_ms, 0) + " ms";
      $("#p-ttfo-p99").textContent = fmt(m.p99_ttfo_ms, 0) + " ms";

      $("#p-dur-mean").textContent = fmt(m.mean_duration_ms, 0) + " ms";
      $("#p-dur-p50").textContent = fmt(m.median_duration_ms, 0) + " ms";
      $("#p-dur-p95").textContent = fmt(m.p95_duration_ms, 0) + " ms";
      $("#p-dur-p99").textContent = fmt(m.p99_duration_ms, 0) + " ms";

      $("#p-icl-mean").textContent = fmt(m.mean_icl_ms, 1) + " ms";
      $("#p-icl-p95").textContent = fmt(m.p95_icl_ms, 1) + " ms";
      $("#p-icl-p99").textContent = fmt(m.p99_icl_ms, 1) + " ms";

      const disc = $("#p-sample-disclaimer");
      if (disc) {
        if (completedN < 30) {
          disc.style.background = "rgba(255,184,77,.12)";
          disc.style.border = "1px solid var(--warn)";
          disc.style.color = "var(--warn)";
          disc.innerHTML = `<b>⚠️ Preliminary Sample Size (${completedN} / 30 runs):</b> High-percentile P95 and P99 metrics carry sampling variance until a larger workload batch is executed. Submit a few batches in Live Arena to achieve statistical stability.`;
        } else {
          disc.style.background = "rgba(61,220,151,.12)";
          disc.style.border = "1px solid var(--ok)";
          disc.style.color = "var(--ok)";
          disc.innerHTML = `<b>✓ Statistically Robust Sample Size (${completedN} runs):</b> P95 and P99 percentiles provide stable SLA tail-latency bounds.`;
        }
      }
    }

    function updatePolicyTable(byPolicy) {
      const tbody = $("#tbl-policy-comparison tbody");
      if (!tbody) return;

      const keys = ["fcfs", "priority", "edf", "token_laxity"];
      const rows = keys.map(k => {
        const pData = byPolicy[k];
        if (!pData || pData.sample_size === 0) {
          return `
            <tr>
              <td><i class="sw" style="background:${col(k)}"></i><b>${LABEL[k]}</b></td>
              <td style="color:var(--dim)">0 runs</td>
              <td style="color:var(--dim)">–</td>
              <td style="color:var(--dim)">–</td>
              <td style="color:var(--dim)">–</td>
              <td style="color:var(--dim)">–</td>
              <td style="color:var(--dim)">–</td>
              <td style="color:var(--dim)">–</td>
            </tr>`;
        }
        return `
          <tr class="${k === 'token_laxity' ? 'me' : ''}">
            <td><i class="sw" style="background:${col(k)}"></i><b>${LABEL[k]}</b></td>
            <td><b>${pData.sample_size}</b></td>
            <td style="color:var(--ok)"><b>${fmt(pData.deadline_met_pct, 1)}%</b></td>
            <td style="color:${pData.deadline_miss_pct > 0 ? 'var(--late)' : 'var(--mute)'}">${fmt(pData.deadline_miss_pct, 1)}%</td>
            <td>${fmt(pData.mean_wait_ms, 0)} ms</td>
            <td>${fmt(pData.median_wait_ms, 0)} ms</td>
            <td>${fmt(pData.mean_ttfo_ms, 0)} ms</td>
            <td>${fmt(pData.p95_icl_ms, 1)} ms</td>
          </tr>`;
      }).join("");

      tbody.innerHTML = rows;
    }

    function renderRunsTable(runs, filterQuery) {
      const tbody = $("#tbl-live-runs tbody");
      if (!tbody) return;

      const q = (filterQuery || "").trim().toLowerCase();
      const filtered = runs.filter(r => {
        if (!q) return true;
        const text = `${r.request_id} ${r.policy} ${r.priority} ${r.model} ${r.prompt_preview || ''} ${r.status}`.toLowerCase();
        return text.includes(q);
      });

      if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;color:var(--dim);padding:24px 0">${runs.length === 0 ? "No completed live requests yet." : "No requests match your filter query."}</td></tr>`;
        return;
      }

      tbody.innerHTML = filtered.map(r => {
        const arrIdx = r.arrival_index != null ? `#${r.arrival_index}` : "–";
        const dispIdx = r.dispatch_index != null ? `#${r.dispatch_index}` : "–";
        const metSla = !r.deadline_missed;
        const prioCol = r.priority === "high" ? "#D58CFF" : (r.priority === "low" ? "#E39BFF" : "#36D6C3");

        return `
        <tr>
          <td><span class="badge" style="background:var(--surface2)">${arrIdx}</span></td>
          <td><span class="badge" style="background:rgba(124,107,255,.2);color:var(--brand-2)">${dispIdx}</span></td>
          <td><code>${esc(r.request_id)}</code></td>
          <td><b>${esc(LABEL[r.policy] || r.policy)}</b></td>
          <td><span class="badge" style="background:${prioCol}22;color:${prioCol};text-transform:capitalize">${esc(r.priority || 'normal')}</span></td>
          <td>${fmt(r.deadline_ms, 0)} ms</td>
          <td>${fmt(r.queue_wait_ms, 0)} ms</td>
          <td>${fmt(r.ttfo_ms, 0)} ms</td>
          <td>${fmt(r.average_icl_ms, 1)} ms</td>
          <td>${fmt(r.generation_duration_ms, 0)} ms</td>
          <td>
            <span class="badge ${metSla ? 'ok' : 'bad'}">${metSla ? '✓ Met SLA' : '✕ Missed'}</span>
          </td>
          <td>
            <span class="badge ${r.status === 'completed' ? 'ok' : 'bad'}">${esc(r.status)}</span>
          </td>
          <td style="color:var(--dim);font-size:12px">${esc(r.timestamp || '–')}</td>
        </tr>`;
      }).join("");
    }

    await fetchMetrics();
    livePollTimer = setInterval(fetchMetrics, 2000);
  }

  // =========================================================================
  // 2. Discrete-Time Simulation Analytics View (Teammate implementation preserved)
  // =========================================================================
  async function renderSimAnalytics() {
    const container = $("#analytics-body");
    if (!container || dead) return;

    let cur = settings.scenario, tab = Store.get("atab", "latency"), state = {}, metric = "request_miss_rate";

    container.innerHTML = `
    <div class="row" style="margin-bottom:14px">
      <div id="sim-tabs"></div>
      <div id="sim-scn" style="margin-left:auto"></div>
    </div>
    <div id="sim-body"></div>`;

    chips($("#sim-tabs"), [
      { k: "latency", label: "Latency" },
      { k: "fair", label: "Fairness" },
      { k: "sig", label: "Significance" },
      { k: "design", label: "Design" }
    ], tab, k => { tab = k; Store.set("atab", k); renderSim(); });

    scenarioChips($("#sim-scn"), cur, k => { cur = k; settings.scenario = k; saveSettings(); renderSim(); });

    const body = $("#sim-body");
    const card = (t, ic, inner, sub = "") => `<div class="card"><h3>${icon(ic)}${t}<small>${sub}</small></h3>${inner}</div>`;
    const BARS = (m, unit, dec, max) => SCHED.map(s => ({ label: SHORT[s], color: col(s), v: S(cur, s, m), lo: CI(cur, s, m).lo, hi: CI(cur, s, m).hi, bold: s === "token_laxity" }));

    async function renderSim() {
      if (tab === "latency") {
        body.innerHTML = `<div class="grid g2">${card("Token lateness", "clock", '<div id="lg"></div><div id="cdf"></div>', "share of tokens within x of deadline")}
          ${card("Gap between tokens", "gauge", '<div class="scroll"><table class="tbl" id="pct"></table></div>', "ms, one run")}
          ${card("Average gap", "zap", '<div id="avg"></div>', "mean, 95% CI")}${card("First token", "users", '<div id="ttft"></div>', "each dot a request")}</div>`;
        const R = await replay(cur); if (dead || source !== "sim") return;
        const cdf = () => {
          const w = 600, h = 300, L = 48, B = 30, X = lin(0, 4, L, w - 10), Y = lin(0, 1, h - B, 10);
          let s = axisY(Y, [0, .25, .5, .75, 1], L, w - L - 10, v => Math.round(v * 100) + "%") + axisX(X, [0, 1, 2, 3, 4], h - B, v => v + "x");
          s += `<line x1="${X(1)}" x2="${X(1)}" y1="10" y2="${h - B}" stroke="var(--late)" stroke-dasharray="5 4" stroke-width="1.6"/><text x="${X(1) + 6}" y="24" style="fill:var(--late)" font-size="12" font-weight="700">deadline</text>`;
          SCHED.forEach(sc => {
            if (state[sc] === false) return; const r = R.runs[sc].tokens.filter(k => k[5] > 0).map(k => k[2] / k[3]).sort((a, b) => a - b), n = r.length; if (!n) return;
            let d = "M" + X(0) + " " + Y(0), st = Math.max(1, Math.floor(n / 260)); for (let i = 0; i < n; i += st)d += `L${X(Math.min(r[i], 4))} ${Y((i + 1) / n)}`; d += `L${X(Math.min(r[n - 1], 4))} ${Y(1)}`;
            s += `<path d="${d}" fill="none" stroke="${col(sc)}" stroke-width="${sc === "token_laxity" ? 3.6 : 2}" stroke-linejoin="round"/>`
          });
          svgWrap($("#cdf"), w, h, s)
        };
        legend($("#lg"), SCHED.map(s => ({ k: s, label: SHORT[s], color: col(s) })), state, cdf); cdf();
        const rows = SCHED.map(s => { const t = R.runs[s].tokens.filter(k => k[5] > 0), l = t.map(k => k[2]); return { s, p50: q(l, .5), p95: q(l, .95), p99: q(l, .99), max: Math.max(0, ...l), miss: 100 * t.filter(k => k[4]).length / Math.max(1, t.length) } });
        $("#pct").innerHTML = `<thead><tr><th>Scheduler</th><th>p50</th><th>p95</th><th>p99</th><th>max</th><th>Late</th></tr></thead><tbody>` + rows.map(r => `<tr class="${r.s === "token_laxity" ? "me" : ""}"><td><i class="sw" style="background:${col(r.s)}"></i>${LABEL[r.s]}</td><td>${fmt(r.p50, 0)}</td><td>${fmt(r.p95, 0)}</td><td>${fmt(r.p99, 0)}</td><td>${fmt(r.max, 0)}</td><td>${fmt(r.miss, 1)}%</td></tr>`).join("") + "</tbody>";
        hbars($("#avg"), BARS("avg_token_latency_ms"), { unit: " ms", dec: 0 });
        const w = 560, rh = 44, h = SCHED.length * rh + 34, L = 70, lg = v => Math.log10(v + 1), xs = lin(0, Math.log10(61), L, w - 14); let s = "";
        [0.1, 0.5, 1, 2, 5, 20, 60].forEach(v => { s += `<line x1="${xs(lg(v))}" x2="${xs(lg(v))}" y1="0" y2="${h - 30}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="${xs(lg(v))}" y="${h - 10}" text-anchor="middle" font-size="12">${v}x</text>` });
        s += `<line x1="${xs(lg(1))}" x2="${xs(lg(1))}" y1="0" y2="${h - 30}" stroke="var(--late)" stroke-width="2"/>`;
        SCHED.forEach((sc, i) => {
          const y = i * rh + rh / 2; s += `<text x="${L - 10}" y="${y + 4}" text-anchor="end" font-size="14" font-weight="${sc === "token_laxity" ? 800 : 600}" style="fill:${sc === "token_laxity" ? "var(--text)" : "var(--mute)"}">${SHORT[sc]}</text>`;
          R.runs[sc].tokens.filter(k => k[5] === 0).forEach((k, j) => { const r = k[2] / k[3]; s += `<circle cx="${xs(lg(r))}" cy="${y + ((j * 37) % 17) - 8}" r="5" fill="${r > 1 ? "var(--late)" : "var(--ok)"}" fill-opacity=".85" data-t="${esc(R.requests[k[0]].id)}: first token after ${fmt(k[2], 0)} ms (budget ${fmt(k[3], 0)} ms)"/>` })
        });
        const sv = svgWrap($("#ttft"), w, h, s); onHover(sv, e => e.target.dataset.t ? esc(e.target.dataset.t) : null);
      }
      else if (tab === "fair") {
        body.innerHTML = `<div class="grid g2">${card("Fairness", "users", '<div id="jain"></div>', "Jain index, 1 is equal")}${card("Who gets left behind", "turtle", '<div id="strip"></div>', "share of ideal speed")}
          ${card("Quality against cost", "gauge", '<div id="trade"></div>', "all scenarios")}${card("Throughput and preemptions", "cpu", '<div id="thr"></div><div id="pre" style="margin-top:12px"></div>', "tokens/s")}</div>`;
        hbars($("#jain"), BARS("jain_progress"), { max: 1, dec: 3 }); hbars($("#thr"), BARS("throughput_tok_s"), { unit: " tok/s", dec: 0 });
        hbars($("#pre"), SCHED.map(s => ({ label: SHORT[s], color: col(s), v: S(cur, s, "preemptions"), bold: s === "token_laxity" })), { dec: 0 });
        const w = 560, h = 360, L = 52, B = 42, ks = SCN(); let mx = 0; ks.forEach(k => SCHED.forEach(s => mx = Math.max(mx, S(k, s, "bg_mean_slowdown"))));
        const X = lin(0, Math.ceil(mx), L, w - 14), Y = lin(0, 100, h - B, 10); let s = axisY(Y, [0, 25, 50, 75, 100], L, w - L - 14, v => v + "%") + axisX(X, niceTicks(0, Math.ceil(mx), 5), h - B, v => v + "x");
        s += `<text x="${(L + w) / 2}" y="${h - 4}" text-anchor="middle" font-size="12">background slowdown</text><text transform="translate(14 ${h / 2}) rotate(-90)" text-anchor="middle" font-size="12">requests missing SLA</text>`;
        ks.forEach(k => SCHED.forEach(sc => { const t = sc === "token_laxity"; s += `<circle cx="${X(S(k, sc, "bg_mean_slowdown"))}" cy="${Y(S(k, sc, "request_miss_rate"))}" r="${t ? 9 : 6}" fill="${col(sc)}" fill-opacity="${t ? 1 : .75}" stroke="${t ? "var(--text)" : "none"}" stroke-width="2" data-t="${esc(LABEL[sc] + ", " + scnName(k))}: ${fmt(S(k, sc, "request_miss_rate"), 0)}% missed, background ${fmt(S(k, sc, "bg_mean_slowdown"), 1)}x"/>` }));
        const sv = svgWrap($("#trade"), w, h, s); onHover(sv, e => e.target.dataset.t ? esc(e.target.dataset.t) : null);
        const R = await replay(cur); if (dead || source !== "sim") return;
        const w2 = 560, rh = 42, h2 = SCHED.length * rh + 34, X2 = lin(0, 1, 70, w2 - 14); let s2 = niceTicks(0, 1, 4).map(t => `<line x1="${X2(t)}" x2="${X2(t)}" y1="0" y2="${h2 - 30}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="${X2(t)}" y="${h2 - 10}" text-anchor="middle" font-size="12">${t}</text>`).join("");
        SCHED.forEach((sc, i) => {
          const y = i * rh + rh / 2; s2 += `<text x="60" y="${y + 4}" text-anchor="end" font-size="14" font-weight="${sc === "token_laxity" ? 800 : 600}" style="fill:${sc === "token_laxity" ? "var(--text)" : "var(--mute)"}">${SHORT[sc]}</text>`;
          const last = {}; R.runs[sc].tokens.forEach(k => { last[k[0]] = Math.max(last[k[0]] || 0, k[1]) });
          R.requests.forEach((r, j) => { if (last[j] == null) return; const p = Math.min(1, r.service_ms / Math.max(last[j] - r.arrival_ms, 1)); s2 += `<circle cx="${X2(p)}" cy="${y + ((j * 29) % 15) - 7}" r="5" fill="${col(sc)}" fill-opacity=".8" data-t="${esc(r.id)} (${esc(r.type)}): ${fmt(100 * p, 0)}% of ideal speed"/>` })
        });
        const sv2 = svgWrap($("#strip"), w2, h2, s2); onHover(sv2, e => e.target.dataset.t ? esc(e.target.dataset.t) : null);
      }
      else if (tab === "sig") {
        const M = BENCH.metric_meta, cols = [...SCN(), "ALL"], BL = SCHED.filter(s => s !== "token_laxity");
        body.innerHTML = `<div class="row" style="margin-bottom:16px"><label class="fld" style="min-width:260px">Metric<select id="metric">${Object.keys(M).map(k => `<option value="${k}">${esc(M[k].label)}</option>`).join("")}</select></label><span class="badge" id="dir"></span><span class="sub" style="color:var(--mute)">Click a cell for the test. * means significant after Holm correction.</span></div>
          <div class="card"><div class="scroll"><table class="tbl" id="mx"></table></div></div>
          <div class="grid g2" style="margin-top:18px" id="rk"></div>`;
        $("#metric").value = metric;
        const cmp = (c, b) => BENCH.comparisons.find(x => x.scenario === c && x.metric === metric && x.baseline === b);
        const mx = () => {
          $("#dir").textContent = M[metric].higher_better ? "Higher is better" : "Lower is better";
          $("#mx").innerHTML = `<thead><tr><th>TLS improvement over</th>${cols.map(c => `<th>${c === "ALL" ? "All pooled" : scnShort[c]}</th>`).join("")}</tr></thead><tbody>` + BL.map(b => `<tr><td><i class="sw" style="background:${col(b)}"></i>${LABEL[b]}</td>${cols.map(c => {
            const r = cmp(c, b); if (!r) return "<td></td>"; const v = r.rel_improvement_pct ?? 0, t = Math.min(1, Math.abs(v) / 80);
            return `<td class="click" data-c="${c}" data-b="${b}" style="cursor:pointer;background:${v >= 0 ? `rgba(124,107,255,${.1 + t * .55})` : `rgba(255,92,108,${.1 + t * .55})`};font-weight:800;text-align:center;border-radius:8px">${v > 0 ? "+" : ""}${fmt(v, 0)}%${r.significant ? " *" : ""}</td>`
          }).join("")}</tr>`).join("") + "</tbody>";
          $$("#mx td[data-c]").forEach(td => td.onclick = () => detail(td.dataset.c, td.dataset.b))
        };
        const detail = (c, b) => {
          const r = cmp(c, b), u = M[metric].unit, p = r.p_holm < 0.001 ? "< 0.001" : fmt(r.p_holm, 3);
          modal(`<button class="btn icon x" data-close aria-label="Close">${icon("x")}</button><h2>TLS vs ${LABEL[b]}</h2><p style="color:var(--mute);margin:0">${esc(M[metric].label)}, ${c === "ALL" ? "all scenarios" : esc(scnName(c))}</p>
            <div class="kv"><div><small>TLS</small><b>${fmt(r.mean_a, 2)}${u}</b></div><div><small>${esc(SHORT[b])}</small><b>${fmt(r.mean_b, 2)}${u}</b></div><div><small>Improvement</small><b>${r.rel_improvement_pct == null ? "n/a" : fmt(r.rel_improvement_pct, 0) + "%"}</b></div>
            <div><small>Wins / ties / losses</small><b>${r.wins}/${r.ties}/${r.losses}</b></div><div><small>Cohen d</small><b>${fmt(r.cohens_dz, 2)}</b></div><div><small>Holm p</small><b>${p}</b></div></div>
            <span class="badge ${r.significant ? "ok" : "warn"}">${r.significant ? "Significant at 0.05" : "Not significant"}</span> <span class="sub" style="color:var(--mute);font-size:13px">${r.n} paired workloads, bootstrap 95% CI ${fmt(r.improvement_ci95[0], 2)} to ${fmt(r.improvement_ci95[1], 2)}${u}</span>`)
        };
        $("#metric").onchange = e => { metric = e.target.value; mx() }; mx();
        $("#rk").innerHTML = SCN().map(k => `<div class="card"><h3>${scnName(k)}<small>mean rank, 1 is best. Friedman p ${BENCH.friedman[k].request_miss_rate.p < 0.001 ? "< 0.001" : fmt(BENCH.friedman[k].request_miss_rate.p, 3)}</small></h3><div id="rk-${k}"></div></div>`).join("");
        SCN().forEach(k => { const rk = BENCH.friedman[k].request_miss_rate.mean_rank; hbars($("#rk-" + k), SCHED.map(s => ({ label: SHORT[s], color: col(s), v: rk[s], bold: s === "token_laxity" })), { rowH: 24, max: 6, dec: 1 }) });
      }
      else {
        const ab = Object.keys(BENCH.ablation); let ac = ab.includes(cur) ? cur : ab[0];
        body.innerHTML = `<div class="card"><h3>${icon("flask")}Remove one part at a time<small>means over the same seeds</small></h3><div class="chips" id="ac" style="margin-bottom:14px"></div><div class="scroll"><table class="tbl" id="abl"></table></div></div>
         <div class="grid g2" style="margin-top:18px"><div class="card"><h3>${icon("sliders")}Capacity bound<small>heavy load, requests missing SLA</small></h3><div id="s1"></div></div><div class="card"><h3>${icon("cpu")}Capacity bound<small>background slowdown</small></h3><div id="s2"></div></div></div>`;
        const VL = { tls_full: "Full TLS", tls_no_planner: "No capacity planner", tls_no_midtoken: "No mid-token preemption", tls_no_aging: "No starvation guard" };
        const tb = () => { const a = BENCH.ablation[ac]; $("#abl").innerHTML = `<thead><tr><th>Variant</th><th>Requests missing SLA</th><th>Tokens late</th><th>p99 gap</th><th>Background</th></tr></thead><tbody>` + Object.keys(VL).map(v => { const m = a[v]; return `<tr class="${v === "tls_full" ? "me" : ""}"><td>${VL[v]}</td><td>${fmt(m.request_miss_rate.mean, 1)}%</td><td>${fmt(m.token_miss_rate.mean, 2)}%</td><td>${fmt(m.p99_itl_ms.mean, 0)} ms</td><td>${fmt(m.bg_mean_slowdown.mean, 2)}x</td></tr>` }).join("") + "</tbody>" };
        chips($("#ac"), ab.map(k => ({ k, label: scnName(k) })), ac, k => { ac = k; tb() }); tb();
        const ks = Object.keys(BENCH.sensitivity), c = col("token_laxity");
        if (ks.length) {
          lineChart($("#s1"), [{ pts: ks.map(k => [+k, BENCH.sensitivity[k].request_miss_rate.mean]), color: c }], { yfmt: v => v + "%", ymax: 80, xt: ks.map(Number), xfmt: v => "bound " + v });
          lineChart($("#s2"), [{ pts: ks.map(k => [+k, BENCH.sensitivity[k].bg_mean_slowdown.mean]), color: "var(--warn)" }], { yfmt: v => v + "x", ymax: 10, xt: ks.map(Number), xfmt: v => "bound " + v })
        }
      }
    }

    await renderSim();
  }

  // Initial render
  switchSource();

  return () => {
    dead = true;
    if (livePollTimer) clearInterval(livePollTimer);
  };
});
