"""
Comprehensive Test Suite: Human-in-the-Loop User Intervention (Wait & Resume).
SIH PS 26171 / Visiionary Autonomous Browser Agent.

Validates the feature requested by user:
1. Exact Flipkart Scenario: "find me the best selling shirt in flipkart and add it to my cart".
   - Step 1: Searches Flipkart for shirt.
   - Step 2: Identifies and opens #1 Best Seller shirt candidate.
   - Step 3: Product page requires user login (unauthenticated user / login modal active).
   - Step 4: Agent pauses, emits ask_user prompt asking user to log in, and waits.
   - Step 5: User logs in (login barrier cleared, "My Account" appears).
   - Step 6: Agent autonomously resumes and clicks "Add to Cart".
   - Step 7: Verifier confirms goal achieved.
2. Cross-Application Compatibility:
   - Amazon unauthenticated checkout/cart prompt.
   - Banking portal requiring OTP verification before downloading statement.
   - Generic CAPTCHA security verification barrier.
"""
import pytest
from server.app.schemas.schemas import (
    SanitizedContextPackage,
    PageContext,
    ElementMetadata,
    PrivacyReport,
    VerificationPayload
)
from server.app.planner import decide_next_action
from server.app.verifier import verify_task_completion
from server.app.task_state_machine import (
    PHASE_DISCOVER,
    PHASE_SEARCH,
    PHASE_SELECT_TARGET,
    PHASE_OPEN_TARGET,
    PHASE_AWAIT_USER_INTERVENTION,
    PHASE_GOAL_ACHIEVED,
)
from server.app.user_intervention import (
    detect_user_intervention_needed,
    verify_user_intervention_completed,
    INTERVENTION_TYPE_LOGIN,
    INTERVENTION_TYPE_OTP,
    INTERVENTION_TYPE_CAPTCHA,
)


class TestFlipkartBestSellingShirtWaitAndResume:
    """Tests the full end-to-end user scenario on Flipkart."""

    TASK = "find me the best selling shirt in flipkart and add it to my cart"

    def test_step1_homepage_searches_shirt(self):
        """On Flipkart homepage, agent identifies search input and types 'shirt'."""
        homepage_elements = [
            ElementMetadata(id="search-input", role="input", label="Search for Products, Brands and More", bbox=[200, 20, 600, 60]),
            ElementMetadata(id="search-btn", role="button", label="Search", bbox=[610, 20, 660, 60]),
            ElementMetadata(id="banner-ad", role="a", label="Summer Mega Deals 80% off", bbox=[100, 100, 1000, 300]),
        ]
        payload = SanitizedContextPackage(
            session_id="flipkart-wait-resume-01",
            step=1,
            instruction_sanitized=self.TASK,
            page=PageContext(
                url_sanitized="https://www.flipkart.com",
                title_sanitized="Online Shopping Site for Mobiles, Electronics, Furniture, Grocery, Lifestyle, Books & More. Best Offers!",
                viewport={"w": 1280, "h": 720, "dpr": 1.0}
            ),
            elements=homepage_elements,
            privacy_report=PrivacyReport(detected=0, sensitive=0, redacted=0),
            history=[]
        )
        actions, summary = decide_next_action(payload)
        first_act = actions[0]
        assert first_act.type == "type"
        assert "shirt" in first_act.value.text.lower()
        print("\n[PASS] Step 1: Agent typed 'shirt' into Flipkart search.")

    def test_step2_search_results_selects_best_seller_shirt(self):
        """On search results page, agent identifies and selects #1 Best Seller candidate."""
        search_elements = [
            ElementMetadata(
                id="shirt-cand-1",
                role="a",
                label="Men Slim Fit Casual Blue Shirt ₹649 (3.9 stars)",
                bbox=[100, 200, 300, 450],
                href="https://www.flipkart.com/blue-shirt/p/itm001"
            ),
            ElementMetadata(
                id="shirt-best-seller",
                role="a",
                label="Men Regular Fit Solid Black Casual Shirt #1 Best Seller ₹799 (4.5 stars)",
                bbox=[320, 200, 520, 450],
                href="https://www.flipkart.com/black-best-seller-shirt/p/itm002"
            ),
            ElementMetadata(
                id="jeans-cand",
                role="a",
                label="Men Slim Fit Denim Jeans ₹999",
                bbox=[540, 200, 740, 450],
                href="https://www.flipkart.com/jeans/p/itm003"
            )
        ]
        payload = SanitizedContextPackage(
            session_id="flipkart-wait-resume-01",
            step=2,
            instruction_sanitized=self.TASK,
            page=PageContext(
                url_sanitized="https://www.flipkart.com/search?q=shirt",
                title_sanitized="Shirt - Buy Shirts Online at Best Prices in India | Flipkart.com",
                viewport={"w": 1280, "h": 720, "dpr": 1.0}
            ),
            elements=search_elements,
            privacy_report=PrivacyReport(detected=0, sensitive=0, redacted=0),
            history=[{"action": "type", "target": "search-input", "value": "shirt"}]
        )
        actions, summary = decide_next_action(payload)
        open_act = actions[0]
        assert open_act.type in ("click", "open_link")
        assert "best-seller" in (open_act.url or open_act.target.element_id)
        print("\n[PASS] Step 2: Agent selected #1 Best Seller candidate.")

    def test_step3_product_page_pauses_for_login(self):
        """
        When product page requires login (unauthenticated user / login modal active),
        agent detects login requirement, pauses, and prompts user to log in.
        """
        product_elements_with_login_modal = [
            ElementMetadata(id="btn-cart", role="button", label="Add to Cart", bbox=[200, 500, 350, 550]),
            ElementMetadata(id="btn-buy", role="button", label="Buy Now", bbox=[360, 500, 510, 550]),
            # Active Flipkart Login Modal
            ElementMetadata(id="login-heading", role="text", label="Login to continue", bbox=[500, 150, 800, 200]),
            ElementMetadata(id="login-input-mobile", role="input", label="Enter Email/Mobile number", bbox=[500, 220, 800, 260]),
            ElementMetadata(id="btn-request-otp", role="button", label="Request OTP", bbox=[500, 280, 800, 320]),
        ]
        payload = SanitizedContextPackage(
            session_id="flipkart-wait-resume-01",
            step=3,
            instruction_sanitized=self.TASK,
            page=PageContext(
                url_sanitized="https://www.flipkart.com/black-best-seller-shirt/p/itm002",
                title_sanitized="Men Regular Fit Solid Black Casual Shirt - Buy Online | Flipkart.com",
                viewport={"w": 1280, "h": 720, "dpr": 1.0}
            ),
            elements=product_elements_with_login_modal,
            privacy_report=PrivacyReport(detected=0, sensitive=0, redacted=0),
            history=[{"action": "open_link", "url": "https://www.flipkart.com/black-best-seller-shirt/p/itm002"}]
        )
        actions, summary = decide_next_action(payload)
        act = actions[0]
        # Agent must yield ask_user / wait for user intervention
        assert act.type == "ask_user"
        assert "log in" in act.question.lower() or "login" in act.question.lower()
        assert "flipkart" in act.question.lower()
        saved_state = payload.verification_state["task_execution_state"]
        assert saved_state["phase"] == PHASE_AWAIT_USER_INTERVENTION
        assert saved_state["user_intervention_type"] == INTERVENTION_TYPE_LOGIN
        print(f"\n[PASS] Step 3: Agent paused and prompted user: '{act.question}'")

    def test_step4_agent_waits_while_user_logging_in(self):
        """While the user is logging in, agent maintains wait state without making illegal clicks."""
        product_elements_still_logging_in = [
            ElementMetadata(id="btn-cart", role="button", label="Add to Cart", bbox=[200, 500, 350, 550]),
            ElementMetadata(id="login-modal", role="text", label="Login to continue", bbox=[500, 150, 800, 200]),
            ElementMetadata(id="otp-input", role="input", label="Enter OTP", bbox=[500, 220, 800, 260]),
        ]
        payload = SanitizedContextPackage(
            session_id="flipkart-wait-resume-01",
            step=4,
            instruction_sanitized=self.TASK,
            page=PageContext(
                url_sanitized="https://www.flipkart.com/black-best-seller-shirt/p/itm002",
                title_sanitized="Men Regular Fit Solid Black Casual Shirt - Buy Online | Flipkart.com",
                viewport={"w": 1280, "h": 720, "dpr": 1.0}
            ),
            elements=product_elements_still_logging_in,
            privacy_report=PrivacyReport(detected=0, sensitive=0, redacted=0),
            history=[{"action": "ask_user", "question": "Please log in"}]
        )
        payload.verification_state = {
            "task_execution_state": {
                "phase": PHASE_AWAIT_USER_INTERVENTION,
                "user_intervention_required": True,
                "user_intervention_type": INTERVENTION_TYPE_LOGIN,
                "user_intervention_completed": False
            }
        }
        actions, summary = decide_next_action(payload)
        act = actions[0]
        assert act.type == "wait"
        assert "waiting for user" in summary.lower()
        print("\n[PASS] Step 4: Agent successfully waited while user entered credentials.")

    def test_step5_user_logs_in_agent_resumes_and_clicks_add_to_cart(self):
        """
        Once user finishes login (login modal dismissed, 'My Account' profile appears),
        agent automatically detects login completion and resumes by clicking 'Add to Cart'.
        """
        product_elements_logged_in = [
            ElementMetadata(id="nav-my-account", role="a", label="My Account", bbox=[900, 20, 1000, 50]),
            ElementMetadata(id="btn-add-to-cart", role="button", label="Add to Cart", bbox=[200, 500, 350, 550]),
            ElementMetadata(id="btn-buy-now", role="button", label="Buy Now", bbox=[360, 500, 510, 550]),
        ]
        payload = SanitizedContextPackage(
            session_id="flipkart-wait-resume-01",
            step=5,
            instruction_sanitized=self.TASK,
            page=PageContext(
                url_sanitized="https://www.flipkart.com/black-best-seller-shirt/p/itm002",
                title_sanitized="Men Regular Fit Solid Black Casual Shirt - Buy Online | Flipkart.com",
                viewport={"w": 1280, "h": 720, "dpr": 1.0}
            ),
            elements=product_elements_logged_in,
            privacy_report=PrivacyReport(detected=0, sensitive=0, redacted=0),
            history=[{"action": "wait", "milliseconds": 1000}]
        )
        payload.verification_state = {
            "task_execution_state": {
                "phase": PHASE_AWAIT_USER_INTERVENTION,
                "user_intervention_required": True,
                "user_intervention_type": INTERVENTION_TYPE_LOGIN,
                "user_intervention_completed": False
            }
        }
        actions, summary = decide_next_action(payload)
        act = actions[0]
        # Resumed autonomous execution: clicks Add to Cart!
        assert act.type == "click"
        assert act.target.element_id == "btn-add-to-cart"
        assert "logged in" in summary.lower() or "resumed" in summary.lower()
        print(f"\n[PASS] Step 5: Agent resumed and clicked '{act.target.element_id}': {summary}")

    def test_step6_verifier_confirms_goal_achieved(self):
        """After clicking Add to Cart, verifier confirms goal achievement upon observing cart evidence."""
        payload = VerificationPayload(
            task=self.TASK,
            current_url="https://www.flipkart.com/viewcart",
            page_title="Shopping Cart | Flipkart.com",
            action_history=[
                {"action": "click", "target": "btn-add-to-cart"},
                {"action": "wait", "milliseconds": 500}
            ],
            screen_summary={"main_headings": ["My Cart (1)", "Men Regular Fit Solid Black Casual Shirt"]},
            elements=[
                ElementMetadata(id="cart-item", role="text", label="Men Regular Fit Solid Black Casual Shirt ₹799", bbox=[100, 200, 500, 300]),
                ElementMetadata(id="cart-count", role="text", label="Cart (1)", bbox=[900, 20, 950, 50]),
            ],
            downloads=[]
        )
        v_res = verify_task_completion(payload)
        assert v_res.achieved is True
        assert v_res.goal_status == "GOAL_ACHIEVED"
        assert "added to cart" in v_res.reason.lower()
        print("\n[PASS] Step 6: Verifier confirmed GOAL_ACHIEVED after user login and cart addition.")


class TestGenericApplicationCompatibility:
    """Proves the wait & resume feature works generically across arbitrary applications."""

    def test_amazon_unauthenticated_cart_flow(self):
        """Works on Amazon: Unauthenticated cart prompt triggers wait for user."""
        amazon_elements = [
            ElementMetadata(id="add-to-cart-button", role="button", label="Add to Cart", bbox=[800, 400, 950, 440]),
            ElementMetadata(id="sign-in-prompt", role="text", label="Sign in to continue to your Amazon account", bbox=[400, 200, 800, 250]),
            ElementMetadata(id="ap_email", role="input", label="Enter mobile phone number or email", bbox=[400, 260, 800, 300]),
        ]
        intervention = detect_user_intervention_needed(
            elements=amazon_elements,
            page=PageContext(url_sanitized="https://www.amazon.in/dp/B00EXAMPLE", title_sanitized="Amazon.in: Product", viewport={"w":1280,"h":720}),
            task="find the best selling shirt on amazon and add to cart",
            requested_action="ADD_TO_CART"
        )
        assert intervention is not None
        assert intervention["type"] == INTERVENTION_TYPE_LOGIN
        assert "amazon" in intervention["prompt"].lower()
        print("\n[PASS] Generic: Successfully detected login requirement on Amazon.")

    def test_banking_portal_otp_flow(self):
        """Works on Banking: OTP verification triggers wait for user before statement download."""
        banking_elements = [
            ElementMetadata(id="otp-box", role="input", label="Enter OTP sent to registered mobile", bbox=[300, 200, 600, 250]),
            ElementMetadata(id="btn-verify", role="button", label="Verify OTP and Download Statement", bbox=[300, 270, 600, 310]),
        ]
        intervention = detect_user_intervention_needed(
            elements=banking_elements,
            page=PageContext(url_sanitized="https://bank.example.com/statements", title_sanitized="Apex Bank Secure Portal", viewport={"w":1280,"h":720}),
            task="download my bank statement",
            requested_action="DOWNLOAD"
        )
        assert intervention is not None
        assert intervention["type"] == INTERVENTION_TYPE_OTP
        assert "otp" in intervention["prompt"].lower()
        print("\n[PASS] Generic: Successfully detected OTP verification on Banking portal.")

    def test_captcha_security_barrier_flow(self):
        """Works on arbitrary site: CAPTCHA prompts user to solve puzzle and pauses agent."""
        captcha_elements = [
            ElementMetadata(id="recaptcha-anchor", role="div", label="I am not a robot (CAPTCHA)", bbox=[300, 200, 500, 280]),
            ElementMetadata(id="submit-form", role="button", label="Submit Registration", bbox=[300, 300, 500, 340]),
        ]
        intervention = detect_user_intervention_needed(
            elements=captcha_elements,
            page=PageContext(url_sanitized="https://portal.gov.in/register", title_sanitized="Portal Verification", viewport={"w":1280,"h":720}),
            task="submit registration form",
            requested_action="SUBMIT"
        )
        assert intervention is not None
        assert intervention["type"] == INTERVENTION_TYPE_CAPTCHA
        assert "captcha" in intervention["prompt"].lower()
        print("\n[PASS] Generic: Successfully detected CAPTCHA security barrier.")
