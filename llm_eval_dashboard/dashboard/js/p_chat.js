/* Live Chat Page: Real Ollama Streaming Inference with Application-Level Workload Scheduling */
route("chat", async view => {
  let convId = Store.get("chat_conv_id", "conv_" + Date.now());
  Store.set("chat_conv_id", convId);

  let selectedModel = Store.get("chat_model", "llama3.2:latest");
  let selectedPolicy = Store.get("chat_policy", "fcfs");
  let selectedPriority = Store.get("chat_priority", "normal");
  let targetDeadline = Store.get("chat_deadline", 8000);

  // Map of active request streams: requestId -> { eventSource, bubble, status, arrivalIndex }
  const activeStreams = new Map();
  let pollTimer = null;
  let dead = false;

  view.innerHTML = `
  <div class="top">
    <div>
      <h1 style="display:flex;align-items:center;gap:10px">
        ${icon("chat")}Live Chat & Scheduler
      </h1>
      <div class="sub">Real Ollama inference governed by application-level queue scheduling</div>
    </div>
    <div class="row" style="margin-left:auto">
      <span class="status" id="ollama-badge" style="padding:6px 12px">
        <span class="dot off" id="ollama-dot"></span>
        <span id="ollama-txt">Checking Ollama...</span>
      </span>
      <button class="btn" id="btn-clear-chat">${icon("trash")}Clear</button>
      <a class="btn" id="btn-export-eval" href="/api/live/export?format=csv" download="live_chat_eval.csv">${icon("dl")}Export CSV</a>
    </div>
  </div>

  <div class="grid g21" style="align-items:start">
    <!-- Chat Column -->
    <div class="card" style="display:flex;flex-direction:column;height:calc(100vh - 190px);min-height:560px;padding:0;overflow:hidden">
      <!-- Chat Controls Header -->
      <div class="chat-controls" style="display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--surface2);flex-wrap:wrap">
        <label class="fld" style="font-size:12px;margin:0">
          Model
          <select id="sel-model" style="min-width:140px"></select>
        </label>
        <label class="fld" style="font-size:12px;margin:0">
          Dispatch Policy
          <select id="sel-policy">
            <option value="fcfs">FCFS (FIFO Arrival)</option>
            <option value="priority">Priority (Static High > Low)</option>
            <option value="edf">EDF (Earliest Deadline)</option>
            <option value="token_laxity">Token Laxity (Dynamic TLS)</option>
          </select>
        </label>
        <label class="fld" style="font-size:12px;margin:0">
          Priority
          <select id="sel-priority">
            <option value="high">High</option>
            <option value="normal" selected>Normal</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label class="fld" style="font-size:12px;margin:0">
          SLA Deadline (ms)
          <input type="number" id="inp-deadline" value="${targetDeadline}" min="100" max="120000" step="500" style="width:100px">
        </label>
      </div>

      <!-- Messages Stream Area -->
      <div id="chat-messages" style="flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:14px">
        <div class="chat-bubble system">
          <div style="font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:6px">${icon("info")}Concurrent Request Scheduling Ready</div>
          <span>You can send multiple prompts consecutively before earlier requests finish. Every request is enqueued and dispatched strictly according to the active scheduler policy. Each response displays its submission order, actual dispatch order, queue wait time, TTFT, and SLA attainment.</span>
        </div>
      </div>

      <!-- Input Bar (Never locked!) -->
      <form id="chat-form" style="display:flex;gap:10px;padding:14px;border-top:1px solid var(--line);background:var(--surface)">
        <textarea id="chat-input" placeholder="Type a prompt to schedule and stream (or submit multiple concurrently)..." rows="2" style="flex:1;background:var(--bg2);border:1px solid var(--line);border-radius:12px;color:var(--text);padding:10px 14px;font:inherit;resize:none" required></textarea>
        <button type="submit" class="btn primary" id="btn-send" style="align-self:flex-end;height:44px;padding:0 20px">
          ${icon("play")}<span>Send</span>
        </button>
      </form>
    </div>

    <!-- Live Queue & Performance Metrics Drawer -->
    <div style="display:flex;flex-direction:column;gap:18px">
      <!-- Active Queue Inspector Card -->
      <div class="card">
        <h3>${icon("cpu")}Request Queue <small id="lbl-queue-count">0 waiting</small></h3>
        <div id="active-req-slot" style="margin-bottom:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface2)">
          <div style="font-size:11px;color:var(--dim);text-transform:uppercase;font-weight:700">Currently Inferencing</div>
          <div id="active-req-info" style="font-size:13px;color:var(--mute);margin-top:4px">No active request (idle)</div>
        </div>
        <div style="font-size:12px;font-weight:700;color:var(--mute);margin-bottom:6px">Waiting Queue</div>
        <div id="queue-list" style="display:flex;flex-direction:column;gap:6px;max-height:190px;overflow-y:auto">
          <div style="color:var(--dim);font-size:12px">Queue is empty</div>
        </div>
      </div>

      <!-- Real Workload Metrics Card -->
      <div class="card">
        <h3>${icon("gauge")}Live Metrics <small>Real Ollama</small></h3>
        <div class="grid g2" id="live-stat-grid" style="gap:10px;margin-bottom:12px">
          <div class="card stat" style="padding:10px">
            <div class="lbl">${icon("clock")}Mean Wait</div>
            <div class="val" id="stat-wait">0<small>ms</small></div>
          </div>
          <div class="card stat" style="padding:10px">
            <div class="lbl">${icon("zap")}Mean TTFT</div>
            <div class="val" id="stat-ttfo">0<small>ms</small></div>
          </div>
          <div class="card stat" style="padding:10px">
            <div class="lbl">${icon("gauge")}Mean ICL</div>
            <div class="val" id="stat-icl">0<small>ms</small></div>
          </div>
          <div class="card stat" style="padding:10px">
            <div class="lbl">${icon("shield")}SLA Met</div>
            <div class="val" id="stat-sla">100<small>%</small></div>
          </div>
        </div>
        <div style="font-size:12px;font-weight:700;color:var(--mute);margin-bottom:6px">Policy Attainment Breakdown</div>
        <div id="policy-breakdown" style="display:flex;flex-direction:column;gap:5px;font-size:12px"></div>
      </div>
    </div>
  </div>
  `;

  // Restore input settings
  $("#sel-policy").value = selectedPolicy;
  $("#sel-priority").value = selectedPriority;

  // -------------------------------------------------------------------------
  // Check Ollama and Load Models
  // -------------------------------------------------------------------------
  async function refreshOllamaModels() {
    try {
      const res = await fetch("/api/ollama/models");
      const data = await res.json();
      const dot = $("#ollama-dot");
      const txt = $("#ollama-txt");
      const sel = $("#sel-model");
      if (data.connected && data.models && data.models.length > 0) {
        dot.className = "dot on";
        txt.textContent = "Ollama connected (" + data.models.length + " models)";
        sel.innerHTML = data.models.map(m => `<option value="${esc(m)}" ${m === selectedModel ? "selected" : ""}>${esc(m)}</option>`).join("");
        selectedModel = sel.value;
      } else if (data.connected) {
        dot.className = "dot off";
        txt.textContent = "Ollama online (no models pulled)";
        sel.innerHTML = `<option value="">No models installed</option>`;
      } else {
        dot.className = "dot off";
        txt.textContent = "Ollama offline (http://localhost:11434)";
        sel.innerHTML = `<option value="llama3.2:latest">llama3.2:latest (offline)</option>`;
      }
    } catch (e) {
      $("#ollama-dot").className = "dot off";
      $("#ollama-txt").textContent = "Error querying Ollama API";
    }
  }

  // -------------------------------------------------------------------------
  // Queue & Metrics Polling
  // -------------------------------------------------------------------------
  async function refreshQueueAndMetrics() {
    if (dead) return;
    try {
      // 1. Queue snapshot
      const qRes = await fetch("/api/live/queue");
      const qData = await qRes.json();
      $("#lbl-queue-count").textContent = qData.queued_count + " waiting";

      const activeDiv = $("#active-req-info");
      if (qData.active_request) {
        const ar = qData.active_request;
        activeDiv.innerHTML = `
          <div style="display:flex;align-items:center;gap:6px">
            <span class="live" style="width:8px;height:8px;background:var(--ok);border-radius:50%;display:inline-block"></span>
            <b>${esc(ar.request_id)}</b>
            <span class="badge" style="margin-left:auto">${esc(ar.policy.toUpperCase())}</span>
          </div>
          <div style="font-size:11px;color:var(--mute);margin-top:2px">Model: ${esc(ar.model)} | Wait: ${fmt(ar.wait_ms, 0)}ms</div>
        `;
      } else {
        activeDiv.innerHTML = `<span style="color:var(--dim)">No active request (idle)</span>`;
      }

      const qList = $("#queue-list");
      if (qData.queue && qData.queue.length > 0) {
        qList.innerHTML = qData.queue.map(item => `
          <div style="padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);display:flex;align-items:center;gap:8px;font-size:12px">
            <span style="font-weight:800;color:var(--brand)">#${item.queue_position}</span>
            <span style="color:var(--text)">${esc(item.request_id)}</span>
            <span class="badge" style="font-size:10px">${esc(item.priority)}</span>
            <span style="margin-left:auto;color:var(--mute)">${fmt(item.wait_ms, 0)}ms</span>
            <button class="btn icon" style="padding:2px 6px;font-size:10px" onclick="cancelChatReq('${item.request_id}')">✕</button>
          </div>
        `).join("");
      } else {
        qList.innerHTML = `<div style="color:var(--dim);font-size:12px;padding:4px 0">Queue is empty</div>`;
      }

      // Update in-flight bubble status indicators
      for (const [rid, streamInfo] of activeStreams.entries()) {
        const item = (qData.queue || []).find(i => i.request_id === rid);
        const stBadge = streamInfo.statusBadge;
        if (item) {
          if (stBadge) stBadge.innerHTML = `⏳ Queued (#${item.queue_position})`;
        } else if (qData.active_request && qData.active_request.request_id === rid) {
          if (stBadge) {
            stBadge.innerHTML = `🚀 Inferencing`;
            stBadge.className = "badge ok";
          }
        }
      }

      // 2. Aggregate evaluation metrics
      const mRes = await fetch("/api/live/metrics");
      const mData = await mRes.json();
      $("#stat-wait").innerHTML = `${fmt(mData.mean_wait_ms, 0)}<small>ms</small>`;
      $("#stat-ttfo").innerHTML = `${fmt(mData.mean_ttfo_ms, 0)}<small>ms</small>`;
      $("#stat-icl").innerHTML = `${fmt(mData.mean_icl_ms, 1)}<small>ms</small>`;
      $("#stat-sla").innerHTML = `${fmt(mData.deadline_attainment_pct, 1)}<small>%</small>`;

      const polDiv = $("#policy-breakdown");
      const byPol = mData.runs_by_policy || {};
      const polKeys = Object.keys(byPol);
      if (polKeys.length > 0) {
        polDiv.innerHTML = polKeys.map(k => {
          const p = byPol[k];
          return `<div style="display:flex;justify-content:space-between;color:var(--mute)">
            <span>${esc(k.toUpperCase())} (${p.count || p.sample_size || 0} runs):</span>
            <b style="color:${p.deadline_met_pct >= 90 ? 'var(--ok)' : 'var(--warn)'}">${p.deadline_met_pct}% SLA, ${p.mean_wait_ms}ms wait</b>
          </div>`;
        }).join("");
      } else {
        polDiv.innerHTML = `<span style="color:var(--dim)">No completed runs yet</span>`;
      }

    } catch (e) {
      console.warn("Queue refresh err", e);
    }
  }

  // -------------------------------------------------------------------------
  // Chat Rendering Helpers
  // -------------------------------------------------------------------------
  function appendUserBubble(text) {
    const wrap = $("#chat-messages");
    const div = el("div", { class: "chat-bubble user" });
    div.innerHTML = `<div class="content">${esc(text)}</div>`;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
  }

  function appendAssistantBubble(reqId, priority, policy, deadlineMs) {
    const wrap = $("#chat-messages");
    const div = el("div", { class: "chat-bubble assistant", id: "bubble-" + reqId });
    const prioCol = priority === "high" ? "#D58CFF" : (priority === "low" ? "#E39BFF" : "#36D6C3");

    div.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:11px;color:var(--dim);border-bottom:1px solid var(--line);padding-bottom:4px">
        <b>${esc(reqId)}</b>
        <span class="badge" style="background:${prioCol}22;color:${prioCol};text-transform:capitalize">${esc(priority)}</span>
        <span>${esc(policy.toUpperCase())}</span>
        <span>SLA: ${fmt(deadlineMs, 0)}ms</span>
        <span class="badge warn bubble-st-badge" style="margin-left:auto">⏳ Queued</span>
        <button class="btn icon btn-bubble-cancel" style="padding:1px 5px;font-size:10px" title="Cancel request">✕</button>
      </div>
      <div class="content"><span class="cursor-typing">Waiting in queue...</span></div>
      <div class="metrics-bar" style="display:none;margin-top:8px;padding-top:6px;border-top:1px solid var(--line);font-size:11px;color:var(--dim);gap:12px;flex-wrap:wrap"></div>
    `;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
    return div;
  }

  // -------------------------------------------------------------------------
  // Send & Stream Chat Request (Concurrent support!)
  // -------------------------------------------------------------------------
  async function submitChat(promptText) {
    selectedModel = $("#sel-model").value;
    selectedPolicy = $("#sel-policy").value;
    selectedPriority = $("#sel-priority").value;
    targetDeadline = parseFloat($("#inp-deadline").value) || 8000;

    Store.set("chat_model", selectedModel);
    Store.set("chat_policy", selectedPolicy);
    Store.set("chat_priority", selectedPriority);
    Store.set("chat_deadline", targetDeadline);

    appendUserBubble(promptText);

    try {
      const resp = await fetch("/api/live/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: promptText,
          model: selectedModel,
          policy: selectedPolicy,
          priority: selectedPriority,
          deadline_ms: targetDeadline,
          conversation_id: convId,
        }),
      });

      const submitData = await resp.json();
      if (!resp.ok) {
        throw new Error(submitData.error || ("Server returned " + resp.status));
      }

      const reqId = submitData.request_id;
      const bubble = appendAssistantBubble(reqId, selectedPriority, selectedPolicy, targetDeadline);
      const contentSpan = $(".content", bubble);
      const metricsDiv = $(".metrics-bar", bubble);
      const statusBadge = $(".bubble-st-badge", bubble);
      const cancelBtn = $(".btn-bubble-cancel", bubble);

      cancelBtn.onclick = () => window.cancelChatReq(reqId);

      // Connect to Server-Sent Events stream for this specific request
      let fullText = "";
      const eventSource = new EventSource("/api/live/stream/" + reqId);

      activeStreams.set(reqId, {
        eventSource,
        bubble,
        statusBadge,
        cancelBtn,
      });

      eventSource.onmessage = function (e) {
        try {
          const chunkData = JSON.parse(e.data);
          if (chunkData.type === "chunk") {
            if (!fullText) {
              contentSpan.textContent = "";
              if (statusBadge) {
                statusBadge.innerHTML = `🚀 Streaming`;
                statusBadge.className = "badge ok";
              }
              cancelBtn.style.display = "none";
            }
            fullText += chunkData.text;
            contentSpan.textContent = fullText;
            $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
          } else if (chunkData.type === "done") {
            eventSource.close();
            activeStreams.delete(reqId);
            cancelBtn.style.display = "none";

            const m = chunkData.metrics || {};
            metricsDiv.style.display = "flex";
            const slaColor = m.deadline_missed ? "var(--late)" : "var(--ok)";
            const slaTxt = m.deadline_missed ? "✕ SLA Missed" : "✓ SLA Met";

            if (statusBadge) {
              statusBadge.innerHTML = slaTxt;
              statusBadge.className = "badge " + (m.deadline_missed ? "bad" : "ok");
            }

            metricsDiv.innerHTML = `
              <span class="badge" style="background:${slaColor};color:#fff;font-weight:700">${slaTxt}</span>
              <span>Wait: <b>${m.queue_wait_ms} ms</b></span>
              <span>TTFT: <b>${m.ttfo_ms} ms</b></span>
              <span>Avg ICL: <b>${m.average_icl_ms} ms</b></span>
              <span>Total: <b>${fmt(m.generation_duration_ms / 1000, 2)} s</b></span>
              <span>Tokens: <b>${m.total_chunks}</b></span>
            `;
            refreshQueueAndMetrics();
          } else if (chunkData.type === "error" || chunkData.type === "cancelled") {
            eventSource.close();
            activeStreams.delete(reqId);
            cancelBtn.style.display = "none";
            if (statusBadge) {
              statusBadge.innerHTML = chunkData.type.toUpperCase();
              statusBadge.className = "badge bad";
            }
            contentSpan.innerHTML = `<span style="color:var(--late)">[${chunkData.type.toUpperCase()}] ${esc(chunkData.message || 'Execution error')}</span>`;
            refreshQueueAndMetrics();
          }
        } catch (err) {
          console.warn("Parse chunk err", err);
        }
      };

      eventSource.onerror = function () {
        eventSource.close();
        activeStreams.delete(reqId);
      };

      refreshQueueAndMetrics();

    } catch (err) {
      toast("Failed to submit prompt: " + err.message, "err");
    }
  }

  // Window helper for queue cancellation
  window.cancelChatReq = async rid => {
    try {
      await fetch("/api/live/cancel/" + rid, { method: "POST" });
      const stream = activeStreams.get(rid);
      if (stream && stream.eventSource) {
        stream.eventSource.close();
        activeStreams.delete(rid);
      }
      toast(`Cancelled request ${rid}`);
      refreshQueueAndMetrics();
    } catch (e) {
      toast("Cancel error", "err");
    }
  };

  // -------------------------------------------------------------------------
  // Event Bindings
  // -------------------------------------------------------------------------
  $("#chat-form").onsubmit = e => {
    e.preventDefault();
    const input = $("#chat-input");
    const val = input.value.trim();
    if (!val) return;
    input.value = "";
    submitChat(val);
  };

  $("#btn-clear-chat").onclick = async () => {
    try {
      await fetch("/api/live/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: convId }),
      });
      convId = "conv_" + Date.now();
      Store.set("chat_conv_id", convId);
      $("#chat-messages").innerHTML = `
        <div class="chat-bubble system">
          <div style="font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:6px">${icon("info")}Conversation Cleared</div>
          <span>New session ID initialized. Past turns cleared from memory.</span>
        </div>
      `;
      toast("Conversation history reset");
    } catch (e) {
      toast("Failed to clear", "err");
    }
  };

  $("#sel-policy").onchange = async e => {
    selectedPolicy = e.target.value;
    Store.set("chat_policy", selectedPolicy);
    try {
      await fetch("/api/live/policy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: selectedPolicy }),
      });
      toast(`Default policy updated to ${selectedPolicy.toUpperCase()}`);
      refreshQueueAndMetrics();
    } catch (err) {
      console.warn("Policy update err", err);
    }
  };

  // Keyboard shortcut: Ctrl+Enter or Cmd+Enter to send
  $("#chat-input").onkeydown = e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      $("#chat-form").dispatchEvent(new Event("submit"));
    }
  };

  // Initial load & periodic poll
  await refreshOllamaModels();
  await refreshQueueAndMetrics();
  pollTimer = setInterval(refreshQueueAndMetrics, 1500);

  return () => {
    dead = true;
    if (pollTimer) clearInterval(pollTimer);
    for (const [rid, s] of activeStreams.entries()) {
      if (s.eventSource) s.eventSource.close();
    }
    activeStreams.clear();
  };
});
