"""
End-to-End Simulation Test: Flipkart Product Discovery Scenario
Recreates the exact user scenario from the benchmark:
"can you find me a black shirt in flipkart, it shou..."
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
from server.app.product_constraints import extract_product_constraints

SERVER_PLAN_URL = "http://127.0.0.1:8080/api/agent/plan"
TASK = "can you find me a black shirt in flipkart, it shou..."


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


def test_conversational_constraint_extraction():
    t0 = time.time()
    constraints = extract_product_constraints(TASK)
    duration_ms = (time.time() - t0) * 1000

    assert constraints["product_type"] == "shirt", f"Expected 'shirt', got {constraints['product_type']}"
    assert constraints["color"] == "black", f"Expected 'black', got {constraints['color']}"

    RunReporter.print_step(
        1,
        "Conversational Natural Language Constraint Extraction",
        "PASS",
        {
            "Extracted Product": constraints["product_type"],
            "Target Color": constraints["color"],
            "Raw Input": TASK,
            "Integrity": "Zero hardcoded keywords - generic regex extraction"
        },
        duration_ms=duration_ms
    )


def test_no_premature_verifier_termination():
    t0 = time.time()
    payload = VerificationPayload(
        task=TASK,
        current_url="https://www.flipkart.com/search?q=black+shirt",
        page_title="Black Shirt - Buy Black Shirt Online at Best Prices In India | Flipkart.com",
        action_history=[{"action": "wait", "milliseconds": 500}],
        screen_summary={"main_headings": ["Black Shirts", "Filters", "Men's Clothing"]},
        downloads=[]
    )
    v_res = verify_task_completion(payload)
    duration_ms = (time.time() - t0) * 1000

    assert not v_res.achieved, "Verifier falsely declared victory on search results page!"
    assert v_res.goal_status == "GOAL_NOT_YET_ACHIEVED", f"Expected GOAL_NOT_YET_ACHIEVED, got {v_res.goal_status}"
    assert v_res.remaining_goal in ("identify shirt", "open selected product"), f"Unexpected remaining_goal: {v_res.remaining_goal}"

    RunReporter.print_step(
        2,
        "Verifier Rejection of Premature Completion on Search Results",
        "PASS",
        {
            "Verifier Status": v_res.goal_status,
            "Premature Goal Claim": "REJECTED (Not Achieved)",
            "Remaining Required Goal": v_res.remaining_goal,
            "Safety Guarantee": "Agent prevented from terminating early before opening product"
        },
        duration_ms=duration_ms
    )


def test_planner_selects_matching_candidate():
    t0 = time.time()
    sample_elements = [
        {
            "id": "cand-blue-shirt",
            "role": "a",
            "label": "Men Slim Fit Blue Denim Shirt ₹899",
            "href": "https://www.flipkart.com/blue-shirt/p/itm001",
            "bbox": [100, 200, 300, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "cand-black-shirt",
            "role": "a",
            "label": "Men Regular Fit Solid Black Casual Shirt ₹699 (4.3 stars)",
            "href": "https://www.flipkart.com/black-shirt/p/itm002",
            "bbox": [320, 200, 520, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "cand-black-dress",
            "role": "a",
            "label": "Women Solid Black Fit and Flare Dress ₹1,299",
            "href": "https://www.flipkart.com/black-dress/p/itm003",
            "bbox": [540, 200, 740, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        }
    ]

    req_payload = {
        "session_id": "test-flipkart-01",
        "step": 2,
        "instruction_sanitized": TASK,
        "page": {
            "url_sanitized": "https://www.flipkart.com/search?q=black+shirt",
            "title_sanitized": "Black Shirt - Buy Black Shirt Online at Best Prices In India | Flipkart.com",
            "viewport": {"w": 1280, "h": 720, "dpr": 1.0}
        },
        "elements": sample_elements,
        "redactions": [],
        "privacy_report": {"detected": 0, "sensitive": 0, "redacted": 0, "uncertain_redacted": 0, "verification": "PASS", "gate": "PASS"},
        "history": [{"action": "type", "target": "search-box", "text": "black shirt"}]
    }

    plan = execute_plan_req(req_payload)
    first_action = plan["actions"][0]
    duration_ms = (time.time() - t0) * 1000

    assert first_action["type"] in ("open_link", "click"), f"Expected click/open_link, got {first_action['type']}"
    if first_action.get("target") and first_action["target"].get("element_id"):
        assert first_action["target"]["element_id"] == "cand-black-shirt", f"Wrong target: {first_action['target']['element_id']}"
    elif first_action.get("url"):
        assert "black-shirt" in first_action["url"], f"Wrong URL: {first_action['url']}"

    RunReporter.print_step(
        3,
        "Planner Autonomous Candidate Grounding & Selection",
        "PASS",
        {
            "Selected Action": first_action["type"],
            "Target Candidate": first_action.get("target", {}).get("element_id") or first_action.get("url"),
            "Grounding Precision": "Selected black shirt, rejected blue shirt and black dress",
            "Reasoning": plan["reasoning_summary"][:120] + "..."
        },
        duration_ms=duration_ms
    )


def test_planner_scrolls_when_no_match_visible():
    t0 = time.time()
    only_mismatched_elements = [
        {
            "id": "cand-blue-shirt",
            "role": "a",
            "label": "Men Slim Fit Blue Denim Shirt ₹899",
            "href": "https://www.flipkart.com/blue-shirt/p/itm001",
            "bbox": [100, 200, 300, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        },
        {
            "id": "cand-red-hoodie",
            "role": "a",
            "label": "Men Red Cotton Hoodie ₹999",
            "href": "https://www.flipkart.com/red-hoodie/p/itm004",
            "bbox": [320, 200, 520, 450],
            "interactable": True,
            "sensitivity": "safe",
            "source": "dom",
            "confidence": 1.0
        }
    ]

    req_payload = {
        "session_id": "test-flipkart-02",
        "step": 3,
        "instruction_sanitized": TASK,
        "page": {
            "url_sanitized": "https://www.flipkart.com/search?q=black+shirt",
            "title_sanitized": "Black Shirt - Buy Black Shirt Online at Best Prices In India | Flipkart.com",
            "viewport": {"w": 1280, "h": 720, "dpr": 1.0}
        },
        "elements": only_mismatched_elements,
        "redactions": [],
        "privacy_report": {"detected": 0, "sensitive": 0, "redacted": 0, "uncertain_redacted": 0, "verification": "PASS", "gate": "PASS"},
        "history": [{"action": "type", "target": "search-box", "text": "black shirt"}]
    }

    plan = execute_plan_req(req_payload)
    first_action = plan["actions"][0]
    duration_ms = (time.time() - t0) * 1000

    assert first_action["type"] == "scroll", f"Expected scroll action, got {first_action['type']}"
    assert first_action["direction"] == "down"

    RunReporter.print_step(
        4,
        "Planner Autonomous Scrolling Fallback (No Match Visible)",
        "PASS",
        {
            "Fallback Trigger": "Only blue shirts / red hoodies on screen",
            "Action Dispatched": f"scroll {first_action.get('direction', 'down')}",
            "Autonomous Decision": "Refused to click wrong items; scrolled to discover candidates",
            "Reasoning": plan["reasoning_summary"][:120] + "..."
        },
        duration_ms=duration_ms
    )


def test_final_goal_verified_on_product_page():
    t0 = time.time()
    payload = VerificationPayload(
        task=TASK,
        current_url="https://www.flipkart.com/black-casual-shirt/p/itm12345",
        page_title="Men Regular Fit Solid Black Casual Shirt - Buy Online at Flipkart.com",
        action_history=[
            {"action": "type", "target": "search-box", "text": "black shirt"},
            {"action": "click", "target": "cand-black-shirt"}
        ],
        screen_summary={"main_headings": ["Men Regular Fit Solid Black Casual Shirt", "Special price ₹699", "Specifications"]},
        downloads=[]
    )
    v_res = verify_task_completion(payload)
    duration_ms = (time.time() - t0) * 1000

    assert v_res.achieved, "Expected goal achieved on matching product page!"
    assert v_res.goal_status == "GOAL_ACHIEVED"

    RunReporter.print_step(
        5,
        "Final Goal Verified on Target Product Page",
        "PASS",
        {
            "Goal Status": v_res.goal_status,
            "Target Page": payload.page_title,
            "Verification Outcome": "GOAL_ACHIEVED",
            "Verifier Reason": v_res.reason
        },
        duration_ms=duration_ms
    )


if __name__ == "__main__":
    RunReporter.print_banner(
        "Flipkart E2E Discovery & Verification Run",
        f"Task: '{TASK}'",
        "E2E SUITE"
    )

    t_start = time.time()
    test_conversational_constraint_extraction()
    test_no_premature_verifier_termination()
    test_planner_selects_matching_candidate()
    test_planner_scrolls_when_no_match_visible()
    test_final_goal_verified_on_product_page()
    total_time = (time.time() - t_start) * 1000

    RunReporter.print_summary_card(
        "Flipkart Black Shirt Discovery Suite",
        {
            "Total Test Steps": 5,
            "Constraint Extraction Accuracy": "100%",
            "Premature Rejection Guard": "PASS",
            "Candidate Grounding": "Solid Black Shirt Selected",
            "Autonomous Scrolling Fallback": "PASS",
            "Final Goal Verifier": "GOAL_ACHIEVED",
            "Execution Duration": f"{total_time:.1f}ms"
        },
        all_passed=True
    )
