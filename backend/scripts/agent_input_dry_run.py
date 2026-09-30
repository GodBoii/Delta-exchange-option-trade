"""Compare production agent paths on frozen evidence. All action tools are simulated."""

import argparse
import asyncio
import json
import os
import time
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from agno.agent import Agent
from agno.db.in_memory import InMemoryDb
from agno.run.agent import RunOutput

from app.default_strategies import builtin_strategy_definitions
from app.exit_schedule import ExitChoice, resolve_exit_schedule, template_from_definition
from automation_agent import team
from automation_agent.assets import PROFILES
from automation_agent.market import MarketIntelligenceTools
from automation_agent.preview import build_preview, resolve_contracts
from automation_agent.storage import StoredChart
from automation_agent.tools import AutomationStrategyTools
from news_agent.config import NewsAgentSettings


def catalogue(asset: str) -> list[dict]:
    return [
        {
            "id": str(uuid4()),
            "version": 1,
            "name": s.name,
            "enabled_for_ai": True,
            "user_id": None,
            "definition_json": template_from_definition(s.model_dump(mode="json", exclude_none=True)),
        }
        for s in builtin_strategy_definitions(datetime.now(UTC))
        if s.instrument.underlying == asset
    ]


async def capture_asset(asset: str, url: str) -> dict:
    profile = PROFILES[asset]
    started = time.perf_counter()
    market = MarketIntelligenceTools(binance_url=url, asset=profile, curated=True)
    packet, context = await asyncio.gather(
        asyncio.to_thread(market.collect_market_packet), asyncio.to_thread(market.collect_delta_option_context)
    )
    options, rows, symbols = context["options"], catalogue(asset), set()
    for saved in rows:
        with suppress(ValueError):
            definition, _ = resolve_exit_schedule(
                saved["definition_json"],
                entry_at=datetime.now(UTC) + timedelta(minutes=10),
                choice=ExitChoice(kind="specific_time", exit_at=datetime.now(UTC) + timedelta(hours=1)),
                options=options,
            )
            symbols.update(leg["productSymbol"] for leg in resolve_contracts(definition, options))
    async with httpx.AsyncClient(timeout=20) as client:

        async def selected(symbol):
            response = await client.post(
                f"{url}/api/market/{profile.market_route}/selected-contracts", json={"symbols": [symbol]}
            )
            response.raise_for_status()
            result = response.json()
            if result.get("asset") != asset:
                raise ValueError("Selected evidence asset mismatch")
            return result["contracts"]

        responses = await asyncio.gather(*(selected(symbol) for symbol in sorted(symbols)), return_exceptions=True)
    refreshed = {o["symbol"]: o for response in responses if isinstance(response, list) for o in response}
    return {
        "asset": asset,
        "market": packet,
        "options": [refreshed.get(o["symbol"], o) for o in options],
        "catalogue": rows,
        "collectionSeconds": time.perf_counter() - started,
        "selectedContractSnapshots": len(refreshed),
    }


def run_case(
    case: dict,
    news: str,
    *,
    curated: bool,
    recheck: bool,
    offline: bool,
    model: str | None = None,
    reasoning: str | None = None,
) -> dict:
    asset, profile = case["asset"], PROFILES[case["asset"]]
    snapshot_id, run_id = str(uuid4()), str(uuid4())
    frozen = deepcopy(case["market"])
    options = deepcopy(case["options"])
    frozen_now = max(int(o["observedAt"]) for o in options)
    simulation_now = datetime.fromtimestamp(frozen_now / 1000, UTC)
    rows = case["catalogue"]
    record = {
        "asset": asset,
        "path": "curated" if curated else "legacy",
        "stage": "recheck" if recheck else "main",
        "collectionSeconds": case.get("collectionSeconds"),
        "simulatedActions": [],
        "tools": [],
    }
    state = {
        "run": {"id": run_id},
        "occupied": 0,
        "parent": None,
        "snapshot": {
            "id": snapshot_id,
            "market_json": {"executionOptionContext": {"underlying": asset, "options": options}},
        },
    }

    class MemoryResearch:
        def request_sync(self, path, args, **kwargs):
            if path == "runtimeAutomation:context":
                return deepcopy(state)
            # No client exists behind this fake. Every mutation stays in this process.
            record["simulatedActions"].append({"name": path, "arguments": args})
            outcome = (
                "strategy_selected"
                if path in {"sharedAnalysis:publish", "runtimeAutomation:schedule"}
                else ("wait_and_run_again" if "schedule" in path.lower() else "strategy_dropped")
            )
            state["run"]["outcome"] = outcome
            return {"status": "committed", "outcome": outcome, "success": True}

    class DryStrategyTools(AutomationStrategyTools):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.application_data = SimpleNamespace(
                selection_context=lambda _, saved=None: (
                    [r for r in rows if saved is None or r["id"] == saved],
                    {"allocation_mode": "half_balance"},
                )
            )

        def preview_strategy(self, strategy_ref: str, activation_time: str, exit_choice: ExitChoice) -> str:
            """Preview one saved strategy with frozen evidence and the production expiry/payoff calculations.

            exit_choice uses kind: intraday|overnight|positional with hours, specific_time with exit_at,
            or expiry with expiry_number 1 or 2. Choose using starting comparisons before previewing.
            """
            try:
                saved_id, version = self.strategy_references[strategy_ref.strip().upper()]
                saved = next(r for r in rows if r["id"] == saved_id)
                activation = datetime.fromisoformat(activation_time.replace("Z", "+00:00")).astimezone(UTC)
                choice = ExitChoice.model_validate(exit_choice)
                definition, schedule = resolve_exit_schedule(
                    saved["definition_json"], entry_at=activation, choice=choice, options=options
                )
                resolved = resolve_contracts(definition, options)
                selected = [o for o in options if o["symbol"] in {leg["productSymbol"] for leg in resolved}]
                self._previews[(saved_id, version, activation.isoformat(), choice.model_dump_json())] = (
                    definition,
                    schedule,
                )
                return json.dumps(
                    {"valid": True, "schedule": schedule, **build_preview(definition, resolved, selected, frozen_now)}
                )
            except (ValueError, KeyError, StopIteration) as error:
                return json.dumps({"valid": False, "reason": str(error)})

    class RecordingAgent(Agent):
        def __init__(self, **kwargs):
            if reasoning and not recheck:
                kwargs["model"].reasoning_effort = reasoning
            kwargs["add_datetime_to_context"] = False
            kwargs["additional_context"] += f" Frozen simulation clock: {simulation_now.isoformat()}."
            record["startingTextBytes"] = len(
                json.dumps(
                    {k: kwargs.get(k) for k in ("instructions", "additional_context", "description", "role")},
                    default=str,
                ).encode()
            )
            record["registeredTools"] = [name for toolkit in kwargs.get("tools", []) for name in toolkit.functions]
            record["reasoningEffort"] = kwargs["model"].reasoning_effort
            super().__init__(**kwargs)

        def capture(self, result):
            record["usage"] = result.metrics.to_dict() if result.metrics else {}
            if not record["usage"].get("input_tokens"):
                record["status"] = "failed"
                record["errorType"] = "ProviderReturnedNoUsage"
            usages = [m.metrics.to_dict() for m in result.messages or [] if m.role == "assistant" and m.metrics]
            record["initialInputTokens"] = next((m.get("input_tokens") for m in usages if m.get("input_tokens")), None)
            record["tools"] = [
                {
                    "name": t.tool_name,
                    "arguments": t.tool_args,
                    "resultBytes": len(str(t.result).encode()),
                    "seconds": t.metrics.duration if t.metrics else None,
                }
                for t in result.tools or []
            ]
            return result

        def record_inputs(self, images):
            record["images"] = len(images)
            record["startingTextBytes"] = len(
                json.dumps(
                    {
                        "instructions": self.instructions,
                        "additional_context": self.additional_context,
                        "description": self.description,
                        "role": self.role,
                    },
                    default=str,
                ).encode()
            )

        async def arun(self, *args, **kwargs):
            self.record_inputs(kwargs.get("images", []))
            if offline:
                return RunOutput(content="Dry-run input construction only; no model invoked.")
            return self.capture(await super().arun(*args, **kwargs))

        def run(self, *args, **kwargs):
            self.record_inputs(kwargs.get("images", []))
            if offline:
                return RunOutput(content="Dry-run input construction only; no model invoked.")
            return self.capture(super().run(*args, **kwargs))

    class MemoryCharts:
        def save_run_charts(self, *, agent_run_id, charts, **kwargs):
            return [StoredChart(c.id, c.label, c.alt_text, agent_run_id, c.content) for c in charts]

    market = MarketIntelligenceTools(asset=profile, curated=curated)
    market.collect_market_packet = lambda: deepcopy(frozen)
    market.collect_delta_option_context = lambda: {"underlying": asset, "options": deepcopy(options)}
    market._packet_cache = frozen
    settings = NewsAgentSettings.load()
    if model:
        settings = replace(settings, automation_model_id=model)
    record["model"] = settings.automation_model_id
    if not offline:
        settings.require_api_key()
    selected_row = next(r for r in rows if "Long call" in r["name"])
    selected_definition, _ = resolve_exit_schedule(
        selected_row["definition_json"],
        entry_at=simulation_now + timedelta(minutes=10),
        choice=ExitChoice(kind="specific_time", exit_at=simulation_now + timedelta(hours=1)),
        options=options,
    )

    def public_response(request):
        if request.url.path.endswith("selected-contracts"):
            symbols = json.loads(request.content)["symbols"]
            return httpx.Response(
                200, json={"asset": asset, "contracts": [o for o in options if o["symbol"] in symbols]}
            )
        raise AssertionError("Dry run attempted an unexpected HTTP request")

    original_client = httpx.Client

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return simulation_now.astimezone(tz) if tz else simulation_now.replace(tzinfo=None)

    started = time.perf_counter()
    with (
        patch.dict(os.environ, {"CURATED_AGENT_INPUT_ENABLED": str(curated).lower()}),
        patch("automation_agent.tools.runtime_data", return_value=MemoryResearch()),
        patch("automation_agent.tools.LocalResearchClient", return_value=MemoryResearch()),
        patch("automation_agent.tools.datetime", FrozenDatetime),
        patch("app.automation_schedule.datetime", FrozenDatetime),
        patch.object(team, "read_parent_run_context", return_value=None),
        patch.object(team, "MarketIntelligenceTools", return_value=market),
        patch.object(team, "AutomationStrategyTools", DryStrategyTools),
        patch.object(team, "ChartStorage", return_value=MemoryCharts()),
        patch.object(team, "save_market_snapshot", return_value=snapshot_id),
        patch.object(team, "create_session_db", side_effect=lambda *a, **kw: InMemoryDb()),
        patch.object(
            team,
            "run_news_pipeline",
            return_value=SimpleNamespace(
                markdown=news, research_tools=[], research_trace=[], report_response=RunOutput(content=news)
            ),
        ),
        patch.object(team, "Agent", RecordingAgent),
        patch.object(
            team,
            "httpx",
            SimpleNamespace(
                Timeout=httpx.Timeout,
                Client=lambda **kwargs: original_client(transport=httpx.MockTransport(public_response), **kwargs),
            ),
        ),
        patch.object(
            team,
            "build_preview",
            side_effect=lambda definition, resolved, selected, now: build_preview(
                definition, resolved, selected, frozen_now
            ),
        ),
    ):
        if recheck:
            result = team.run_activation_recheck(
                settings=settings,
                user_id="global",
                agent_run_id=run_id,
                session_id="dry-recheck",
                asset=profile,
                recheck_context={
                    "proposalId": str(uuid4()),
                    "selectedStrategy": {
                        "name": selected_row["name"],
                        "definition": selected_definition,
                        "activationTime": selected_definition["entry"]["entryAt"],
                    },
                    "originalSelection": {"finalResponse": "Frozen baseline proposal for checking input delivery."},
                },
            )
        else:
            result = team.run_automation_team(
                settings=settings,
                user_id="global",
                agent_run_id=run_id,
                session_id="dry-main",
                asset=profile,
                account_context={},
                trigger="manual",
            )
    record["durationSeconds"] = time.perf_counter() - started
    record["report"] = result.report
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("output", type=Path)
    capture.add_argument("--btc-url", required=True)
    capture.add_argument("--eth-url", required=True)
    capture.add_argument(
        "--news", type=Path, required=True, help="Existing frozen news summary; never run news research"
    )
    run = sub.add_parser("run")
    run.add_argument("cases", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--offline", action="store_true")
    run.add_argument("--asset", choices=["BTC", "ETH"])
    run.add_argument("--stage", choices=["main", "recheck"])
    run.add_argument("--path", choices=["legacy", "curated"])
    run.add_argument("--model", help="Dry-run model override; never changes live or news-agent settings")
    run.add_argument(
        "--reasoning", choices=["low", "medium", "high", "max"], help="Main-agent dry-run override; recheck stays low"
    )
    args = parser.parse_args()
    if args.command == "capture":
        cases = [
            asyncio.run(capture_asset(asset, url)) for asset, url in (("BTC", args.btc_url), ("ETH", args.eth_url))
        ]
        args.output.write_text(
            json.dumps({"news": args.news.read_text(encoding="utf-8"), "cases": cases}), encoding="utf-8"
        )
        print(
            json.dumps(
                {"assets": [c["asset"] for c in cases], "captureSeconds": sum(c["collectionSeconds"] for c in cases)}
            )
        )
    else:
        bundle = json.loads(args.cases.read_text(encoding="utf-8"))
        args.output.mkdir(parents=True, exist_ok=True)
        for case in bundle["cases"]:
            if args.asset and args.asset != case["asset"]:
                continue
            for recheck in (False, True):
                if args.stage and args.stage != ("recheck" if recheck else "main"):
                    continue
                for curated in (False, True):
                    if args.path and args.path != ("curated" if curated else "legacy"):
                        continue
                    name = f"{case['asset']}-{'recheck' if recheck else 'main'}-{'curated' if curated else 'legacy'}"
                    started = time.perf_counter()
                    try:
                        result = run_case(
                            case,
                            bundle["news"],
                            curated=curated,
                            recheck=recheck,
                            offline=args.offline,
                            model=args.model,
                            reasoning=args.reasoning,
                        )
                    except Exception as error:
                        result = {
                            "status": "failed",
                            "errorType": type(error).__name__,
                            "durationSeconds": time.perf_counter() - started,
                        }
                    (args.output / f"{name}.json").write_text(
                        json.dumps(result, indent=2, default=str), encoding="utf-8"
                    )
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "status": result.get("status", "completed"),
                                "startingTextBytes": result.get("startingTextBytes"),
                                "usage": result.get("usage"),
                                "tools": [t["name"] for t in result.get("tools", [])],
                            }
                        ),
                        flush=True,
                    )


if __name__ == "__main__":
    main()
