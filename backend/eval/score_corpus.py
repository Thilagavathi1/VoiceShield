"""Score the classifier against the labelled corpus.

    python -m eval.score_corpus

Prints a confusion matrix and per-case detail. Put the matrix on a slide -- no other
team will have measured anything, and the false-positive number is the one that proves
you thought about the elderly daughter-hangs-up harm and not just the demo.

Only needs ANTHROPIC_API_KEY. Works before Agora enablement comes through.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.guard import classify  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "data" / "scam_corpus.jsonl"


def to_messages(turns: list[list[str]]) -> list[dict]:
    """Shape a corpus case like Agora would deliver it, including SAL speaker ids."""
    return [
        {"role": "user", "content": text, "metadata": {"vpids": [speaker]}}
        for speaker, text in turns
    ]


async def main() -> int:
    if not settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY not set (see .env.example)")
        return 2

    cases = [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    threshold = settings().warn_threshold
    print(f"{len(cases)} cases, warn threshold {threshold}\n")

    sem = asyncio.Semaphore(6)

    async def run(case: dict) -> tuple[dict, object]:
        async with sem:
            return case, await classify(to_messages(case["turns"]))

    results = await asyncio.gather(*(run(c) for c in cases))

    tp = fp = tn = fn = 0
    failures: list[str] = []
    latencies: list[int] = []

    for case, v in results:
        latencies.append(v.latency_ms)
        flagged = v.risk >= threshold
        is_scam = case["label"] == "scam"
        if is_scam and flagged:
            tp += 1
            mark = "ok  "
        elif is_scam and not flagged:
            fn += 1
            mark = "MISS"
            failures.append(f"  MISS  {case['id']:<22} risk={v.risk:<3} ({case['pattern']})")
        elif not is_scam and flagged:
            fp += 1
            mark = "FALSE"
            failures.append(
                f"  FALSE {case['id']:<22} risk={v.risk:<3} said={v.pattern} "
                f"signals={v.signals}"
            )
        else:
            tn += 1
            mark = "ok  "
        print(f"{mark} {case['id']:<24} risk={v.risk:<3} {v.latency_ms:>5}ms  {v.pattern}")

    scams = tp + fn
    legit = tn + fp
    recall = tp / scams if scams else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    fpr = fp / legit if legit else 0.0
    latencies.sort()

    print("\n" + "=" * 58)
    print(f"  scams caught     {tp}/{scams}   (recall    {recall:.0%})")
    print(f"  false alarms     {fp}/{legit}   (FP rate   {fpr:.0%})")
    print(f"  precision        {precision:.0%}")
    if latencies:
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"  classify latency p50 {p50}ms  p95 {p95}ms")
    print("=" * 58)

    if failures:
        print("\nCases to fix (tune prompts.py, not the threshold, first):")
        print("\n".join(failures))
    else:
        print("\nClean sweep. Add harder lookalike cases -- this corpus is too easy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
