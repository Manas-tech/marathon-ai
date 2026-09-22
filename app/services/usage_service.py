"""Track token usage across all Gemini calls in a run and estimate cost.

Ported from the original usage_tracker.py with no behavioural changes,
plus a small `to_totals()` helper so the router can serialize it straight
into the UsageTotals API schema.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

from app.core.config import get_settings

settings = get_settings()


@dataclass
class CallUsage:
    label: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class UsageTracker:
    calls: List[CallUsage] = field(default_factory=list)

    def record(self, label: str, usage_metadata) -> None:
        if usage_metadata is None:
            self.calls.append(CallUsage(label, 0, 0, 0))
            return
        prompt = getattr(usage_metadata, "prompt_token_count", None) or 0
        # candidates + thinking tokens are both billed at output rates
        candidates = getattr(usage_metadata, "candidates_token_count", None) or 0
        thoughts = getattr(usage_metadata, "thoughts_token_count", None) or 0
        output = candidates + thoughts
        total = getattr(usage_metadata, "total_token_count", None) or (prompt + output)
        self.calls.append(CallUsage(label, prompt, output, total))

    def totals(self):
        input_tokens = sum(c.input_tokens for c in self.calls)
        output_tokens = sum(c.output_tokens for c in self.calls)
        total_tokens = sum(c.total_tokens for c in self.calls)
        return input_tokens, output_tokens, total_tokens

    def estimate_cost_usd(self, input_rate_per_mtok: float | None = None, output_rate_per_mtok: float | None = None) -> float:
        input_rate_per_mtok = settings.INPUT_COST_PER_MTOK if input_rate_per_mtok is None else input_rate_per_mtok
        output_rate_per_mtok = settings.OUTPUT_COST_PER_MTOK if output_rate_per_mtok is None else output_rate_per_mtok
        input_tokens, output_tokens, _ = self.totals()
        return (input_tokens / 1_000_000) * input_rate_per_mtok + (output_tokens / 1_000_000) * output_rate_per_mtok

    def to_totals(self) -> dict:
        input_tokens, output_tokens, total_tokens = self.totals()
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(self.estimate_cost_usd(), 6),
        }

    def to_console_text(self) -> str:
        input_tokens, output_tokens, total_tokens = self.totals()
        cost = self.estimate_cost_usd()

        lines = [f"{'-'*50}", "Token usage", f"{'-'*50}"]
        for c in self.calls:
            lines.append(f"  {c.label:<30} in={c.input_tokens:>7}  out={c.output_tokens:>6}  total={c.total_tokens:>7}")
        lines.append(f"  {'-'*30}")
        lines.append(f"  {'TOTAL':<30} in={input_tokens:>7}  out={output_tokens:>6}  total={total_tokens:>7}")
        lines.append(f"\n  Estimated cost: ${cost:.4f}  "
                      f"(@ ${settings.INPUT_COST_PER_MTOK}/1M in, ${settings.OUTPUT_COST_PER_MTOK}/1M out — "
                      f"set INPUT_COST_PER_MTOK / OUTPUT_COST_PER_MTOK in .env if your model's rates differ)")
        lines.append(f"{'-'*50}")
        return "\n".join(lines)

    def print_summary(self) -> None:
        print("\n" + self.to_console_text())
