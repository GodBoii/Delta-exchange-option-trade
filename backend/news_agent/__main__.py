from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import NewsAgentSettings
from .models import NewsAnalysisReport
from .pipeline import run_news_pipeline


def _markdown_report(report: NewsAnalysisReport) -> str:
    lines = [
        "# News intelligence report",
        "",
        f"Analyzed: {report.analyzed_at.isoformat()}",
        "",
        report.executive_summary,
        "",
        "## Aggregate view",
        "",
        f"BTC direction: **{report.aggregate_btc_direction}**  ",
        f"Volatility risk: **{report.aggregate_volatility_risk}**",
    ]
    if report.events:
        lines.extend(["", "## Events", ""])
        for event in report.events:
            lines.extend(
                [
                    f"### {event.headline}",
                    "",
                    event.summary,
                    "",
                    f"Type/status: `{event.event_type}` / `{event.event_status}`  ",
                    f"BTC relevance: `{event.btc_relevance:.2f}`; novelty: `{event.novelty:.2f}`  ",
                    f"Volatility/horizon: `{event.volatility_impact}` / `{event.expected_horizon}`",
                    "",
                ]
            )
    if report.contradictions:
        lines.extend(["## Contradictions", "", *[f"- {item}" for item in report.contradictions], ""])
    if report.missing_information:
        lines.extend(["## Missing information", "", *[f"- {item}" for item in report.missing_information], ""])
    if report.images:
        lines.extend(["## Related news images", ""])
        for image in report.images:
            alt = (image.alt_text or "Related news image").replace("]", "")
            lines.extend([f"![{alt}]({image.image_url})", f"Source: {image.source_page_url}", ""])
    if report.sources:
        lines.extend(["## Sources", ""])
        for source in report.sources:
            lines.append(f"- [{source.title}]({source.url}) — {source.source_class}")
    lines.extend(["", f"> {report.risk_notice}"])
    return "\n".join(lines)


def _serialize_content(content: Any) -> str:
    if isinstance(content, BaseModel):
        return content.model_dump_json(indent=2)
    if isinstance(content, (dict, list)):
        return json.dumps(content, indent=2, ensure_ascii=False, default=str)
    return str(content)


def main() -> int:
    settings = NewsAgentSettings.load()
    parser = argparse.ArgumentParser(description="Run the isolated Agno news intelligence agent.")
    parser.add_argument("prompt", nargs="+", help="News research question or topic")
    parser.add_argument("--json", action="store_true", help="Print the validated report as JSON")
    parser.add_argument("--debug", action="store_true", help="Enable Agno debug logging")
    parser.add_argument("--save", type=Path, help="Optionally save the rendered report to a file")
    parser.add_argument(
        "--session-id",
        default=settings.default_session_id,
        help="Session to create or resume (default: %(default)s)",
    )
    parser.add_argument(
        "--user-id",
        default=settings.default_user_id,
        help="User owning the session (default: %(default)s)",
    )
    args = parser.parse_args()

    prompt = " ".join(args.prompt).strip()
    try:
        pipeline_result = run_news_pipeline(
            prompt,
            settings=settings,
            session_id=args.session_id,
            user_id=args.user_id,
            debug_mode=args.debug,
        )
        response = pipeline_result.report_response
    except Exception as exc:
        parser.exit(1, f"News agent failed: {exc}\n")

    session_id = response.session_id or args.session_id
    run_id = response.run_id
    if isinstance(response.content, NewsAnalysisReport):
        if args.json:
            output = json.dumps(
                {
                    "session_id": session_id,
                    "research_session_id": pipeline_result.research_session_id,
                    "run_id": run_id,
                    "model": pipeline_result.model_id,
                    "research_tools": pipeline_result.research_tools,
                    "report": response.content.model_dump(mode="json"),
                },
                indent=2,
                ensure_ascii=False,
            )
        else:
            output = "\n".join(
                [
                    f"Session: `{session_id}`  ",
                    f"Research session: `{pipeline_result.research_session_id}`  ",
                    f"Run: `{run_id}`  ",
                    f"Model: `{pipeline_result.model_id}`  ",
                    f"Research tools: `{', '.join(pipeline_result.research_tools)}`",
                    "",
                    _markdown_report(response.content),
                ]
            )
    else:
        output = _serialize_content(response.content)
        output += (
            f"\n\nSession: {session_id}\nRun: {run_id}"
            "\nWARNING: The model response did not validate as NewsAnalysisReport."
        )

    print(output)
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
