"""
Token-Laxity Scheduler (TLS) - the LLM-aware scheduling policy.

Part of Member 2: Core Scheduling Algorithm.

IDEA
----
Classic EDF/LLF schedulers reason about a job's *completion* deadline. An LLM
decode stream has no single completion deadline: the user-visible quality metric is
that EVERY token arrives within an inter-token latency (ITL) target. TLS therefore
treats the *next-token* deadline as a first-class scheduling quantity and derives
"laxity" from the request's own token-generation rate.

1. NEXT-TOKEN DEADLINE
       decode  : d_i = t_last_token + D_i                (D_i = ITL target)
       prefill : d_i = t_arrival    + B_i                (B_i = TTFT budget)

2. TOKEN-RATE SERVICE ESTIMATE (strictly causal, uses only past tokens)
   Each task's per-token CPU service time c_i is tracked with an EWMA, and its
   jitter with an EWMA of absolute deviation:
       c_i   <- a*s_k + (1-a)*c_i          (s_k = CPU time token k really needed)
       sig_i <- a*|s_k - c_i_old| + (1-a)*sig_i
   i.e. 1/c_i is the task's token-generation rate per unit of CPU time.
   Prefill uses  c = P0 + p * prompt_tokens  with p learned online.

3. TOKEN LAXITY (ms) and normalised laxity
       c_rem = max(c_i - progress_in_current_token, 0.1*c_i)
       L_i   = (d_i - now) - c_rem - kappa*sig_i            (jitter-guarded slack)
       lam_i = L_i / D_i                                    (SLA-relative; comparable
                                                             across 30ms and 100ms tasks)
   L_i < 0 means: even if given a core right now, the token is expected to be late.

4. RATE-BASED CAPACITY PLANNER (overload protection)
   To sustain its SLA token rate a stream needs a CPU share
       u_i = min(1, (c_i + kappa*sig_i) / D_i)
   Laxity ordering alone collapses under overload (everyone ends up slightly late).
   So streams are admitted to a PROTECTED set P in order (class priority, then
   arrival) while  sum(u_i) <= rho * num_cores. Streams outside P are best-effort:
   they only use otherwise idle cores, plus an anti-starvation guard that grants one
   token after `starve_limit_ms` without progress. P is recomputed every tick with a
   small hysteresis so streams do not flap in and out. A higher-class arrival can
   push a lower-class stream out of P. Before a stream has any decode history, u uses
   a global EWMA of observed c/D over all streams (still strictly past data).

5. URGENCY KEY (lower = more urgent) - a single scale for ALL tasks
       protected  : key = lam_i - bonus(class) - beta * miss_ewma_i
       best-effort: key = K_be (+ arrival order)
       background : key = K_bg - r_bg * (seconds since it last ran)     [aging]
   Background jobs get a *pseudo-deadline*: their key starts above any inference
   key (so they yield) and decays while they wait, so they are never starved forever.

6. PREEMPTION CONTROLLER - decides WHEN and BY HOW MUCH
   A waiting task W may displace a running task R only if ALL hold:
     (gate)     W is protected AND urgent: lam_W < lambda_soft  (or W is an aged
                background job / a starving best-effort stream)
     (margin)   key_R - key_W >= margin_soft                      -> avoids thrashing
     (boundary) R is background / best-effort, OR R sits at a token boundary (just
                emitted), OR W is critical (lam_W < lambda_crit) and
                key_R - key_W >= margin_hard (mid-token preemption)
     (min-run)  R has run >= min_run_ms since dispatch            -> bounds switch cost
   "By how much": an urgent inference task is granted exactly the CPU it needs to emit
   its next token. Right after the token its laxity jumps back to ~1 - c/D, so its key
   rises and it yields at its next token boundary - the boost is self-limiting, and
   the victim (usually a background job) resumes. Depth of preemption scales with
   urgency: comfortable -> never preempts; urgent -> preempts background tasks and
   other inference tasks at token boundaries; critical -> may preempt mid-token.
   Idle cores are always filled first (work-conserving).

Only past observations feed the estimators - the policy never peeks at future tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from scheduler_base import Assignment, Scheduler, place
from task_model import Core, Task


@dataclass
class TLSConfig:
    # --- token-rate estimator ---
    ewma_alpha: float = 0.30            # smoothing of per-token service time
    jitter_kappa: float = 1.0           # safety margin = kappa * sigma
    prior_service_fraction: float = 0.7  # initial global c/D prior (then learned online)
    prior_alpha: float = 0.05           # smoothing of the global c/D prior
    prefill_base_ms: float = 35.0       # P0: fixed prefill overhead
    prefill_ms_per_token: float = 0.18  # p: initial per-prompt-token prefill cost (learned)
    prefill_alpha: float = 0.30
    default_prefill_ms: float = 100.0   # when prompt length is unknown
    # --- urgency key ---
    class_bonus: Dict[str, float] = field(default_factory=lambda: {"high": 0.25, "normal": 0.10, "low": 0.0})
    miss_beta: float = 0.15             # weight of recent violation rate
    miss_alpha: float = 0.20
    # --- rate-based capacity planner ---
    util_bound: float = 0.80            # rho: protect streams while sum(u) <= rho * cores
    admit_hysteresis: float = 0.05      # already-protected streams may exceed the bound by 5%
    besteffort_key: float = 1.5
    starve_limit_ms: float = 1500.0
    # --- background pseudo-deadline ---
    bg_base_key: float = 3.0
    bg_aging_per_s: float = 2.0
    # --- preemption controller ---
    lambda_soft: float = 0.60           # waiting inference task may preempt only below this
    lambda_crit: float = 0.10           # critical: may preempt mid-token
    margin_soft: float = 0.30
    margin_hard: float = 0.60
    min_run_ms: float = 3.0
    bg_min_slice_ms: float = 8.0
    boundary_tol_ms: float = 1.5        # progress below this = "at a token boundary"


@dataclass
class _State:
    c_hat: Optional[float] = None
    sigma: float = 0.0
    miss: float = 0.0


@dataclass
class _View:
    key: float
    lam: float = 0.0          # normalised laxity
    laxity_ms: float = 0.0
    pred_ms: float = 0.0
    critical: bool = False
    besteffort: bool = False
    starving: bool = False
    is_bg: bool = False


class TokenLaxityScheduler(Scheduler):
    name = "token_laxity"
    description = "Token-Laxity Scheduler: rate-based next-token laxity + urgency-scaled preemption"

    def __init__(self, config: Optional[TLSConfig] = None, **overrides):
        self.cfg = config or TLSConfig()
        for k, v in overrides.items():
            if not hasattr(self.cfg, k):
                raise ValueError(f"Unknown TLS parameter '{k}'")
            setattr(self.cfg, k, v)
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.state: Dict[str, _State] = {}
        self.prefill_p = self.cfg.prefill_ms_per_token
        self.prefill_mean = self.cfg.default_prefill_ms
        self.u_prior = self.cfg.prior_service_fraction
        self.admitted: set = set()

    def on_arrival(self, task, now_ms):
        self.state[task.task_id] = _State()

    def on_complete(self, task, now_ms):
        self.state.pop(task.task_id, None)

    def on_token_emit(self, task, now_ms, latency_ms, service_ms, token_index):
        cfg = self.cfg
        if token_index == 0:                                   # prefill finished: learn prefill cost
            cfg_a = cfg.prefill_alpha
            if task.prompt_tokens > 0:
                obs = max(0.0, (service_ms - cfg.prefill_base_ms) / task.prompt_tokens)
                self.prefill_p = cfg_a * obs + (1 - cfg_a) * self.prefill_p
            self.prefill_mean = cfg_a * service_ms + (1 - cfg_a) * self.prefill_mean
            return
        st = self.state[task.task_id]
        a = cfg.ewma_alpha
        self.u_prior = cfg.prior_alpha * (service_ms / max(task.itl_deadline_ms, 1.0)) \
            + (1 - cfg.prior_alpha) * self.u_prior
        if st.c_hat is None:
            st.c_hat = service_ms
        else:
            st.sigma = a * abs(service_ms - st.c_hat) + (1 - a) * st.sigma
            st.c_hat = a * service_ms + (1 - a) * st.c_hat
        st.miss = cfg.miss_alpha * (1.0 if latency_ms > task.itl_deadline_ms else 0.0) \
            + (1 - cfg.miss_alpha) * st.miss

    # --------------------------------------------------------------- estimator
    def predict_service(self, task: Task) -> Tuple[float, float]:
        """(expected CPU ms for the current burst, jitter sigma) - causal."""
        if task.burst_idx == 0:                                 # prefill
            if task.prompt_tokens > 0:
                return self.cfg.prefill_base_ms + self.prefill_p * task.prompt_tokens, 0.0
            return self.prefill_mean, 0.0
        st = self.state[task.task_id]
        if st.c_hat is None:
            return self.u_prior * task.itl_deadline_ms, 0.0
        return st.c_hat, st.sigma

    def required_share(self, task: Task) -> float:
        """u_i: fraction of one core needed to sustain the task's SLA token rate."""
        st = self.state[task.task_id]
        D = max(task.itl_deadline_ms, 1.0)
        if st.c_hat is None:
            return min(1.0, self.u_prior)
        return min(1.0, (st.c_hat + self.cfg.jitter_kappa * st.sigma) / D)

    def _plan(self, runnable: List[Task], num_cores: int) -> set:
        """Protected set P: greedy first-fit by (class, arrival) under sum(u) <= rho*M."""
        cfg = self.cfg
        cap = cfg.util_bound * num_cores
        order = sorted((t for t in runnable if t.is_inference),
                       key=lambda t: (-t.priority_rank, t.arrival_ms, t.task_id))
        admitted, used = set(), 0.0
        for t in order:
            u = self.required_share(t)
            limit = cap * (1.0 + cfg.admit_hysteresis) if t.task_id in self.admitted else cap
            if not admitted or used + u <= limit:
                admitted.add(t.task_id)
                used += u
        return admitted

    # -------------------------------------------------------------------- view
    def _view(self, t: Task, now: float) -> _View:
        cfg = self.cfg
        if not t.is_inference:
            return _View(key=cfg.bg_base_key - cfg.bg_aging_per_s * t.wait_ms(now) / 1000.0, is_bg=True)

        D = max(t.ttft_budget_ms if t.burst_idx == 0 else t.itl_deadline_ms, 1.0)
        c_hat, sigma = self.predict_service(t)
        c_rem = max(c_hat - t.burst_done_ms, 0.1 * c_hat)
        laxity = (t.next_deadline_ms() - now) - c_rem - cfg.jitter_kappa * sigma
        lam = laxity / D

        if t.task_id in self.admitted:
            key = (lam - cfg.class_bonus.get(t.priority_label, 0.0)
                   - cfg.miss_beta * self.state[t.task_id].miss)
            return _View(key=key, lam=lam, laxity_ms=laxity, pred_ms=c_hat, critical=lam < cfg.lambda_crit)

        starving = (now - t.last_emit_ms) > cfg.starve_limit_ms
        key = -2.0 if starving else cfg.besteffort_key + 1e-6 * t.arrival_ms
        return _View(key=key, lam=lam, laxity_ms=laxity, pred_ms=c_hat, besteffort=True, starving=starving)

    # -------------------------------------------------------- preemption rules
    def _may_preempt(self, w: Task, vw: _View, r: Task, vr: _View) -> Optional[str]:
        """Return a reason code if W may displace R, else None."""
        cfg = self.cfg
        if not vw.is_bg:
            if vw.besteffort and not vw.starving:
                return None                                     # unprotected streams never preempt
            if not vw.besteffort and vw.lam >= cfg.lambda_soft:
                return None                                     # comfortable: wait for a free core
        min_run = cfg.bg_min_slice_ms if vr.is_bg else cfg.min_run_ms
        if r.run_since_dispatch_ms < min_run:
            return None
        margin = vr.key - vw.key
        if margin < cfg.margin_soft:
            return None
        if vr.is_bg or vr.besteffort:                           # victim has no latency guarantee
            if vw.is_bg:
                return "bg_aging_rotation"
            return "preempt_background_critical" if vw.critical else (
                "preempt_background" if vr.is_bg else "preempt_besteffort")
        if r.burst_done_ms <= cfg.boundary_tol_ms:              # protected victim at a token boundary
            return "bg_starvation_guard" if vw.is_bg else (
                "anti_starvation_token" if vw.starving else "preempt_token_boundary")
        if vw.critical and margin >= cfg.margin_hard:           # mid-token: only for critical
            return "preempt_critical_mid_token"
        return None

    # ----------------------------------------------------------------- decide
    def decide(self, now_ms: float, runnable: List[Task], cores: List[Core]) -> Dict[int, Assignment]:
        self.admitted = self._plan(runnable, len(cores))
        views = {t.task_id: self._view(t, now_ms) for t in runnable}

        def asg(t: Task, reason: str, displaced: str = "") -> Assignment:
            v = views[t.task_id]
            if v.is_bg:
                detail = f"bg_key={v.key:.2f}"
            else:
                detail = f"lam={v.lam:.2f} L={v.laxity_ms:.1f}ms key={v.key:.2f}" + (
                    " best-effort" if v.besteffort else " protected")
            if v.is_bg:
                return Assignment(t, reason, detail, displaced, {"key": round(v.key, 4)})
            return Assignment(t, reason, detail, displaced, {
                "laxity_ms": round(v.laxity_ms, 3), "norm_laxity": round(v.lam, 4),
                "key": round(v.key, 4), "pred_service_ms": round(v.pred_ms, 3)})

        running = [c.current for c in cores if c.current is not None]
        selected: List[Assignment] = [Assignment(t, "keep") for t in running]
        waiting = sorted((t for t in runnable if t.running_core is None),
                         key=lambda t: views[t.task_id].key)

        fresh = set()
        free = len(cores) - len(running)
        for t in waiting[:free]:                                # work-conserving fill
            v = views[t.task_id]
            reason = ("anti_starvation_token" if v.starving else
                      "fill_idle_core_critical" if v.critical else "fill_idle_core")
            selected.append(asg(t, reason))
            fresh.add(t.task_id)

        for w in waiting[free:]:                                # preemption phase
            vw = views[w.task_id]
            best_i, best_reason, best_key = -1, None, -1e18
            for i, a in enumerate(selected):
                r = a.task
                if r.task_id in fresh:
                    continue
                vr = views[r.task_id]
                reason = self._may_preempt(w, vw, r, vr)
                if reason and vr.key > best_key:                # evict the most comfortable victim
                    best_i, best_reason, best_key = i, reason, vr.key
            if best_i >= 0:
                victim = selected[best_i].task
                selected[best_i] = asg(w, best_reason, displaced=victim.task_id)
                selected[best_i].detail += f" | evicts {victim.task_id} (key={best_key:.2f})"
                selected[best_i].info["victim_key"] = round(best_key, 4)
                fresh.add(w.task_id)
        return place(selected, cores)
