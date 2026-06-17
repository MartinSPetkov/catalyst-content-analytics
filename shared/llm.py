import os
import json
import subprocess
import time

if os.environ.get("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY is set. This repo runs on subscription auth via claude -p. "
        "Unset it before running."
    )

MODEL = "claude-sonnet-4-6"

_RETRY_DELAYS = [2, 4, 8]


def _shell(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"claude exited {result.returncode}")
    return result.stdout.strip()


def call(prompt: str) -> str:
    print("[LLM] Calling claude...")
    t0 = time.time()
    last_err = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            if "rate" in str(last_err).lower():
                print(f"[LLM] Rate limit hit. Waiting 60s before retry...")
                time.sleep(60)
            else:
                time.sleep(delay)
        try:
            text = _shell(["claude", "-p", prompt, "--model", MODEL])
            print(f"[LLM] Done ({time.time() - t0:.1f}s)")
            return text
        except RuntimeError as e:
            last_err = e
            if attempt < len(_RETRY_DELAYS):
                print(f"[LLM] Attempt {attempt + 1} failed: {e}. Retrying...")
    raise RuntimeError(f"claude failed after {len(_RETRY_DELAYS) + 1} attempts: {last_err}")


def call_json(prompt: str) -> dict | list:
    full_prompt = prompt + "\n\nReturn only valid JSON. No markdown, no code fences, no commentary."
    print("[LLM] Calling claude...")
    t0 = time.time()
    last_err = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            if "rate" in str(last_err).lower():
                print(f"[LLM] Rate limit hit. Waiting 60s before retry...")
                time.sleep(60)
            else:
                time.sleep(delay)
        try:
            raw = _shell(["claude", "-p", full_prompt, "--model", MODEL])
            cleaned = _strip_fences(raw)
            result = json.loads(cleaned)
            print(f"[LLM] Done ({time.time() - t0:.1f}s)")
            return result
        except (RuntimeError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < len(_RETRY_DELAYS):
                print(f"[LLM] Attempt {attempt + 1} failed: {e}. Retrying...")
    raise RuntimeError(f"claude failed after {len(_RETRY_DELAYS) + 1} attempts: {last_err}")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()
