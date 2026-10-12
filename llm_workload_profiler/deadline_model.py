"""
Token Deadline and Risk Model Module for LLM Inference Workloads.

Part of Member 1: Workload Simulation & Latency Profiling Module.
Project: LLM-Aware OS Scheduler for Local AI Inference Workloads.

This module evaluates deadline constraints and estimates deadline risk:
- Configurable maximum acceptable Inter-Token Latency (ITL deadline target)
- Observed latency slack calculation (Deadline - Observed ITL)
- Predicted latency slack using a simple, documented Moving-Average / EWMA predictor
- Explicit detection and flagging of observed deadline violations
- Classification of predicted deadline risk: LOW, MEDIUM, or HIGH

IMPORTANT ARCHITECTURAL BOUNDARY:
This module does NOT schedule tasks or decide execution order.
Scheduling policies (e.g., EDF, Least Slack First, preemption) are strictly
owned by Member 2. This module solely provides measurements, deadline metadata,
and risk signals for Member 2's scheduler to consume.
"""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional

from latency_profiler import RequestProfileResult
from workload_generator import InferenceRequest


class RiskLevel(str, Enum):
    """Categorical risk classification for deadline violation."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class DeadlineEvaluationResult:
    """Evaluation result for a single request comparing latency against deadlines.
    
    Maintains a strict separation between OBSERVED facts (violations, slacks)
    and PREDICTED estimates (predicted slack, risk level).
    """
    request_id: str
    deadline_target_ms: float

    # --- Observed Metrics (Ground Truth Measurements) ---
    total_tokens_evaluated: int
    observed_violation_count: int
    observed_violation_rate: float        # Percentage of generated tokens missing deadline
    has_observed_deadline_violation: bool  # True if at least 1 token missed deadline
    min_observed_slack_ms: float          # Worst-case token slack (deadline - max_itl)
    avg_observed_slack_ms: float          # Average slack (deadline - avg_itl)
    p95_observed_slack_ms: float          # 95th percentile slack (deadline - p95_itl)

    # --- Predicted Metrics (Statistical Risk Estimation) ---
    prediction_method: str
    predicted_itl_ms: float
    predicted_slack_ms: float             # deadline - predicted_itl
    predicted_risk_level: RiskLevel
    risk_reasoning: str

    def to_dict(self) -> dict:
        """Serializes the deadline evaluation to a dictionary."""
        data = asdict(self)
        data["predicted_risk_level"] = self.predicted_risk_level.value
        return data


class DeadlineRiskModel:
    """Evaluates deadline compliance and computes predictive risk metrics."""

    def __init__(
        self,
        high_risk_slack_ratio_threshold: float = 0.10,
        medium_risk_slack_ratio_threshold: float = 0.25,
        ewma_alpha: float = 0.35,
    ):
        """Initializes the risk model with configurable threshold parameters.

        Args:
            high_risk_slack_ratio_threshold: If (predicted_slack / deadline) < threshold,
                                             or if predicted_slack <= 0, classified as HIGH.
            medium_risk_slack_ratio_threshold: If threshold <= ratio < medium threshold,
                                               classified as MEDIUM. Otherwise LOW.
            ewma_alpha: Smoothing factor for Exponential Weighted Moving Average (0 < alpha <= 1).
        """
        self.high_risk_threshold = high_risk_slack_ratio_threshold
        self.medium_risk_threshold = medium_risk_slack_ratio_threshold
        self.ewma_alpha = ewma_alpha

    def predict_next_itl(
        self,
        history_itl_samples: List[float],
        fallback_target_ms: float = 30.0,
        window_size: int = 10,
    ) -> float:
        """Predicts the latency of the upcoming token using strictly historical observations.
        
        Zero future-data leakage guarantee:
        Only observations strictly preceding the target token (y_1, ..., y_t) are utilized.
        Computes sequential Exponential Weighted Moving Average (EWMA):
            y_hat_{k} = alpha * y_{k} + (1 - alpha) * y_hat_{k-1}
        """
        if not history_itl_samples:
            return fallback_target_ms

        window = history_itl_samples[-min(window_size, len(history_itl_samples)):]
        predicted = window[0]
        for sample in window[1:]:
            predicted = (self.ewma_alpha * sample) + ((1.0 - self.ewma_alpha) * predicted)
        return round(predicted, 2)

    def evaluate_request(
        self,
        request: InferenceRequest,
        profile: RequestProfileResult,
        custom_deadline_ms: Optional[float] = None,
    ) -> DeadlineEvaluationResult:
        """Evaluates deadline violations and predicts risk for an inference profile.

        Args:
            request: The original inference request metadata.
            profile: Latency profile containing per-token samples.
            custom_deadline_ms: Optional deadline override; defaults to request.target_itl_ms.
        """
        deadline = custom_deadline_ms if custom_deadline_ms is not None else request.target_itl_ms
        if deadline <= 0:
            raise ValueError(f"Deadline must be positive, got {deadline}")

        itl_samples = profile.itl_samples_ms
        total_tokens = len(itl_samples)

        # -------------------------------------------------------------------
        # 1. OBSERVED GROUND TRUTH EVALUATION
        # -------------------------------------------------------------------
        if total_tokens > 0:
            violations = [itl for itl in itl_samples if itl > deadline]
            violation_count = len(violations)
            violation_rate = round(violation_count / total_tokens, 4)
            has_violation = violation_count > 0

            # Observed slacks: Slack = Deadline - Observed_ITL
            # A negative slack indicates a deadline violation.
            slacks = [deadline - itl for itl in itl_samples]
            min_slack = min(slacks)
            avg_slack = deadline - profile.avg_itl_ms
            p95_slack = deadline - profile.p95_itl_ms
        else:
            violation_count = 0
            violation_rate = 0.0
            has_violation = False
            min_slack = deadline
            avg_slack = deadline
            p95_slack = deadline

        # -------------------------------------------------------------------
        # 2. PREDICTED METRIC EVALUATION (EWMA Predictor)
        # -------------------------------------------------------------------
        # Prediction Methodology:
        # We calculate an Exponential Weighted Moving Average (EWMA) over recent
        # token ITL samples. If fewer than 3 tokens exist, we fall back to a weighted
        # combination of the request's initial target and profile average.
        if total_tokens >= 3:
            recent_tokens = itl_samples[-min(10, total_tokens):]
            predicted_itl = self.predict_next_itl(recent_tokens, fallback_target_ms=request.target_itl_ms)
            method = f"EWMA(alpha={self.ewma_alpha}, window={len(recent_tokens)})"
        elif total_tokens > 0:
            predicted_itl = profile.avg_itl_ms
            method = "ProfileMean(sparse_tokens)"
        else:
            predicted_itl = request.target_itl_ms
            method = "StaticTargetPrior"

        predicted_slack = deadline - predicted_itl
        slack_ratio = predicted_slack / deadline if deadline > 0 else 0.0

        # Classify risk level based on normalized slack ratio
        if predicted_slack <= 0 or slack_ratio < self.high_risk_threshold:
            risk_level = RiskLevel.HIGH
            reasoning = (f"Critical slack deficit: predicted slack is {predicted_slack:.2f}ms "
                         f"({slack_ratio*100:.1f}% of deadline target {deadline:.1f}ms).")
        elif slack_ratio < self.medium_risk_threshold:
            risk_level = RiskLevel.MEDIUM
            reasoning = (f"Moderate slack buffer: predicted slack is {predicted_slack:.2f}ms "
                         f"({slack_ratio*100:.1f}% of deadline target {deadline:.1f}ms).")
        else:
            risk_level = RiskLevel.LOW
            reasoning = (f"Healthy slack buffer: predicted slack is {predicted_slack:.2f}ms "
                         f"({slack_ratio*100:.1f}% of deadline target {deadline:.1f}ms).")

        return DeadlineEvaluationResult(
            request_id=request.request_id,
            deadline_target_ms=round(deadline, 2),
            total_tokens_evaluated=total_tokens,
            observed_violation_count=violation_count,
            observed_violation_rate=violation_rate,
            has_observed_deadline_violation=has_violation,
            min_observed_slack_ms=round(min_slack, 2),
            avg_observed_slack_ms=round(avg_slack, 2),
            p95_observed_slack_ms=round(p95_slack, 2),
            prediction_method=method,
            predicted_itl_ms=round(predicted_itl, 2),
            predicted_slack_ms=round(predicted_slack, 2),
            predicted_risk_level=risk_level,
            risk_reasoning=reasoning,
        )


if __name__ == "__main__":
    from workload_generator import WorkloadGenerator
    from latency_profiler import LatencyProfiler, SimulatedInferenceBackend

    print("--- Deadline Model & Risk Predictor Demonstration ---")
    gen = WorkloadGenerator(seed=42)
    sample_requests = gen.generate_workload(total_requests=4, arrival_rate_rps=1.0)

    profiler = LatencyProfiler(backend=SimulatedInferenceBackend(seed=42))
    model = DeadlineRiskModel()

    for req in sample_requests:
        profile = profiler.profile_request(req, interference_factor=1.2)
        evaluation = model.evaluate_request(req, profile)

        print(f"[{req.request_id}] Target: {evaluation.deadline_target_ms:4.1f}ms | "
              f"Violations: {evaluation.observed_violation_count}/{evaluation.total_tokens_evaluated} "
              f"({evaluation.observed_violation_rate*100:4.1f}%) | "
              f"Avg Slack: {evaluation.avg_observed_slack_ms:5.1f}ms | "
              f"Pred Slack: {evaluation.predicted_slack_ms:5.1f}ms | "
              f"Risk: {evaluation.predicted_risk_level.value.upper():6s} | "
              f"Reason: {evaluation.risk_reasoning}")
