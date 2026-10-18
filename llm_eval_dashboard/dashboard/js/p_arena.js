/* Live Arena Page: Real Ollama Execution Timeline & Queue Scheduling vs Simulation Replay */
route("arena", async view => {
  let mode = Store.get("arena_mode", "live"); // "live" | "sim"
  let dead = false;
  let livePollTimer = null;

  // Render container
  view.innerHTML = `
  <div class="top" style="margin-bottom:14px">
    <div>
      <h1 style="display:flex;align-items:center;gap:10px">
        ${icon("arena")}Arena & Execution Timeline
      </h1>
      <div class="sub" id="arena-sub-label">Real local Ollama execution governed by application-level scheduling</div>
    </div>
    <div class="row" style="margin-left:auto">
      <div id="mode-chips"></div>
    </div>
  </div>
  <div id="arena-body"></div>`;

  chips($("#mode-chips"), [
    { k: "live", label: "⚡ Live Ollama Arena" },
    { k: "sim", label: "🧪 Simulation Replay" }
  ], mode, m => {
    mode = m;
    Store.set("arena_mode", m);
    switchMode();
  });

  function switchMode() {
    if (dead) return;
    if (livePollTimer) {
      clearInterval(livePollTimer);
      livePollTimer = null;
    }
    const sub = $("#arena-sub-label");
    if (mode === "live") {
      if (sub) sub.textContent = "Real local Ollama execution governed by application-level scheduling";
      renderLiveMode();
    } else {
      if (sub) sub.textContent = "Discrete-time multi-core CPU scheduler simulation replay";
      renderSimMode();
    }
  }

  // =========================================================================
  // 1. Live Ollama Arena Mode
  // =========================================================================
  async function renderLiveMode() {
    const container = $("#arena-body");
    if (!container || dead) return;

    container.innerHTML = `
    <!-- Live Mode Toolbar -->
    <div class="tool" role="toolbar" aria-label="Live Controls" style="margin-bottom:18px">
      <div class="row" style="gap:12px;width:100%">
        <span class="status" id="live-srv-status" style="padding:6px 12px;background:var(--surface)">
          <span class="dot on" id="live-dot"></span>
          <span id="live-srv-txt" style="font-weight:700">Connecting to Ollama...</span>
        </span>

        <label class="fld" style="margin:0;font-size:12px">
          Dispatch Policy
          <select id="arena-live-policy" style="min-width:160px">
            <option value="fcfs">FCFS (FIFO Arrival)</option>
            <option value="priority">Priority (Static High > Low)</option>
            <option value="edf">EDF (Earliest Deadline)</option>
            <option value="token_laxity">Token Laxity (Dynamic TLS)</option>
          </select>
        </label>

        <button class="btn primary" id="btn-burst" style="margin-left:auto">
          ${icon("zap")}<span>Inject Test Workload (4 Prompts)</span>
        </button>
        <button class="btn" id="btn-custom-prompt">
          ${icon("plus")}Custom Prompt
        </button>
        <button class="btn icon" id="btn-refresh-live" title="Refresh state">
          ${icon("restart")}
        </button>
      </div>
    </div>

    <!-- Live HUD Stats -->
    <div class="grid g4" id="live-hud" style="margin-bottom:18px">
      <div class="card stat" style="padding:14px 18px">
        <div class="lbl">${icon("sliders")}Active Policy</div>
        <div class="val" id="hud-policy" style="font-size:26px;text-transform:uppercase">–</div>
      </div>
      <div class="card stat" style="padding:14px 18px">
        <div class="lbl">${icon("cpu")}Inference Slot</div>
        <div class="val" id="hud-slot" style="font-size:26px;color:var(--ok)">Idle</div>
      </div>
      <div class="card stat" style="padding:14px 18px">
        <div class="lbl">${icon("clock")}Queue Waiting</div>
        <div class="val" id="hud-queued" style="font-size:26px">0</div>
      </div>
      <div class="card stat" style="padding:14px 18px">
        <div class="lbl">${icon("check")}SLA Attainment</div>
        <div class="val" id="hud-sla" style="font-size:26px;color:var(--ok)">100%</div>
      </div>
    </div>

    <!-- Execution Timeline Section -->
    <div class="card" style="margin-bottom:18px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        <h3 style="margin:0">${icon("clock")}Real Request Execution Timeline</h3>
        <span class="sub" style="color:var(--mute)">Submission Order vs Actual Dispatch Order</span>
        <div class="legend" style="margin-left:auto">
          <span><i class="sw" style="background:var(--warn)"></i>Queue Wait</span>
          <span><i class="sw" style="background:var(--brand)"></i>TTFO / Eval</span>
          <span><i class="sw" style="background:var(--ok)"></i>Generation Stream</span>
        </div>
      </div>

      <div id="timeline-list">
        <div class="empty">
          ${icon("clock")}
          <b>No live requests executed yet</b>
          <span>Click "Inject Test Workload" above or submit prompts from Live Chat to see real execution timelines.</span>
        </div>
      </div>
    </div>

    <!-- Bottom Two Columns: Queue Inspector + Events Feed -->
    <div class="grid g2">
      <!-- Live Queue Inspector -->
      <div class="card">
        <h3>${icon("cpu")}Live Queue Inspector <small id="lbl-live-q-count">0 waiting</small></h3>
        <div id="active-inferencing-box" style="margin-bottom:14px;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--surface2)">
          <div style="font-size:11px;color:var(--dim);text-transform:uppercase;font-weight:700">Currently Executing</div>
          <div id="active-slot-details" style="font-size:13px;color:var(--mute);margin-top:4px">No active request (idle)</div>
        </div>

        <div style="font-size:12px;font-weight:700;color:var(--mute);margin-bottom:8px">Queued Requests (Ranked by Policy)</div>
        <div class="scroll" style="max-height:260px">
          <table class="tbl" id="tbl-queue-inspector">
            <thead>
              <tr>
                <th>Pos</th>
                <th>Request ID</th>
                <th>Arrival #</th>
                <th>Priority</th>
                <th>Deadline</th>
                <th>Wait</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr><td colspan="7" style="text-align:center;color:var(--dim)">Queue is empty</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Scheduler Events Feed -->
      <div class="card">
        <h3>${icon("zap")}Timestamped Scheduler Events <small id="lbl-events-count">Recent events</small></h3>
        <div class="feed" id="live-events-feed" style="height:320px;overflow-y:auto">
          <div class="com">Connecting to event stream...</div>
        </div>
      </div>
    </div>`;

    // Wire up policy change
    const selPolicy = $("#arena-live-policy");
    if (selPolicy) {
      selPolicy.onchange = async () => {
        const p = selPolicy.value;
        try {
          const res = await fetch("/api/live/policy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ policy: p })
          });
          if (res.ok) {
            toast(`Dispatch policy switched to ${LABEL[p] || p.toUpperCase()}`);
            fetchTimeline();
          }
        } catch (e) {
          toast("Failed to update policy: " + e.message, "err");
        }
      };
    }

    // Wire up Burst Workload Button
    const btnBurst = $("#btn-burst");
    if (btnBurst) {
      btnBurst.onclick = async () => {
        btnBurst.disabled = true;
        btnBurst.innerHTML = `${icon("zap")}<span>Injecting 4 Prompts...</span>`;
        try {
          const res = await fetch("/api/live/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ policy: selPolicy ? selPolicy.value : "fcfs" })
          });
          const data = await res.json();
          if (res.ok) {
            toast(`Injected ${data.submitted_count} benchmark requests into scheduler queue`);
            fetchTimeline();
          } else {
            toast(data.error || "Batch injection failed", "err");
          }
        } catch (e) {
          toast("Batch error: " + e.message, "err");
        } finally {
          btnBurst.disabled = false;
          btnBurst.innerHTML = `${icon("zap")}<span>Inject Test Workload (4 Prompts)</span>`;
        }
      };
    }

    // Wire up Custom Prompt Modal
    const btnCustom = $("#btn-custom-prompt");
    if (btnCustom) {
      btnCustom.onclick = () => openCustomPromptModal();
    }

    const btnRef = $("#btn-refresh-live");
    if (btnRef) {
      btnRef.onclick = () => fetchTimeline();
    }

    // Polling function for live timeline
    async function fetchTimeline() {
      if (dead || mode !== "live") return;
      try {
        const [tlRes, healthRes] = await Promise.all([
          fetch("/api/live/timeline", { cache: "no-store" }),
          fetch("/api/health", { cache: "no-store" })
        ]);

        if (!tlRes.ok) return;
        const tl = await tlRes.json();
        const health = healthRes.ok ? await healthRes.json() : null;

        // Update server status pill
        const dot = $("#live-dot"), txt = $("#live-srv-txt");
        if (dot && txt) {
          if (health && health.ollama_connected) {
            dot.className = "dot on";
            txt.textContent = "Ollama Connected (" + (health.active_policy ? health.active_policy.toUpperCase() : "LIVE") + ")";
          } else {
            dot.className = "dot off";
            txt.textContent = "Ollama Offline (Check `ollama serve`)";
          }
        }

        // Update HUD
        if (selPolicy && tl.active_policy && selPolicy.value !== tl.active_policy && !selPolicy.matches(":focus")) {
          selPolicy.value = tl.active_policy;
        }
        const hudPol = $("#hud-policy");
        if (hudPol) hudPol.textContent = (LABEL[tl.active_policy] || tl.active_policy || "FCFS");

        const hudSlot = $("#hud-slot");
        if (hudSlot) {
          if (tl.is_busy && tl.active_request) {
            hudSlot.textContent = "Inferencing";
            hudSlot.style.color = "var(--warn)";
          } else {
            hudSlot.textContent = "Idle (Ready)";
            hudSlot.style.color = "var(--ok)";
          }
        }

        const hudQ = $("#hud-queued");
        if (hudQ) hudQ.textContent = tl.queued_count;

        const hudSla = $("#hud-sla");
        if (hudSla) {
          const comp = tl.timeline.filter(t => t.status === "completed");
          if (comp.length > 0) {
            const onTime = comp.filter(t => !t.deadline_missed).length;
            const pct = Math.round(100.0 * onTime / comp.length);
            hudSla.textContent = pct + "%";
            hudSla.style.color = pct >= 80 ? "var(--ok)" : "var(--late)";
          } else {
            hudSla.textContent = "100%";
          }
        }

        // Render Timeline List
        renderTimelineList(tl.timeline || []);

        // Render Queue Inspector
        renderQueueInspector(tl.active_request, tl.timeline.filter(t => t.status === "queued"));

        // Render Events Feed
        renderEventsFeed(tl.events || []);

      } catch (err) {
        console.warn("Live timeline poll error:", err);
      }
    }

    function renderTimelineList(items) {
      const elList = $("#timeline-list");
      if (!elList) return;

      if (!items || items.length === 0) {
        elList.innerHTML = `
        <div class="empty">
          ${icon("clock")}
          <b>No live requests in timeline yet</b>
          <span>Click "Inject Test Workload" above or submit prompts from Live Chat to see real execution timelines.</span>
        </div>`;
        return;
      }

      // Show latest items first
      const displayItems = [...items].reverse();

      elList.innerHTML = displayItems.map(item => {
        const isQueued = item.status === "queued";
        const isRunning = item.status === "running";
        const isCompleted = item.status === "completed";
        const isFailed = item.status === "failed";

        const arrIdx = item.arrival_index != null ? item.arrival_index : "–";
        const dispIdx = item.dispatch_index != null ? item.dispatch_index : (isRunning ? "Active" : "Pending");

        // Reorder indicator
        let reorderHtml = "";
        if (item.dispatch_index != null && item.arrival_index != null) {
          if (item.dispatch_index < item.arrival_index) {
            const diff = item.arrival_index - item.dispatch_index;
            reorderHtml = `<span class="tl-reorder jump" title="Dispatched ahead of arrival order via ${item.policy}">▲ +${diff} Jumped</span>`;
          } else if (item.dispatch_index === item.arrival_index) {
            reorderHtml = `<span class="tl-reorder fifo">● FIFO Order</span>`;
          } else {
            reorderHtml = `<span class="tl-reorder wait">▼ Deferred</span>`;
          }
        } else if (isQueued) {
          reorderHtml = `<span class="tl-reorder wait">⏳ Pos #${item.queue_position || 1}</span>`;
        }

        // Priority pill
        const prioCol = item.priority === "high" ? "#D58CFF" : (item.priority === "low" ? "#E39BFF" : "#36D6C3");
        const prioBadge = `<span class="badge" style="background:${prioCol}22;color:${prioCol};text-transform:capitalize">${esc(item.priority || "normal")}</span>`;

        // Segments calculation
        const waitMs = Math.max(0, Number(item.queue_wait_ms || 0));
        const ttfoMs = Math.max(0, Number(item.ttfo_ms || 0));
        const genMs = Math.max(0, Number(item.generation_duration_ms || 0));
        const totalTurnaround = waitMs + genMs;

        let barContent = "";
        if (isQueued) {
          barContent = `<div class="tl-seg wait" style="width:100%;animation:pulse 1.5s infinite">Queued (waiting: ${fmt(waitMs, 0)} ms)</div>`;
        } else if (isRunning) {
          barContent = `
            <div class="tl-seg wait" style="width:30%">Wait ${fmt(waitMs, 0)} ms</div>
            <div class="tl-seg stream" style="width:70%;animation:pulse 1s infinite">Inferencing now...</div>`;
        } else {
          // Proportion based on turnaround
          const total = Math.max(totalTurnaround, 1);
          const wPct = Math.max(8, Math.min(60, (waitMs / total) * 100));
          const tPct = ttfoMs > 0 ? Math.max(6, Math.min(30, (ttfoMs / total) * 100)) : 0;
          const gPct = Math.max(10, 100 - wPct - tPct);

          barContent = `
            <div class="tl-seg wait" style="width:${wPct}%" title="Queue wait: ${fmt(waitMs, 0)} ms">Wait ${fmt(waitMs, 0)}ms</div>
            ${ttfoMs > 0 ? `<div class="tl-seg ttfo" style="width:${tPct}%" title="TTFO: ${fmt(ttfoMs, 0)} ms">TTFO ${fmt(ttfoMs, 0)}ms</div>` : ""}
            <div class="tl-seg stream" style="width:${gPct}%" title="Generation: ${fmt(genMs, 0)} ms">Stream ${fmt(genMs, 0)}ms</div>`;
        }

        // SLA Badge
        let slaBadge = "";
        if (isCompleted) {
          if (item.deadline_missed) {
            slaBadge = `<span class="badge bad">${icon("alert")}Missed SLA (${fmt(totalTurnaround/1000, 1)}s / ${fmt(item.deadline_ms/1000, 1)}s)</span>`;
          } else {
            slaBadge = `<span class="badge ok">${icon("check")}Met SLA (${fmt(totalTurnaround/1000, 1)}s)</span>`;
          }
        } else if (isFailed) {
          slaBadge = `<span class="badge bad">${icon("alert")}Failed</span>`;
        } else if (isRunning) {
          slaBadge = `<span class="badge" style="background:var(--brand-soft);color:var(--brand-2)">${icon("cpu")}Active</span>`;
        } else {
          slaBadge = `<span class="badge warn">${icon("clock")}Queued</span>`;
        }

        return `
        <div class="tl-row ${isRunning ? 'running' : ''}">
          <!-- Order Column -->
          <div>
            <div class="tl-orders">
              <span class="badge-sub" title="Arrival / Submission Order">Sub #${arrIdx}</span>
              <span style="color:var(--dim)">➔</span>
              <span class="badge-disp" title="Execution / Dispatch Order">Disp #${dispIdx}</span>
            </div>
            <div style="margin-top:6px">${reorderHtml}</div>
          </div>

          <!-- Request Details -->
          <div>
            <div style="font-weight:700;font-size:13px;display:flex;align-items:center;gap:6px">
              ${esc(item.request_id)} ${prioBadge}
            </div>
            <div style="font-size:12px;color:var(--mute);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px" title="${esc(item.prompt_preview || '')}">
              ${esc(item.prompt_preview || item.model || '')}
            </div>
          </div>

          <!-- Horizontal Timeline Bar -->
          <div>
            <div class="tl-bar-wrap">${barContent}</div>
            <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim);margin-top:4px">
              <span>Arrived: ${esc(item.timestamp || '–')}</span>
              <span>Target SLA: ${fmt(item.deadline_ms, 0)} ms</span>
            </div>
          </div>

          <!-- Metrics & Status -->
          <div class="tl-meta">
            ${slaBadge}
            <div style="color:var(--mute);font-size:11px;margin-top:4px">
              Turnaround: <b>${isCompleted ? fmt(totalTurnaround, 0) + ' ms' : 'In progress'}</b>
            </div>
          </div>
        </div>`;
      }).join("");
    }

    function renderQueueInspector(activeReq, queuedItems) {
      const box = $("#active-slot-details");
      if (box) {
        if (activeReq) {
          box.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px">
              <span class="dot on"></span>
              <b style="color:var(--text)">${esc(activeReq.request_id)}</b>
              <span class="badge" style="text-transform:capitalize">${esc(activeReq.priority || 'normal')}</span>
              <span style="margin-left:auto;color:var(--dim)">Model: ${esc(activeReq.model)}</span>
            </div>
            <div style="margin-top:4px;color:var(--mute);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
              ${esc(activeReq.prompt_preview || 'Inferencing prompt...')}
            </div>
            <div style="display:flex;gap:14px;margin-top:6px;font-size:11.5px;color:var(--dim)">
              <span>Wait: <b>${fmt(activeReq.wait_ms, 0)} ms</b></span>
              <span>Tokens: <b>${activeReq.output_chunks || 0}</b></span>
              <span>Deadline: <b>${fmt(activeReq.deadline_ms, 0)} ms</b></span>
            </div>`;
        } else {
          box.innerHTML = `<span style="color:var(--mute)">No active request (idle). Ready to dispatch next queued task.</span>`;
        }
      }

      const countLbl = $("#lbl-live-q-count");
      if (countLbl) countLbl.textContent = `${queuedItems.length} waiting`;

      const tbody = $("#tbl-queue-inspector tbody");
      if (tbody) {
        if (!queuedItems || queuedItems.length === 0) {
          tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:24px 0">Queue is empty. Submit a prompt to schedule.</td></tr>`;
        } else {
          tbody.innerHTML = queuedItems.map((q, idx) => `
            <tr>
              <td><b>#${idx + 1}</b></td>
              <td><code>${esc(q.request_id)}</code></td>
              <td>Sub #${q.arrival_index}</td>
              <td><span class="badge" style="text-transform:capitalize">${esc(q.priority)}</span></td>
              <td>${fmt(q.deadline_ms, 0)} ms</td>
              <td>${fmt(q.queue_wait_ms, 0)} ms</td>
              <td><button class="btn icon" style="padding:3px 7px;font-size:11px" onclick="cancelLiveReq('${q.request_id}')">Cancel</button></td>
            </tr>
          `).join("");
        }
      }
    }

    function renderEventsFeed(events) {
      const feed = $("#live-events-feed");
      if (!feed) return;
      if (!events || events.length === 0) {
        feed.innerHTML = `<div class="com">Waiting for scheduler events...</div>`;
        return;
      }

      feed.innerHTML = events.slice(0, 40).map(ev => {
        const time = ev.time_str || "";
        const rid = ev.request_id ? esc(ev.request_id) : "";
        if (ev.type === "enqueued") {
          return `<div class="arr">[${time}] 📥 ENQUEUED ${rid} (${esc(ev.priority || 'normal')}, SLA: ${fmt(ev.deadline_ms, 0)}ms)</div>`;
        } else if (ev.type === "dispatched") {
          return `<div class="dis" style="font-weight:700">[${time}] 🚀 DISPATCHED ${rid} (Order #${ev.dispatch_index}, wait: ${fmt(ev.wait_ms, 0)}ms, ${ev.policy})</div>`;
        } else if (ev.type === "first_token") {
          return `<div class="com" style="color:var(--brand-2)">[${time}] ⚡ FIRST TOKEN ${rid} (TTFO: ${fmt(ev.ttfo_ms, 0)}ms)</div>`;
        } else if (ev.type === "completed") {
          const ok = !ev.deadline_missed;
          return `<div class="${ok ? 'arr' : 'pre'}" style="font-weight:700">[${time}] ${ok ? '✅' : '⚠️'} COMPLETED ${rid} (Duration: ${fmt(ev.duration_ms, 0)}ms, ${ok ? 'Met SLA' : 'MISSED SLA'})</div>`;
        } else if (ev.type === "policy_changed") {
          return `<div class="dis" style="color:var(--warn)">[${time}] ⚙️ POLICY CHANGED: ${esc(ev.old_policy)} ➔ ${esc(ev.policy)}</div>`;
        } else if (ev.type === "failed") {
          return `<div class="pre">[${time}] ❌ FAILED ${rid}: ${esc(ev.error || '')}</div>`;
        } else if (ev.type === "cancelled") {
          return `<div class="com">[${time}] 🚫 CANCELLED ${rid}</div>`;
        }
        return `<div class="com">[${time}] ${esc(ev.type)} ${rid}</div>`;
      }).join("");
    }

    // Modal for custom prompt injection
    function openCustomPromptModal() {
      const m = modal(`
        <button class="btn icon x" data-close aria-label="Close">${icon("x")}</button>
        <h2>Submit Live Request</h2>
        <p style="color:var(--mute);margin:0 0 16px">Enqueue a prompt with specific priority and SLA deadline budget.</p>
        <form id="frm-custom-req" class="grid" style="gap:14px">
          <label class="fld">
            Prompt
            <textarea name="prompt" rows="3" style="width:100%;resize:vertical" required>What is priority inversion in operating systems?</textarea>
          </label>
          <div class="grid g2">
            <label class="fld">
              Priority
              <select name="priority">
                <option value="high">High</option>
                <option value="normal" selected>Normal</option>
                <option value="low">Low</option>
              </select>
            </label>
            <label class="fld">
              SLA Deadline (ms)
              <input type="number" name="deadline_ms" value="5000" min="200" max="60000" step="500">
            </label>
          </div>
          <div class="row" style="justify-content:flex-end;margin-top:10px">
            <button type="button" class="btn" data-close>Cancel</button>
            <button type="submit" class="btn primary" id="btn-submit-custom">${icon("play")}Enqueue Request</button>
          </div>
        </form>
      `);

      $("#frm-custom-req", m.el).onsubmit = async e => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        const btn = $("#btn-submit-custom", m.el);
        btn.disabled = true;
        try {
          const res = await fetch("/api/live/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt: f.prompt,
              model: "llama3.2:latest",
              priority: f.priority,
              deadline_ms: Number(f.deadline_ms),
              policy: selPolicy ? selPolicy.value : "fcfs"
            })
          });
          const data = await res.json();
          if (res.ok) {
            toast(`Enqueued ${data.request_id} at position #${data.queue_position}`);
            m.close();
            fetchTimeline();
          } else {
            toast(data.error || "Submission failed", "err");
            btn.disabled = false;
          }
        } catch (err) {
          toast(err.message, "err");
          btn.disabled = false;
        }
      };
    }

    // Initial fetch and start interval
    await fetchTimeline();
    livePollTimer = setInterval(fetchTimeline, 1500);
  }

  // Window helper for queue cancellation button
  window.cancelLiveReq = async rid => {
    try {
      const res = await fetch("/api/live/cancel/" + rid, { method: "POST" });
      const data = await res.json();
      if (data.ok) toast(`Cancelled request ${rid}`);
      else toast(`Could not cancel ${rid} (may have already dispatched)`, "warn");
    } catch (e) {
      toast("Cancel error: " + e.message, "err");
    }
  };

  // =========================================================================
  // 2. Discrete-Time Simulation Replay Mode (Preserved from teammate implementation)
  // =========================================================================
  function renderSimMode() {
    const container = $("#arena-body");
    if (!container || dead) return;

    let R = null, T = 0, END = 1, playing = false, last = 0, raf = 0, tab = "req", lastSlow = 0, scn = settings.scenario;
    let A = settings.left, B = settings.right;
    const opts = () => SCN().map(k => `<option value="${k}">${esc(scnName(k))}</option>`).join("") + (window.REPLAY && REPLAY.custom ? `<option value="custom">${esc(REPLAY.custom.meta.title || "Custom run")}</option>` : "");
    const sopts = sel => SCHED.map(s => `<option value="${s}" ${s === sel ? "selected" : ""}>${LABEL[s]}</option>`).join("");

    container.innerHTML = `
    <div class="tool" role="toolbar" aria-label="Playback">
      <button class="btn primary" id="play" aria-label="Play">${icon("play")}<span>Play</span></button>
      <button class="btn icon" id="restart" aria-label="Restart" data-tip="Restart">${icon("restart")}</button>
      <input class="scrub" id="scrub" type="range" min="0" max="1000" value="0" aria-label="Position">
      <div class="clock" id="clock">0:00.0</div>
      <select id="speed" aria-label="Speed"><option value="5">5x</option><option value="15">15x</option><option value="40">40x</option><option value="120">120x</option></select>
      <select id="zoom" aria-label="Core window"><option value="3">3 s</option><option value="6">6 s</option><option value="15">15 s</option><option value="40">40 s</option></select>
    </div>
    <div class="top" style="margin-bottom:16px">
      <select id="scn" aria-label="Scenario">${opts()}</select>
      <select id="selA" aria-label="Left scheduler">${sopts(A)}</select><span class="vs">vs</span><select id="selB" aria-label="Right scheduler">${sopts(B)}</select>
      <span class="sub" id="meta" style="margin-right:auto"></span>
      <button class="btn" id="new">${icon("plus")}New workload</button>
    </div>
    <div class="grid g2" id="panels"></div>
    <div class="card drawer">
      <div class="row" style="margin-bottom:14px">
        <div class="chips" id="tabs"></div>
        <span class="legend" style="margin-left:auto">
          <span><i class="sw" style="background:#8B7CFF"></i>High</span>
          <span><i class="sw" style="background:#36D6C3"></i>Normal</span>
          <span><i class="sw" style="background:#E39BFF"></i>Low</span>
          <span><i class="sw" style="background:var(--dim)"></i>Background</span>
        </span>
      </div>
      <div id="drawer" class="grid g2"></div>
    </div>`;

    $("#scn").value = scn; $("#speed").value = settings.speed; $("#zoom").value = settings.zoom;
    chips($("#tabs"), [{ k: "req", label: "Requests" }, { k: "log", label: "Decisions" }], tab, k => { tab = k; buildDrawer(); slow() });

    const names = (R, run, i) => i < R.requests.length ? R.requests[i].id : R.background[i - R.requests.length].id;
    function panels() {
      const keys = [A, B];
      $("#panels").innerHTML = keys.map((k, i) => `<div class="card panel" data-p="${i}"><header><h3><i class="sw" style="background:${col(k)};width:14px;height:14px;border-radius:5px"></i>${LABEL[k]}</h3>
        <div class="hud"><span class="pill"><span>Late</span><b data-h="late">0%</b></span><span class="pill"><span>Tokens</span><b data-h="tok">0</b></span><span class="pill"><span>Dispatches</span><b data-h="disp">0</b></span></div></header>
        <canvas class="tl" height="150" aria-label="Cores over time"></canvas><div class="cap"><span>Cores over time</span></div>
        <canvas class="sc" height="170" style="margin-top:12px" aria-label="Token latency"></canvas><div class="cap"><span>Each dot is a token: latency over deadline. Above the red line it was late.</span></div>
        <table class="tbl" style="margin-top:14px"><tbody><tr><td style="color:var(--mute)">Final: requests missing SLA</td><td><b>${fmt(R.runs[k].metrics.request_miss_rate, 0)}%</b></td></tr><tr><td style="color:var(--mute)">p99 gap</td><td><b>${fmt(R.runs[k].metrics.p99_itl_ms, 0)} ms</b></td></tr><tr><td style="color:var(--mute)">Background slowdown</td><td><b>${fmt(R.runs[k].metrics.bg_mean_slowdown, 1)}x</b></td></tr></tbody></table></div>`).join("");
      $$(".panel").forEach((p, i) => { const c = $("canvas.tl", p); onHover(c, e => Viz.hitTimeline(c, e)) });
    }
    function buildDrawer() {
      const keys = [A, B];
      $("#drawer").innerHTML = keys.map((k, i) => tab === "req" ?
        `<div data-d="${i}"><div style="font-weight:800;margin-bottom:10px"><i class="sw" style="background:${col(k)}"></i>${LABEL[k]}</div><div class="lanes">${R.requests.map(r => `<div class="lane2"><span>${esc(r.id.replace("req_", "req "))}</span><div class="trk"><i style="background:${Viz.P()[r.prio]}"></i></div><span class="n"></span></div>`).join("")}</div></div>` :
        `<div data-d="${i}"><div style="font-weight:800;margin-bottom:10px"><i class="sw" style="background:${col(k)}"></i>${LABEL[k]}</div><div class="feed"></div></div>`).join("");
    }
    function draw(full = true) {
      const win = settings.zoom * 1000;
      $$(".panel").forEach((p, i) => {
        const k = i ? B : A; Viz.timeline($("canvas.tl", p), R, k, T, { win, rowH: 28 }); Viz.scatter($("canvas.sc", p), R, k, T, { win: 40000, h: 170 });
        const n = Viz.counts(R, k, T); const hl = $('[data-h=late]', p); hl.textContent = fmt(n.pct, 1) + "%"; hl.parentNode.className = "pill " + (n.n ? (n.pct > 10 ? "bad" : "ok") : ""); $('[data-h=tok]', p).textContent = n.n; $('[data-h=disp]', p).textContent = n.disp
      });
      $("#clock").textContent = `${Math.floor(T / 60000)}:${(T / 1000 % 60).toFixed(1).padStart(4, "0")}`; $("#scrub").value = Math.round(1000 * T / END);
      if (full) slow();
    }
    function slow() {
      [A, B].forEach((k, i) => {
        const d = $(`[data-d="${i}"]`); if (!d) return; const run = Viz.prep(R, k);
        if (tab === "req") {
          $$(".lane2", d).forEach((ln, j) => {
            const a = run._per[j], m = Viz.bs(a, T, 1); let l = 0; for (let q = 0; q < m; q++)l += a[q][4]; const tot = R.requests[j].tokens + 1;
            $("i", ln).style.width = (100 * m / tot) + "%"; $(".n", ln).textContent = l ? l + " late" : ""; ln.style.opacity = T < R.requests[j].arrival_ms ? .4 : 1
          })
        }
        else {
          const kx = Viz.bs(run.events, T, 0), rows = []; for (let q = kx - 1; q >= Math.max(0, kx - 11); q--) {
            const e = run.events[q], nm = n => names(R, run, n), why = e[4] >= 0 ? run.reasons[e[4]].replace(/_/g, " ") : "", ts = (e[0] / 1000).toFixed(2) + "s";
            rows.push(e[1] === 0 ? `<div class="arr">${ts}  arrive ${nm(e[3])}</div>` : e[1] === 1 ? `<div class="dis">${ts}  core ${e[2]} runs ${nm(e[3])}${e[6] != null ? ` (laxity ${e[6]})` : ""} ${why}</div>` : e[1] === 2 ? `<div class="pre">${ts}  core ${e[2]} takes back ${nm(e[3])}${e[5] >= 0 ? " for " + nm(e[5]) : ""}</div>` : `<div class="com">${ts}  ${nm(e[3])} finished</div>`)
          }
          $(".feed", d).innerHTML = rows.join("") || `<div class="com">Nothing yet. Press play.</div>`
        }
      })
    }
    function frame(ts) {
      if (dead || !playing || mode !== "sim") return; const dt = Math.min(ts - last, 100); last = ts; T += dt * settings.speed; if (T >= END) { T = END; setPlay(false) }
      const full = ts - lastSlow > 110; if (full) lastSlow = ts; draw(full); if (playing) raf = requestAnimationFrame(frame)
    }
    function setPlay(on) {
      playing = on; $("#play").innerHTML = icon(on ? "pause" : "play") + `<span>${on ? "Pause" : T >= END ? "Replay" : "Play"}</span>`; if (on) { if (T >= END) T = 0; last = performance.now(); raf = requestAnimationFrame(frame) } else cancelAnimationFrame(raf)
    }
    async function load(key, data) {
      scn = key; R = data || await replay(key);
      END = Math.max(...Object.values(R.runs).map(r => Math.max(...r.tokens.map(t => t[1])))) + 500; T = 0;
      $("#meta").textContent = `${R.requests.length} requests, ${R.background.length} background jobs, ${R.meta.cores} cores, seed ${R.meta.seed}, load ${fmt(R.meta.offered_load, 2)}`;
      $("#scn").innerHTML = opts(); $("#scn").value = key; panels(); buildDrawer(); setPlay(false); draw()
    }

    $("#play").onclick = () => setPlay(!playing);
    $("#restart").onclick = () => { T = 0; draw() };
    $("#scrub").oninput = e => { T = END * e.target.value / 1000; draw() };
    $("#speed").onchange = e => { settings.speed = +e.target.value; saveSettings() };
    $("#zoom").onchange = e => { settings.zoom = +e.target.value; saveSettings(); draw() };
    $("#scn").onchange = async e => { const v = e.target.value; if (v !== "custom") { settings.scenario = v; saveSettings() } await load(v, v === "custom" ? REPLAY.custom : null) };
    $("#selA").onchange = e => { A = e.target.value; settings.left = A; saveSettings(); panels(); buildDrawer(); draw() };
    $("#selB").onchange = e => { B = e.target.value; settings.right = B; saveSettings(); panels(); buildDrawer(); draw() };
    $("#new").onclick = () => newRunModal(async data => { (window.REPLAY = window.REPLAY || {}).custom = data; await load("custom", data) });

    load(settings.scenario || "heavy");
  }

  // Initial render
  switchMode();

  return () => {
    dead = true;
    if (livePollTimer) clearInterval(livePollTimer);
  };
});

/* New workload dialog (shared by Arena and Experiments). onData(replayJson) */
function newRunModal(onData) {
  const m = modal(`<button class="btn icon x" data-close aria-label="Close">${icon("x")}</button><h2>New workload</h2><p style="color:var(--mute);margin:0 0 16px">Fresh trace, six schedulers, same jobs.</p>
   <form id="nf" class="grid g2" style="gap:14px">
    <label class="fld">Cores<input type="number" name="cores" value="4" min="1" max="16"></label>
    <label class="fld">Requests<input type="number" name="requests" value="10" min="2" max="30"></label>
    <label class="fld">Arrivals per second<input type="number" name="rps" value="1.2" min="0.2" max="6" step="0.1"></label>
    <label class="fld">Seed<input type="number" name="seed" value="${Math.floor(2 + Math.random() * 97)}" min="1" max="999"></label>
    <label class="fld" style="grid-column:1/-1">Background jobs<select name="background"><option value="none">None</option><option value="light_load">Light</option><option value="mixed_load" selected>Mixed</option><option value="heavy_load">Heavy</option></select></label>
    <div class="row" style="grid-column:1/-1;justify-content:flex-end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" id="go">${icon("play")}Run</button></div>
   </form>`);
  $("#nf", m.el).onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)), b = $("#go", m.el); b.disabled = true; b.lastChild.textContent = "Simulating...";
    try {
      const d = await apiRun(f); d.meta.title = "Custom run"; const h = Store.get("history", []);
      const tls = d.runs.token_laxity.metrics.request_miss_rate, others = Object.entries(d.runs).filter(([k]) => k !== "token_laxity").map(([k, v]) => [k, v.metrics.request_miss_rate]).sort((a, b) => a[1] - b[1])[0];
      h.unshift({ id: Date.now(), ts: new Date().toISOString(), params: f, tls, best: others[0], bestVal: others[1] }); Store.set("history", h.slice(0, 30));
      m.close(); toast("Run finished"); onData(d)
    }
    catch (err) { b.disabled = false; b.lastChild.textContent = "Run"; toast(serverUp === false ? "Start the server to run new workloads: python server.py" : err.message, "err") }
  };
}
