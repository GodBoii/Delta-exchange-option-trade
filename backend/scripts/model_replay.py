"""Replay saved decisions against frozen tool results. No trading modules are executed."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from agno.tools.function import Function

MODEL = "openai/gpt-6.1-sol"
TOOL_NAMES = {
    "get_btc_market_packet",
    "get_eth_market_packet",
    "show_available_strategy",
    "calculate_exit_time",
    "select_strategy_and_time",
    "scheduled_next_agent_run",
}


def historical_schemas(repo: Path, timestamp: int, asset: str) -> tuple[str, list[dict]]:
    """Recover callable schemas from Git without importing or executing trading code."""
    stamp = datetime.fromtimestamp(timestamp, UTC).isoformat()
    revision = subprocess.check_output(
        ["git", "rev-list", "-1", f"--before={stamp}", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()
    if not revision:
        raise ValueError(f"No historical source at {stamp}")
    schemas = []
    for filename in ("tools.py", "market.py"):
        source = subprocess.check_output(
            ["git", "show", f"{revision}:backend/automation_agent/{filename}"],
            cwd=repo,
            text=True,
            encoding="utf-8",
        )
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef) or node.name not in TOOL_NAMES:
                continue
            if node.name == f"get_{'eth' if asset == 'BTC' else 'btc'}_market_packet":
                continue
            node.args.args = [arg for arg in node.args.args if arg.arg != "self"]
            node.decorator_list = []
            node.body = [ast.Expr(ast.Constant(ast.get_docstring(node) or "")), ast.Pass()]
            stub = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
            namespace = {"Any": Any, "Literal": Literal}
            exec(compile(stub, filename, "exec", dont_inherit=True), namespace)
            schema = Function.from_callable(namespace[node.name]).to_dict()
            schemas.append({"type": "function", "function": schema})
    return revision, schemas


def normalized_args(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object")
    return value


def recorded_result(calls: list[dict], name: str, arguments: dict) -> tuple[str, bool]:
    for call in calls:
        if (call.get("tool_name") or call.get("name")) == name and normalized_args(
            call.get("tool_args", call.get("args"))
        ) == arguments:
            result = call.get("result")
            if result is not None:
                return result if isinstance(result, str) else json.dumps(result), True
    return json.dumps(
        {
            "success": False,
            "status": "unavailable_in_historical_record",
            "message": "No recorded result exists for this exact tool and arguments. No live action was performed.",
        }
    ), False


def prepare_cases(evidence: dict, repo: Path) -> list[dict]:
    reports = {r["snapshot_id"]: r for r in evidence["reports"] if r.get("snapshot_id")}
    charts: dict[str, dict[str, str]] = {}
    for chart in evidence["charts"]:
        charts.setdefault(chart["run_id"], {})[chart["chart_id"]] = chart["base64"]
    cases = []
    for session in evidence["sessions"]:
        for run in session.get("runs") or []:
            report = reports.get((run.get("metadata") or {}).get("marketSnapshotId"))
            if not report:
                continue
            messages = []
            for message in run.get("messages") or []:
                if message["role"] == "assistant":
                    break
                if message["role"] in ("system", "user"):
                    messages.append({"role": message["role"], "content": message.get("content") or ""})
            if not messages or messages[0]["role"] != "system":
                raise ValueError(f"Missing original prompt for {run['run_id']}")
            expected_charts = (report.get("market_json") or {}).get("chartImages") or []
            image_map = charts.get(report["id"], {})
            if any(chart["id"] not in image_map for chart in expected_charts):
                raise ValueError(f"Missing original charts for {run['run_id']}")
            if expected_charts:
                user_message = next(m for m in messages if m["role"] == "user")
                user_message["content"] = [{"type": "text", "text": user_message["content"]}] + [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + image_map[chart["id"]],
                            "detail": "high",
                        },
                    }
                    for chart in expected_charts
                ]
            asset = "ETH" if "eth" in run.get("agent_id", "").lower() else "BTC"
            revision, schemas = historical_schemas(repo, run["created_at"], asset)
            # Deployments can precede the corresponding Git commit. Detect schema
            # mismatches against observed calls rather than silently use an older API.
            _, current_schemas = historical_schemas(repo, int(time.time()) + 86400, asset)
            corrections = []
            for call in run.get("tools") or []:
                name = call["tool_name"]
                observed = normalized_args(call.get("tool_args"))
                existing = next((s for s in schemas if s["function"]["name"] == name), None)
                if existing and set(observed) <= set(existing["function"]["parameters"]["properties"]):
                    continue
                replacement = next((s for s in current_schemas if s["function"]["name"] == name), None)
                if not replacement or not set(observed) <= set(replacement["function"]["parameters"]["properties"]):
                    raise ValueError(f"Cannot reconstruct schema for {name} in {run['run_id']}")
                schemas = [s for s in schemas if s["function"]["name"] != name] + [replacement]
                corrections.append(name)
            payload = {"messages": messages, "tools": schemas}
            cases.append(
                {
                    "id": report["id"],
                    "asset": asset,
                    "created_at": run["created_at"],
                    "captured_at": report["market_json"].get("capturedAt"),
                    "source_revision": revision,
                    "schema_provenance": "reconstructed_from_historical_git",
                    "schema_corrections_from_current_source": sorted(set(corrections)),
                    "input_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                    **payload,
                    "original": {
                        "model": run["model"],
                        "report": run.get("content"),
                        "tools": run.get("tools") or [],
                        "metrics": run.get("metrics") or {},
                    },
                }
            )
    return sorted(cases, key=lambda case: (case["created_at"], case["id"]))


def replay_case(case: dict, api_key: str, max_rounds: int = 12) -> dict:
    messages = list(case["messages"])
    trace, usages = [], []
    started = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(240, connect=15)) as client:
        for _ in range(max_rounds):
            request_started = time.perf_counter()
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + api_key},
                json={
                    "model": MODEL,
                    "reasoning": {"effort": "medium", "exclude": True},
                    "messages": messages,
                    "tools": case["tools"],
                    "provider": {"require_parameters": True},
                    "max_tokens": 12000,
                },
            )
            if response.is_error:
                try:
                    error = response.json().get("error", {})
                    error_message = error.get("message", "Provider request failed")
                except (ValueError, AttributeError):
                    error_message = "Provider returned a non-JSON error"
                return {
                    "status": "failed",
                    "http_status": response.status_code,
                    "error": error_message,
                    "tools": trace,
                    "usage": usages,
                    "duration_seconds": time.perf_counter() - started,
                }
            data = response.json()
            if data.get("error"):
                raise RuntimeError(f"Provider error: {data['error'].get('message', 'unknown')}")
            if data.get("model") != MODEL:
                raise RuntimeError(f"Unexpected model {data.get('model')}")
            usages.append({**(data.get("usage") or {}), "request_seconds": time.perf_counter() - request_started})
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                return {"status": "truncated", "tools": trace, "usage": usages}
            message = choice["message"]
            messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
            if not message.get("tool_calls"):
                return {
                    "status": "completed",
                    "model": MODEL,
                    "reasoning_effort": "medium",
                    "report": message.get("content"),
                    "tools": trace,
                    "usage": usages,
                    "duration_seconds": time.perf_counter() - started,
                }
            for call in message["tool_calls"]:
                function = call["function"]
                arguments = normalized_args(function["arguments"])
                result, matched = recorded_result(case["original"]["tools"], function["name"], arguments)
                trace.append(
                    {
                        "name": function["name"],
                        "args": arguments,
                        "result": result,
                        "exact_match": matched,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return {"status": "tool_limit", "tools": trace, "usage": usages, "duration_seconds": time.perf_counter() - started}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("evidence", type=Path)
    prepare.add_argument("output", type=Path)
    prepare.add_argument("--repo", type=Path)
    run = sub.add_parser("run")
    run.add_argument("cases", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--workers", type=int, default=3)
    run.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        cases = prepare_cases(evidence, args.repo or Path(__file__).resolve().parents[2])
        args.output.write_text(json.dumps({"window": evidence["window"], "cases": cases}), encoding="utf-8")
        print(json.dumps({"prepared": len(cases), "output": str(args.output)}))
        return
    if not 1 <= args.workers <= 4:
        parser.error("workers must be between 1 and 4")
    api_key = os.environ["OPENROUTER_API_KEY"]
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    args.output.mkdir(parents=True, exist_ok=True)
    pending = []
    for case in cases:
        path = args.output / (case["id"] + ".json")
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous and previous.get("input_sha256") != case["input_sha256"]:
            raise ValueError(f"Saved result uses different inputs for {case['id']}; choose a new output directory")
        if previous is None or (args.retry_failed and previous.get("status") == "failed"):
            pending.append(case)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(replay_case, case, api_key): case for case in pending}
        for future in as_completed(futures):
            case = futures[future]
            try:
                result = future.result()
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError) as error:
                result = {"status": "failed", "error": str(error)[:300]}
            result.update({"case_id": case["id"], "input_sha256": case["input_sha256"]})
            (args.output / (case["id"] + ".json")).write_text(json.dumps(result), encoding="utf-8")
            print(json.dumps({"case": case["id"], "asset": case["asset"], "status": result["status"]}), flush=True)


if __name__ == "__main__":
    main()
