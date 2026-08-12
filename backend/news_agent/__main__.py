from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import NewsAgentSettings
from .pipeline import run_news_pipeline


def main() -> int:
    settings = NewsAgentSettings.load()
    parser = argparse.ArgumentParser(description="Run the isolated Agno news intelligence agent.")
    parser.add_argument("prompt", nargs="+", help="News research question or topic")
    parser.add_argument("--json", action="store_true", help="Wrap the native Markdown and run metadata as JSON")
    parser.add_argument("--debug", action="store_true", help="Enable Agno debug logging")
    parser.add_argument("--save", type=Path, help="Optionally save the Markdown analysis to a file")
    parser.add_argument("--session-id", default=settings.default_session_id)
    parser.add_argument("--user-id", default=settings.default_user_id)
    args = parser.parse_args()

    try:
        result = run_news_pipeline(
            " ".join(args.prompt).strip(),
            settings=settings,
            session_id=args.session_id,
            user_id=args.user_id,
            debug_mode=args.debug,
        )
    except Exception as exc:
        parser.exit(1, f"News agent failed: {exc}\n")

    analysis = result.markdown or str(result.report_response.content or "")
    if args.json:
        output = json.dumps(
            {
                "session_id": result.session_id,
                "run_id": result.report_response.run_id,
                "model": result.model_id,
                "research_tools": result.research_tools,
                "analysis": analysis,
            },
            indent=2,
            ensure_ascii=False,
        )
    else:
        output = analysis

    print(output)
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
