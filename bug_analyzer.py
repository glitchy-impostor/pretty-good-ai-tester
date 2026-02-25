"""
Bug analyzer — reads saved transcripts and uses GPT-4o to identify
bugs, quality issues, and behavioral problems in the AI agent's responses.

Usage:
  python bug_analyzer.py                    # Analyze all transcripts
  python bug_analyzer.py --file call_01_... # Analyze a specific file
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"

BUG_ANALYSIS_PROMPT = """You are a QA analyst reviewing conversations between a patient and an AI medical office receptionist.

Your job is to identify bugs, quality issues, and behavioral problems in the AGENT's responses.

Categories to check:
1. HALLUCINATION — Agent makes up specific facts (exact hours, addresses, prices, doctor names) that it couldn't know
2. MISUNDERSTANDING — Agent misunderstands what the patient is asking for
3. WRONG_INFORMATION — Agent provides incorrect or contradictory information
4. POOR_UX — Awkward phrasing, unnatural responses, too robotic, repetitive
5. FAILURE_TO_HELP — Agent fails to accomplish a reasonable patient request with no explanation
6. INAPPROPRIATE_RESPONSE — Agent responds in a way that would be harmful or inappropriate in a real medical context
7. TURN_TAKING — Agent interrupts, cuts off, or has timing issues
8. MISSING_CONFIRMATION — Agent completes an action (booking, cancellation) without confirming details

For each bug found, provide:
- category: one of the categories above
- severity: HIGH / MEDIUM / LOW
- description: clear description of the problem
- quote: the exact problematic agent text (or "N/A")
- recommendation: what the agent should have done instead

Return your analysis as JSON in this format:
{
  "scenario_name": "...",
  "overall_quality": "GOOD/FAIR/POOR",
  "bugs": [
    {
      "category": "...",
      "severity": "HIGH/MEDIUM/LOW",
      "description": "...",
      "quote": "...",
      "recommendation": "..."
    }
  ],
  "positive_observations": ["..."],
  "summary": "2-3 sentence overall assessment"
}

If no bugs are found, return an empty bugs array and note positive observations.
"""


def load_transcript(filepath: Path) -> dict:
    with open(filepath) as f:
        return json.load(f)


def format_transcript_for_analysis(data: dict) -> str:
    lines = [f"Scenario: {data['scenario_name']}", ""]
    for turn in data["transcript"]:
        speaker = "AGENT" if turn["speaker"] == "agent" else "PATIENT"
        lines.append(f"[{speaker}]: {turn['text']}")
    return "\n".join(lines)


def analyze_transcript(data: dict) -> dict:
    """Send transcript to GPT-4o for bug analysis."""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    transcript_text = format_transcript_for_analysis(data)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": BUG_ANALYSIS_PROMPT},
            {"role": "user", "content": f"Analyze this transcript:\n\n{transcript_text}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=1000,
    )

    return json.loads(response.choices[0].message.content)


def generate_bug_report(analyses: list[dict], output_path: Path):
    """Generate a consolidated bug report from all analyses."""

    all_bugs = []
    for analysis in analyses:
        for bug in analysis.get("bugs", []):
            bug["scenario"] = analysis.get("scenario_name", "unknown")
            all_bugs.append(bug)

    # Group by severity
    high = [b for b in all_bugs if b["severity"] == "HIGH"]
    medium = [b for b in all_bugs if b["severity"] == "MEDIUM"]
    low = [b for b in all_bugs if b["severity"] == "LOW"]

    # Group by category
    from collections import Counter
    categories = Counter(b["category"] for b in all_bugs)

    report_lines = [
        "# Bug Report: Pretty Good AI Agent",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Calls analyzed: {len(analyses)}",
        "",
        "## Summary",
        f"- Total bugs found: {len(all_bugs)}",
        f"- HIGH severity: {len(high)}",
        f"- MEDIUM severity: {len(medium)}",
        f"- LOW severity: {len(low)}",
        "",
        "## Bug Categories",
    ]
    for cat, count in categories.most_common():
        report_lines.append(f"- {cat}: {count}")

    report_lines.extend(["", "## Per-Call Quality Summary"])
    for analysis in analyses:
        quality = analysis.get("overall_quality", "N/A")
        scenario = analysis.get("scenario_name", "unknown")
        bug_count = len(analysis.get("bugs", []))
        report_lines.append(f"- **{scenario}**: {quality} ({bug_count} bugs)")

    if high:
        report_lines.extend(["", "## 🔴 HIGH Severity Bugs"])
        for bug in high:
            report_lines.extend([
                f"\n### [{bug['scenario']}] {bug['category']}",
                f"**Description**: {bug['description']}",
                f"**Quote**: _{bug.get('quote', 'N/A')}_",
                f"**Recommendation**: {bug['recommendation']}",
            ])

    if medium:
        report_lines.extend(["", "## 🟡 MEDIUM Severity Bugs"])
        for bug in medium:
            report_lines.extend([
                f"\n### [{bug['scenario']}] {bug['category']}",
                f"**Description**: {bug['description']}",
                f"**Quote**: _{bug.get('quote', 'N/A')}_",
                f"**Recommendation**: {bug['recommendation']}",
            ])

    if low:
        report_lines.extend(["", "## 🟢 LOW Severity Bugs"])
        for bug in low:
            report_lines.extend([
                f"\n### [{bug['scenario']}] {bug['category']}",
                f"**Description**: {bug['description']}",
                f"**Quote**: _{bug.get('quote', 'N/A')}_",
                f"**Recommendation**: {bug['recommendation']}",
            ])

    # Positive observations
    all_positives = []
    for analysis in analyses:
        all_positives.extend(analysis.get("positive_observations", []))
    if all_positives:
        report_lines.extend(["", "## ✅ Positive Observations"])
        for obs in all_positives:
            report_lines.append(f"- {obs}")

    report_content = "\n".join(report_lines)
    with open(output_path, "w") as f:
        f.write(report_content)

    print(f"\n📊 Bug report saved: {output_path}")
    return report_content


def main():
    parser = argparse.ArgumentParser(description="Analyze call transcripts for bugs")
    parser.add_argument("--file", type=str, help="Analyze a specific transcript file")
    parser.add_argument("--all", action="store_true", default=True, help="Analyze all transcripts")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not set")
        return

    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(TRANSCRIPTS_DIR.glob("*.json"))

    if not files:
        print(f"❌ No transcript files found in {TRANSCRIPTS_DIR}")
        return

    print(f"🔍 Analyzing {len(files)} transcript(s)...")

    analyses = []
    raw_analyses = {}

    for filepath in files:
        print(f"\n  Processing: {filepath.name}")
        data = load_transcript(filepath)
        analysis = analyze_transcript(data)
        analysis["scenario_name"] = data.get("scenario_name", filepath.stem)
        analyses.append(analysis)
        raw_analyses[filepath.stem] = analysis

        # Print quick summary
        bug_count = len(analysis.get("bugs", []))
        quality = analysis.get("overall_quality", "N/A")
        print(f"  → Quality: {quality} | Bugs: {bug_count}")
        print(f"  → {analysis.get('summary', '')[:100]}...")

    # Save raw analyses
    raw_path = TRANSCRIPTS_DIR / "analyses.json"
    with open(raw_path, "w") as f:
        json.dump(raw_analyses, f, indent=2)

    # Generate consolidated report
    report_path = Path(__file__).parent / "BUG_REPORT.md"
    generate_bug_report(analyses, report_path)

    print(f"\n✅ Analysis complete!")
    print(f"   Raw analyses: {raw_path}")
    print(f"   Bug report: {report_path}")


if __name__ == "__main__":
    main()
