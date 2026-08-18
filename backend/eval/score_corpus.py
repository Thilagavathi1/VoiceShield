"""Score the classifier against the labelled corpus.

    python -m eval.score_corpus

Prints a confusion matrix and per-case detail. Put the matrix on a slide -- no other
team will have measured anything, and the false-positive number is the one that proves
you thought about the elderly daughter-hangs-up harm and not just the demo.

Needs only GEMINI_API_KEY, and runs without it to measure the free rule
floor alone. Works before Agora enablement comes through.
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
    if not settings().gemini_api_key:
        print("!! GEMINI_API_KEY not set — measuring the FREE RULE LAYER only.")
        print("   These are floor numbers, not the full system. See .env.example.\n")

    cases = [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    threshold = settings().warn_threshold
    rpm = settings().eval_requests_per_minute
    print(f"{len(cases)} cases, warn threshold {threshold}, paced at {rpm} req/min")
    print(f"model: {settings().classifier_model}  (allow ~{len(cases) * 60 // rpm}s)\n")

    # Pace the run to the free tier's request-per-minute ceiling. Firing these
    # concurrently rate-limits the account, every case falls back to the rule floor,
    # and the resulting numbers measure the wrong thing entirely -- which is exactly
    # what happened the first time this was run.
    sem = asyncio.Semaphore(2)
    gap = 60.0 / max(1, settings().eval_requests_per_minute)

    async def run(index: int, case: dict) -> tuple[dict, object]:
        await asyncio.sleep(index * gap)
        async with sem:
            return case, await classify(to_messages(case["turns"]))

    results = await asyncio.gather(*(run(i, c) for i, c in enumerate(cases)))

    # Infrastructure failures are NOT data points. Counting an unreachable API as a
    # correctly-scored "safe" case is how an eval reports 0% false alarms while
    # detecting nothing -- so they are tallied apart and they void the run.
    errored = [(c, v) for c, v in results if not v.usable]
    if errored:
        print(f"\n{'=' * 58}")
        print(f"  RUN VOID — {len(errored)}/{len(cases)} cases could not be judged")
        print(f"{'=' * 58}")
        # Dedupe on the cause, not the raw string: every API error carries a unique
        # request_id, so the full message never repeats and all 28 would print.
        seen: set[str] = set()
        for _case, v in errored:
            cause = (v.error or "").split("'request_id'")[0].strip().rstrip(",")
            if cause not in seen:
                seen.add(cause)
                print(f"  {cause[:300]}")
        print("\nNo accuracy numbers are reported: a failed call is not a verdict.")
        print("Fix the cause above and re-run. Common causes:")
        print("  - missing or invalid GEMINI_API_KEY (aistudio.google.com/apikey)")
        print("  - free-tier rate limit hit — wait a minute and re-run")
        print("  - model id not available to this account")
        return 1

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
        src = "rules-only" if v.degraded else v.source
        print(
            f"{mark} {case['id']:<24} risk={v.risk:<3} {v.latency_ms:>5}ms  "
            f"{v.pattern:<20} [{src}]"
        )

    # A run where every verdict came from the keyword floor is a valid measurement,
    # but it is NOT the full system -- label it so the numbers are never quoted as if
    # the model had been involved.
    if results and all(v.degraded for _c, v in results):
        print("\n!! Every case was judged by the rule layer alone (LLM unavailable).")
        print("   Report these as rule-floor numbers, not VoiceShield's accuracy.")

    mix: dict[str, int] = {}
    for _c, v in results:
        mix[v.source] = mix.get(v.source, 0) + 1
    print("\nverdicts by layer:", ", ".join(f"{k}={n}" for k, n in sorted(mix.items())))
    if mix.get("rules", 0):
        print(f"!! {mix['rules']} case(s) fell back to the keyword floor — "
              "the model was unreachable for those, so treat them as degraded.")

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
