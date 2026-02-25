"""
register_patients.py — Automates patient registration via the PivotPoint intake form.

Usage:
  pip install playwright
  playwright install chromium
  python register_patients.py             # Register all 5 canonical patients
  python register_patients.py --patient A # Register a specific patient (A-E)
  python register_patients.py --headless  # Run without browser window (default: visible)
"""

import argparse
import asyncio
import time
from playwright.async_api import async_playwright, Page

# ─────────────────────────────────────────────────────────────────
# Patient records — phone numbers use real US area codes to pass
# validation. DOB format matches the form's MM/DD/YYYY fields.
# ─────────────────────────────────────────────────────────────────

PATIENTS = {
    "A": {
        "label": "Thomas Nguyen (Canonical C1, C2)",
        "first_name": "Thomas",
        "last_name": "Nguyen",
        "email": "thomas.nguyen.pivottest@mailinator.com",
        "phone": "6195550142",   # San Diego area code (619), passes US validation
        "dob_month": "08",
        "dob_day": "14",
        "dob_year": "1985",
    },
    "B": {
        "label": "Rebecca Okafor (Canonical C3, C7)",
        "first_name": "Rebecca",
        "last_name": "Okafor",
        "email": "rebecca.okafor.pivottest@mailinator.com",
        "phone": "7025550388",   # Las Vegas area code (702)
        "dob_month": "02",
        "dob_day": "28",
        "dob_year": "1978",
    },
    "C": {
        "label": "Marcus Chen (Canonical C4)",
        "first_name": "Marcus",
        "last_name": "Chen",
        "email": "marcus.chen.pivottest@mailinator.com",
        "phone": "4155550467",   # San Francisco area code (415)
        "dob_month": "11",
        "dob_day": "11",
        "dob_year": "1990",
    },
    "D": {
        "label": "Susan Hartley (Canonical C6)",
        "first_name": "Susan",
        "last_name": "Hartley",
        "email": "susan.hartley.pivottest@mailinator.com",
        "phone": "3125550512",   # Chicago area code (312)
        "dob_month": "04",
        "dob_day": "03",
        "dob_year": "1955",
    },
    "E": {
        "label": "Carlos Rivera (Canonical C5 — New Patient)",
        "first_name": "Carlos",
        "last_name": "Rivera",
        "email": "carlos.rivera.pivottest@mailinator.com",
        "phone": "8585550634",   # San Diego area code (858)
        "dob_month": "07",
        "dob_day": "07",
        "dob_year": "1982",
    },
}

INTAKE_URL = "https://pivotpointortho.com/intake"


async def fill_patient(page: Page, patient: dict, headless: bool = False) -> bool:
    """
    Fill and submit the intake form for one patient.
    Returns True on success, False on failure.
    """
    print(f"\n  → Navigating to {INTAKE_URL}")
    await page.goto(INTAKE_URL, wait_until="networkidle")
    await page.wait_for_timeout(1000)

    print(f"  → Filling form for {patient['first_name']} {patient['last_name']}")

    # Try common input field selectors — Next.js forms vary in structure
    # Strategy: try by placeholder, then by name, then by label association
    async def fill_field(label_text: str, value: str, field_type: str = "text"):
        """Try multiple strategies to find and fill a field."""
        strategies = [
            f"input[placeholder*='{label_text}' i]",
            f"input[name*='{label_text.lower().replace(' ', '')}']",
            f"input[name*='{label_text.lower().replace(' ', '_')}']",
            f"input[id*='{label_text.lower().replace(' ', '')}']",
            f"input[id*='{label_text.lower().replace(' ', '_')}']",
        ]
        for selector in strategies:
            try:
                elem = page.locator(selector).first
                if await elem.count() > 0:
                    await elem.fill(value)
                    return True
            except Exception:
                continue

        # Last resort: find by label text
        try:
            await page.get_by_label(label_text, exact=False).first.fill(value)
            return True
        except Exception:
            pass

        print(f"    ⚠️  Could not find field: {label_text}")
        return False

    # Fill each field
    await fill_field("First Name", patient["first_name"])
    await fill_field("Last Name", patient["last_name"])
    await fill_field("Email", patient["email"])
    await fill_field("Phone", patient["phone"])

    # DOB — try combined field first, then separate month/day/year fields
    dob_combined = f"{patient['dob_month']}/{patient['dob_day']}/{patient['dob_year']}"
    dob_filled = await fill_field("DOB", dob_combined)
    if not dob_filled:
        dob_filled = await fill_field("Date of Birth", dob_combined)
    if not dob_filled:
        # Try separate fields
        await fill_field("Month", patient["dob_month"])
        await fill_field("Day", patient["dob_day"])
        await fill_field("Year", patient["dob_year"])

    # Screenshot before submit for debugging
    if not headless:
        await page.wait_for_timeout(500)

    # Find and click submit button
    submit_strategies = [
        "button[type='submit']",
        "button:has-text('Submit')",
        "button:has-text('Register')",
        "button:has-text('Try our demo')",
        "input[type='submit']",
    ]

    submitted = False
    for selector in submit_strategies:
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0:
                print(f"  → Clicking submit button: {selector}")
                await btn.click()
                submitted = True
                break
        except Exception:
            continue

    if not submitted:
        print(f"  ❌ Could not find submit button")
        return False

    # Wait for response
    await page.wait_for_timeout(2000)

    # Check for success indicator
    page_text = await page.inner_text("body")
    success_phrases = ["success", "thank you", "registered", "submitted", "confirmation", "welcome"]
    error_phrases = ["error", "invalid", "required", "failed"]

    if any(p in page_text.lower() for p in success_phrases):
        print(f"  ✅ Successfully registered {patient['first_name']} {patient['last_name']}")
        return True
    elif any(p in page_text.lower() for p in error_phrases):
        print(f"  ❌ Form error for {patient['first_name']} {patient['last_name']}")
        print(f"     Page text snippet: {page_text[:200]}")
        return False
    else:
        print(f"  ⚠️  Submitted but unclear result — check browser window")
        print(f"     Page text snippet: {page_text[:200]}")
        return True  # Assume success if no error


async def main(patient_keys: list[str], headless: bool):
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for key in patient_keys:
            patient = PATIENTS[key]
            print(f"\n{'='*55}")
            print(f"Patient {key}: {patient['label']}")
            print(f"{'='*55}")

            success = await fill_patient(page, patient, headless)
            results[key] = success

            if success:
                # Brief pause between submissions
                await page.wait_for_timeout(1500)

        await browser.close()

    # Summary
    print(f"\n{'='*55}")
    print("Registration Summary")
    print(f"{'='*55}")
    for key, success in results.items():
        patient = PATIENTS[key]
        icon = "✅" if success else "❌"
        print(f"  {icon} Patient {key}: {patient['first_name']} {patient['last_name']}")

    print("\n📝 Update CANONICAL_TESTS.md with the actual phone numbers used:")
    for key, patient in PATIENTS.items():
        if key in patient_keys:
            print(f"   Patient {key} ({patient['first_name']} {patient['last_name']}): {patient['phone']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register canonical test patients via intake form")
    parser.add_argument("--patient", choices=list(PATIENTS.keys()),
                        help="Register a specific patient (A-E). Omit to register all.")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run browser in headless mode (no visible window)")
    args = parser.parse_args()

    keys = [args.patient] if args.patient else list(PATIENTS.keys())
    asyncio.run(main(keys, args.headless))
