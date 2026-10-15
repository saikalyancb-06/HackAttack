"""The plan endpoint hands a login / OTP wall to the user (ask_user) whichever planner is active, and lets the
agent continue once the history says the user finished their turn.   python -m pytest server/tests/test_operator_handoff.py"""
from fastapi.testclient import TestClient

from server.app.main import app

client = TestClient(app)
LOGIN_WALL = [{"id": "m", "role": "input", "label": "Enter Email/Mobile number", "bbox": [0, 0, 1, 1]},
              {"id": "r", "role": "button", "label": "Request OTP", "bbox": [0, 0, 1, 1]}]


def _pkg(elements, history=None):
    return {"session_id": "s", "step": 1, "instruction_sanitized": "find a black shirt on flipkart and add it to cart",
            "page": {"url_sanitized": "https://www.flipkart.com/shirt/p/x", "title_sanitized": "Shop", "viewport": {}},
            "elements": elements, "redactions": [], "history": history or [],
            "privacy_report": {"detected": 0, "sensitive": 0, "redacted": 0}}


def test_login_wall_hands_page_to_user():
    r = client.post("/api/agent/plan", json=_pkg(LOGIN_WALL)).json()
    assert r["actions"][0]["type"] == "ask_user" and "log in" in r["actions"][0]["question"].lower()
    assert r["state"] == "AWAIT_USER_INTERVENTION"


def test_resumes_right_after_user_turn():
    r = client.post("/api/agent/plan", json=_pkg(LOGIN_WALL, [{"action": "ask_user", "user_intervention_completed": True}])).json()
    assert r["state"] != "AWAIT_USER_INTERVENTION"


if __name__ == "__main__":
    test_login_wall_hands_page_to_user()
    test_resumes_right_after_user_turn()
    print("operator handoff OK")
