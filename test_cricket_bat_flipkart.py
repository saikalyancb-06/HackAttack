"""
E2E Simulation for User's Task:
"find me the best selling cricket bat in flipkart"
Rendered with Rich Formatted Terminal Output & RunReporter.
"""
import sys
import time
from pathlib import Path

# Ensure E:\hackattackamc is on python path
ROOT = Path("E:/hackattackamc")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reports.run_reporter import RunReporter
from server.app.schemas.schemas import VerificationPayload
from server.app.verifier import verify_task_completion

SERVER_PLAN_URL = "http://127.0.0.1:8080/api/agent/plan"
TASK = "find me the best selling cricket bat in flipkart"


def execute_plan_req(req_payload: dict) -> dict:
    """Dispatches plan request to running server, or falls back to in-process FastAPI TestClient."""
    import requests
    try:
        res = requests.post(SERVER_PLAN_URL, json=req_payload, timeout=1.5)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass

    from fastapi.testclient import TestClient
    from server.app.main import app
    client = TestClient(app)
    res = client.post("/api/agent/plan", json=req_payload)
    assert res.status_code == 200, f"Plan request failed: {res.text}"
    return res.json()


def test_homepage_search_intent_not_ad_clicking():
    t0 = time.time()
    homepage_elements = [
        {
            "id": "search-input-box",
            "role": "input",
            "label": "Search for Products, Brands and More",
            "bbox": [200, 20, 600, 60],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "search-submit-btn",
            "role": "button",
            "label": "Search",
            "bbox": [610, 20, 660, 60],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "ad-beardo-perfume",
            "role": "a",
            "label": "Beardo Godfather Revenge Perfume 100ml",
            "href": "https://www.flipkart.com/beardo-godfather-revenge-perfume/p/itm9ffc4bf3f",
            "bbox": [100, 200, 300, 400],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "footer-gift-cards",
            "role": "a",
            "label": "Gift Cards",
            "href": "https://www.flipkart.com/the-gift-card-store",
            "bbox": [100, 800, 200, 830],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        }
    ]

    req_payload = {
        "session_id": "test-cricket-01",
        "step": 1,
        "instruction_sanitized": TASK,
        "page": {
            "url_sanitized": "https://www.flipkart.com",
            "title_sanitized": "Online Shopping Site for Mobiles, Electronics, Furniture, Grocery, Lifestyle, Books & More. Best Offers!",
            "viewport": {"w": 1280, "h": 720, "dpr": 1.0}
        },
        "elements": homepage_elements,
        "redactions": [],
        "privacy_report": {"detected": 0, "sensitive": 0, "redacted": 0, "uncertain_redacted": 0, "verification": "PASS", "gate": "PASS"},
        "history": []
    }

    plan = execute_plan_req(req_payload)
    first_act = plan["actions"][0]
    duration_ms = (time.time() - t0) * 1000

    assert first_act["type"] in ("type", "search"), f"Expected type/search, got {first_act['type']}"
    if first_act.get("value") and first_act["value"].get("text"):
        assert "cricket bat" in first_act["value"]["text"].lower()
    assert first_act.get("target", {}).get("element_id") != "ad-beardo-perfume", "AGENT CLICKED BEARDO AD ON HOMEPAGE!"
    assert first_act.get("target", {}).get("element_id") != "footer-gift-cards", "AGENT CLICKED GIFT CARDS LINK ON HOMEPAGE!"

    RunReporter.print_step(
        1,
        "Homepage Search Input Selection (Ignores Ads & Promos)",
        "PASS",
        {
            "Action Type": first_act["type"],
            "Target Input": first_act.get("target", {}).get("element_id"),
            "Typed Query": first_act.get("value", {}).get("text"),
            "Rejection": "Ignored Beardo perfume ad & Gift cards footer",
            "Reasoning": plan["reasoning_summary"][:120] + "..."
        },
        duration_ms=duration_ms
    )


def test_search_results_best_seller_candidate_selection():
    t0 = time.time()
    results_elements = [
        {
            "id": "cand_sg_bat",
            "role": "a",
            "label": "SG Scorer Classic Kashmir Willow Cricket Bat ₹1,299 (4.4 stars)",
            "href": "https://www.flipkart.com/sg-scorer-cricket-bat/p/itm001",
            "bbox": [100, 200, 300, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "badge_best_seller",
            "role": "div",
            "label": "Best Seller",
            "bbox": [100, 180, 200, 200],
            "interactable": False,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "cand_ss_bat",
            "role": "a",
            "label": "SS Master 500 English Willow Cricket Bat ₹3,499 (4.1 stars)",
            "href": "https://www.flipkart.com/ss-master-cricket-bat/p/itm002",
            "bbox": [320, 200, 520, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        }
    ]

    req_payload = {
        "session_id": "test-cricket-02",
        "step": 2,
        "instruction_sanitized": TASK,
        "page": {
            "url_sanitized": "https://www.flipkart.com/search?q=cricket+bat",
            "title_sanitized": "Cricket Bat - Buy Cricket Bat Online at Best Prices In India | Flipkart.com",
            "viewport": {"w": 1280, "h": 720, "dpr": 1.0}
        },
        "elements": results_elements,
        "redactions": [],
        "privacy_report": {"detected": 0, "sensitive": 0, "redacted": 0, "uncertain_redacted": 0, "verification": "PASS", "gate": "PASS"},
        "history": [{"action": "type", "target": "search-input-box", "text": "cricket bat"}]
    }

    plan = execute_plan_req(req_payload)
    first_act = plan["actions"][0]
    duration_ms = (time.time() - t0) * 1000

    assert first_act["type"] in ("open_link", "click", "scroll"), f"Expected click/open_link/scroll, got {first_act['type']}"
    target_id = first_act.get("target", {}).get("element_id") if first_act.get("target") else None
    url = first_act.get("url", "")
    assert target_id == "cand_sg_bat" or "sg-scorer" in url, f"Expected Best Seller SG Bat, got {target_id} / {url}"

    RunReporter.print_step(
        2,
        "Search Results Candidate Selection with Best Seller Criterion",
        "PASS",
        {
            "Action Type": first_act["type"],
            "Selected Candidate": target_id or url,
            "Item Details": "SG Scorer Classic Kashmir Willow Cricket Bat (#1 Best Seller)",
            "Reasoning": plan["reasoning_summary"][:120] + "..."
        },
        duration_ms=duration_ms
    )


from server.app.schemas.schemas import ElementMetadata

def test_verifier_goal_achieved_on_product_page():
    t0 = time.time()
    elements = [
        ElementMetadata(
            id="cand_sg_bat",
            role="a",
            label="SG Scorer Classic Kashmir Willow Cricket Bat #1 Best Seller ₹1,299",
            bbox=[100, 200, 300, 450],
            href="https://www.flipkart.com/sg-scorer-cricket-bat/p/itm001"
        )
    ]
    payload = VerificationPayload(
        task=TASK,
        current_url="https://www.flipkart.com/sg-scorer-cricket-bat/p/itm001",
        page_title="SG Scorer Classic Kashmir Willow Cricket Bat - Buy Online at Flipkart.com",
        action_history=[
            {"action": "type", "target": "search-input-box", "text": "cricket bat"},
            {"action": "click", "target": "cand_sg_bat"}
        ],
        elements=elements,
        screen_summary={"main_headings": ["SG Scorer Classic Kashmir Willow Cricket Bat", "#1 Best Seller", "Special price ₹1,299", "Specifications"]},
        downloads=[]
    )
    v_res = verify_task_completion(payload)
    duration_ms = (time.time() - t0) * 1000

    assert v_res.achieved
    assert v_res.goal_status == "GOAL_ACHIEVED"

    RunReporter.print_step(
        3,
        "Independent Verifier Confirms Goal on Opened Product Page",
        "PASS",
        {
            "Goal Status": v_res.goal_status,
            "Target Product": "SG Scorer Classic Kashmir Willow Cricket Bat",
            "Verification Outcome": "GOAL_ACHIEVED",
            "Verifier Reason": v_res.reason
        },
        duration_ms=duration_ms
    )


if __name__ == "__main__":
    RunReporter.print_banner(
        "Flipkart Best Seller Discovery Run",
        "Task: 'find me the best selling cricket bat in flipkart'",
        "SIMULATION"
    )

    t_start = time.time()
    test_homepage_search_intent_not_ad_clicking()
    test_search_results_best_seller_candidate_selection()
    test_verifier_goal_achieved_on_product_page()
    total_time = (time.time() - t_start) * 1000

    RunReporter.print_summary_card(
        "Flipkart Best Seller Cricket Bat Discovery",
        {
            "Total Steps Executed": 3,
            "Ad & Promo Avoidance": "100% Passed",
            "Candidate Grounding": "SG Scorer Classic Best Seller Chosen",
            "Verifier Status": "GOAL_ACHIEVED",
            "Total Execution Time": f"{total_time:.1f}ms",
            "Interactive Dashboard": "reports/dashboard.html"
        },
        all_passed=True
    )
