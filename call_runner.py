"""
Call runner — makes outbound calls via Twilio for each scenario.

Scheduling behavior:
  - Single scenario: place call, wait for it to complete, exit
  - Multiple scenarios: place call, wait for completion, immediately place next

Usage:
  python call_runner.py --scenario 1         # Run a specific scenario
  python call_runner.py --all                # Run all 10 back-to-back
  python call_runner.py --range 1 5          # Run scenarios 1-5 back-to-back
  python call_runner.py --all --gap 5        # Add a 5s pause between calls (default: 3s)
"""

import argparse
import os
import time

from dotenv import load_dotenv
from twilio.rest import Client

from scenarios.scenarios import get_all_scenarios, get_scenario, get_all_canonical_scenarios, get_canonical_scenario, get_all_jailbreak_scenarios, get_jailbreak_scenario

load_dotenv()

# Twilio call statuses that mean the call is over
TERMINAL_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}

# How often to poll Twilio for call status (seconds)
POLL_INTERVAL = 5


def get_twilio_client() -> Client:
    return Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


def make_call(scenario_id: int, client: Client) -> str:
    """Place an outbound call. Returns the CallSid."""
    from_number = os.environ["TWILIO_PHONE_NUMBER"]
    to_number = os.environ["TARGET_PHONE_NUMBER"]
    public_url = os.environ["PUBLIC_URL"].rstrip("/")
    webhook_url = f"{public_url}/incoming-call/{scenario_id}"

    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        try:
            scenario = get_canonical_scenario(scenario_id)
        except ValueError:
            scenario = get_jailbreak_scenario(scenario_id)
    print(f"\n{'='*60}")
    print(f"📞 Scenario {scenario_id}: {scenario['name']}")
    print(f"   {scenario['description']}")
    print(f"   From: {from_number} → To: {to_number}")
    print(f"{'='*60}")

    call = client.calls.create(
        to=to_number,
        from_=from_number,
        url=webhook_url,
        method="POST",
        time_limit=300,  # 5 minute hard cap per call
    )

    print(f"   SID: {call.sid} | Status: {call.status}")
    return call.sid


def wait_for_completion(call_sid: str, client: Client) -> str:
    """
    Poll Twilio every POLL_INTERVAL seconds until the call reaches a
    terminal status. Returns the final status string.
    """
    print(f"   ⏳ Waiting for call to complete...", end="", flush=True)
    start = time.time()

    while True:
        time.sleep(POLL_INTERVAL)
        call = client.calls(call_sid).fetch()
        status = call.status

        elapsed = int(time.time() - start)
        print(f"\r   ⏳ Status: {status} ({elapsed}s elapsed)          ", end="", flush=True)

        if status in TERMINAL_STATUSES:
            duration = call.duration or "?"
            print(f"\r   ✅ Call {status} after {duration}s                    ")
            return status


def run_scenario_and_wait(scenario_id: int, client: Client) -> str:
    """Place a call and block until it's done. Returns final status."""
    call_sid = make_call(scenario_id, client)
    final_status = wait_for_completion(call_sid, client)
    return final_status


def run_multiple(scenario_ids: list[int], gap_seconds: int):
    """Run a list of scenarios back-to-back, starting each immediately after the last ends."""
    client = get_twilio_client()
    total = len(scenario_ids)
    results = []

    print(f"\n🚀 Running {total} scenario(s) back-to-back (gap: {gap_seconds}s between calls)\n")

    for i, scenario_id in enumerate(scenario_ids):
        status = run_scenario_and_wait(scenario_id, client)
        results.append((scenario_id, status))

        if i < total - 1:
            if gap_seconds > 0:
                print(f"\n   Pausing {gap_seconds}s before next call...")
                time.sleep(gap_seconds)
            else:
                print(f"\n   Starting next call immediately...")

    # Summary
    print(f"\n{'='*60}")
    print(f"🎉 All {total} scenario(s) complete!")
    print(f"{'='*60}")
    for scenario_id, status in results:
        try:
            scenario = get_scenario(scenario_id)
        except ValueError:
            try:
                scenario = get_canonical_scenario(scenario_id)
            except ValueError:
                scenario = get_jailbreak_scenario(scenario_id)
        icon = "✅" if status == "completed" else "⚠️"
        print(f"  {icon} Scenario {scenario_id} ({scenario['name']}): {status}")


def main():
    parser = argparse.ArgumentParser(
        description="Voice Bot Call Runner — triggers next call immediately after each completes"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", type=int, help="Run a single scenario by ID (1-10)")
    group.add_argument("--all", action="store_true", help="Run all 10 scenarios back-to-back")
    group.add_argument(
        "--range", nargs=2, type=int, metavar=("START", "END"),
        help="Run a range of scenarios, e.g. --range 1 5"
    )
    group.add_argument("--canonical", action="store_true", help="Run all 7 canonical test scenarios (C1-C7, IDs 11-17)")
    group.add_argument("--canonical-scenario", type=int, metavar="ID", help="Run a single canonical scenario by ID (11-17)")
    group.add_argument("--jailbreak", action="store_true", help="Run both jailbreak stress test scenarios (J1-J2, IDs 18-19)")
    group.add_argument("--jailbreak-scenario", type=int, metavar="ID", help="Run a single jailbreak scenario by ID (18 or 19)")
    parser.add_argument(
        "--gap", type=int, default=3,
        help="Seconds to pause between calls (default: 3). Use 0 for immediate."
    )

    args = parser.parse_args()

    # Validate env vars
    required_vars = [
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
        "TARGET_PHONE_NUMBER", "PUBLIC_URL"
    ]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("   Copy .env.example to .env and fill in your credentials.")
        return

    if args.scenario:
        run_multiple([args.scenario], gap_seconds=0)
    elif args.all:
        ids = [s["id"] for s in get_all_scenarios()]
        run_multiple(ids, gap_seconds=args.gap)
    elif args.range:
        start, end = args.range
        run_multiple(list(range(start, end + 1)), gap_seconds=args.gap)
    elif args.canonical:
        ids = [s["id"] for s in get_all_canonical_scenarios()]
        print("\n⚠️  Canonical tests require pre-registered patients. See CANONICAL_TESTS.md")
        run_multiple(ids, gap_seconds=args.gap)
    elif args.canonical_scenario:
        print("\n⚠️  Canonical tests require pre-registered patients. See CANONICAL_TESTS.md")
        run_multiple([args.canonical_scenario], gap_seconds=0)
    elif args.jailbreak:
        ids = [s["id"] for s in get_all_jailbreak_scenarios()]
        print("\n🔴 Running jailbreak stress tests")
        run_multiple(ids, gap_seconds=args.gap)
    elif args.jailbreak_scenario:
        print("\n🔴 Running jailbreak stress test")
        run_multiple([args.jailbreak_scenario], gap_seconds=0)


if __name__ == "__main__":
    main()
