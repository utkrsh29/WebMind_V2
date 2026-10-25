from typing import Any, Dict, List
import numpy as np


class BenchmarkResult:
    def __init__(
        self,
        config_name: str,
        total_tasks: int,
        success_rate: float,
        mean_latency: float,
        p95_latency: float,
        total_tokens: int,
        mean_tokens_per_task: float,
        escalation_rate: float,
        recovery_rate: float,
        action_efficiency: float,
    ):
        self.config_name = config_name
        self.total_tasks = total_tasks
        self.success_rate = success_rate
        self.mean_latency = mean_latency
        self.p95_latency = p95_latency
        self.total_tokens = total_tokens
        self.mean_tokens_per_task = mean_tokens_per_task
        self.escalation_rate = escalation_rate
        self.recovery_rate = recovery_rate
        self.action_efficiency = action_efficiency

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Configuration": self.config_name,
            "Total Tasks": self.total_tasks,
            "Success Rate (%)": f"{self.success_rate:.1f}%",
            "Mean Latency (s)": f"{self.mean_latency:.2f}s",
            "P95 Latency (s)": f"{self.p95_latency:.2f}s",
            "Total Tokens": self.total_tokens,
            "Avg Tokens / Task": f"{self.mean_tokens_per_task:.0f}",
            "Escalation Rate (%)": f"{self.escalation_rate:.1f}%",
            "Recovery Rate (%)": f"{self.recovery_rate:.1f}%",
            "Action Efficiency (Steps/Success)": f"{self.action_efficiency:.2f}",
        }


def compute_benchmark_metrics(config_name: str, task_records: List[Dict[str, Any]]) -> BenchmarkResult:
    """Aggregates raw task execution outputs into standardized benchmark evaluation metrics."""
    total_tasks = len(task_records)
    if total_tasks == 0:
        return BenchmarkResult(config_name, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0)

    successful = [r for r in task_records if r.get("status") == "completed"]
    success_rate = (len(successful) / total_tasks) * 100.0

    latencies = [r.get("latency_seconds", 0.0) for r in task_records]
    mean_latency = float(np.mean(latencies)) if latencies else 0.0
    p95_latency = float(np.percentile(latencies, 95)) if latencies else 0.0

    tokens_list = [r.get("total_tokens", 0) for r in task_records]
    total_tokens = sum(tokens_list)
    mean_tokens = total_tokens / total_tasks

    escalated = [r for r in task_records if r.get("escalation_count", 0) > 0]
    escalation_rate = (len(escalated) / total_tasks) * 100.0

    recovered = [r for r in escalated if r.get("status") == "completed"]
    recovery_rate = (len(recovered) / len(escalated) * 100.0) if escalated else 100.0

    successful_steps = sum(r.get("steps", 0) for r in successful)
    action_efficiency = (successful_steps / len(successful)) if successful else 0.0

    return BenchmarkResult(
        config_name=config_name,
        total_tasks=total_tasks,
        success_rate=success_rate,
        mean_latency=mean_latency,
        p95_latency=p95_latency,
        total_tokens=total_tokens,
        mean_tokens_per_task=mean_tokens,
        escalation_rate=escalation_rate,
        recovery_rate=recovery_rate,
        action_efficiency=action_efficiency,
    )
