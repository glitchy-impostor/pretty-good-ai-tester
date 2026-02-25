"""
Conversation logger — saves transcripts and call metadata to disk.
"""

import json
import os
from datetime import datetime
from pathlib import Path

TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"
TRANSCRIPTS_DIR.mkdir(exist_ok=True)


class CallLogger:
    def __init__(self, scenario_id: int, scenario_name: str, call_sid: str = "unknown"):
        self.scenario_id = scenario_id
        self.scenario_name = scenario_name
        self.call_sid = call_sid
        self.started_at = datetime.now()
        self.turns: list[dict] = []
        self.metadata: dict = {}

    def log_turn(self, speaker: str, text: str):
        """Log a single conversation turn. speaker: 'patient' or 'agent'"""
        self.turns.append({
            "timestamp": datetime.now().isoformat(),
            "speaker": speaker,
            "text": text.strip(),
        })
        # Print to console in real time
        label = "🧑 PATIENT" if speaker == "patient" else "🤖 AGENT"
        print(f"  {label}: {text.strip()}")

    def set_metadata(self, **kwargs):
        self.metadata.update(kwargs)

    def save(self) -> Path:
        """Save the full transcript to a JSON file and return the path."""
        filename = f"call_{self.scenario_id:02d}_{self.scenario_name}_{self.started_at.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = TRANSCRIPTS_DIR / filename

        data = {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "call_sid": self.call_sid,
            "started_at": self.started_at.isoformat(),
            "ended_at": datetime.now().isoformat(),
            "metadata": self.metadata,
            "transcript": self.turns,
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        print(f"\n📝 Transcript saved: {filepath}")
        return filepath

    def get_full_transcript_text(self) -> str:
        """Return a readable text version of the transcript."""
        lines = [
            f"=== Call: Scenario {self.scenario_id} - {self.scenario_name} ===",
            f"Started: {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for turn in self.turns:
            label = "PATIENT" if turn["speaker"] == "patient" else "AGENT"
            lines.append(f"[{label}]: {turn['text']}")
        return "\n".join(lines)
