"""
Generic User Intervention & Human-in-the-Loop Wait-and-Resume System.
SIH PS 26171 / Visiionary Autonomous Browser Agent.

Handles scenarios where the autonomous agent must pause and yield control to the user:
1. Authentication / Login required (e.g. unauthenticated user on shopping/banking portal).
2. Sensitive personal detail entry (e.g. OTP, 2FA token, personal PIN, CAPTCHA).
3. Ambiguous user choice or mandatory input (e.g. address confirmation, size/variant).

Zero site-specific coupling: Works generically across Flipkart, Amazon, banking portals,
and arbitrary web applications.
"""
from __future__ import annotations

import re
from typing import Dict, Any, List, Optional, Tuple
from server.app.schemas.schemas import ElementMetadata, PageContext


INTERVENTION_TYPE_LOGIN = "LOGIN"
INTERVENTION_TYPE_OTP = "OTP"
INTERVENTION_TYPE_DETAILS = "DETAILS_INPUT"
INTERVENTION_TYPE_SELECTION = "SELECTION"
INTERVENTION_TYPE_CAPTCHA = "CAPTCHA"

# Generic keywords indicating user authentication is required
_AUTH_TRIGGER_KEYWORDS = [
    "login to continue", "log in to continue", "sign in to continue",
    "please login", "please log in", "please sign in",
    "login to add", "log in to add", "sign in to add",
    "login to buy", "log in to buy", "sign in to buy",
    "login / signup", "login/signup", "login or signup",
    "enter email/mobile number", "enter mobile number",
    "request otp", "send otp", "verify otp"
]

# Generic keywords indicating a user is currently logged in / authenticated
_LOGGED_IN_INDICATORS = [
    "my account", "my profile", "account & lists", "logout", "log out",
    "sign out", "signout", "hello,", "hi,", "welcome,", "profile", "orders",
    "my orders", "wallet", "user avatar"
]

# Generic keywords indicating an unauthenticated login button
_LOGIN_BUTTON_LABELS = [
    "login", "log in", "sign in", "signin", "login / register"
]


def is_user_logged_in(elements: List[ElementMetadata], page_text: str = "") -> bool:
    """
    Evaluates whether the user appears authenticated in the current browser DOM.
    Returns True if logged-in indicators are present and no prominent login wall is active.
    """
    text_lower = page_text.lower() if page_text else ""
    element_labels = [el.label.strip().lower() for el in elements if el.label]
    combined_labels = " ".join(element_labels)

    # 1. Check for logged-in indicators
    has_profile = any(k in combined_labels or k in text_lower for k in _LOGGED_IN_INDICATORS)

    # 2. Check for active login prompt / modal
    has_login_modal = any(
        k in combined_labels or k in text_lower
        for k in ("enter email/mobile number", "request otp", "login to continue", "please log in")
    )

    # If login modal is active, user is definitely NOT logged in yet
    if has_login_modal:
        return False

    return has_profile


def detect_user_intervention_needed(
    elements: List[ElementMetadata],
    page: Optional[PageContext] = None,
    task: str = "",
    requested_action: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    task_state: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Authoritative generic detector for required human intervention.
    Returns intervention details dict if the agent must wait for the user, else None.
    """
    t_lower = (task or "").lower()
    page_title = (page.title_sanitized if page else "").lower()
    page_url = (page.url_sanitized if page else "").lower()

    element_labels = [el.label.strip().lower() for el in elements if el.label]
    element_ids = [el.id.strip().lower() for el in elements if el.id]
    all_dom_text = " ".join(element_labels + element_ids) + " " + page_title

    # -------------------------------------------------------------------------
    # 1. Check for CAPTCHA
    # -------------------------------------------------------------------------
    captcha_elements = [
        el for el in elements
        if any(k in el.label.lower() + el.id.lower() for k in ("captcha", "recaptcha", "hcaptcha", "robot check", "solve puzzle"))
    ]
    if captcha_elements or "captcha" in page_title:
        return {
            "type": INTERVENTION_TYPE_CAPTCHA,
            "reason": "Security verification / CAPTCHA detected on page.",
            "prompt": "Security verification / CAPTCHA detected. Please solve the CAPTCHA in the browser. I will wait and automatically resume once completed.",
            "wait_for": "CAPTCHA_SOLVED",
            "element_id": captcha_elements[0].id if captcha_elements else None
        }

    # -------------------------------------------------------------------------
    # 2. Check for OTP / 2FA verification prompt
    # -------------------------------------------------------------------------
    otp_elements = [
        el for el in elements
        if any(k in el.label.lower() + el.id.lower() for k in ("otp", "one-time password", "verification code", "enter otp", "2fa code"))
        and el.role in ("input", "textarea")
    ]
    if otp_elements or any(k in all_dom_text for k in ("enter otp", "enter verification code", "we have sent an otp")):
        return {
            "type": INTERVENTION_TYPE_OTP,
            "reason": "One-Time Password (OTP) / 2FA verification required.",
            "prompt": "OTP verification required. Please enter your OTP in the browser. I will wait for you and automatically continue the task once verified.",
            "wait_for": "OTP_VERIFIED",
            "element_id": otp_elements[0].id if otp_elements else None
        }

    # -------------------------------------------------------------------------
    # 3. Check for Login / Authentication Requirement
    # Applicable when user wants downstream actions like Add to Cart, Buy, Checkout, Download
    # -------------------------------------------------------------------------
    is_downstream_action = (
        requested_action in ("ADD_TO_CART", "BUY_NOW", "CHECKOUT", "DOWNLOAD", "SUBMIT") or
        any(k in t_lower for k in ("add to cart", "cart", "buy", "purchase", "checkout", "download statement"))
    )

    # A. Active Login Modal / Barrier is currently on screen
    login_modal_active = any(k in all_dom_text for k in _AUTH_TRIGGER_KEYWORDS)
    password_fields = [el for el in elements if el.role == "password" or "password" in el.id.lower() or "password" in el.label.lower()]
    login_inputs = [el for el in elements if el.role == "input" and any(k in el.label.lower() + el.id.lower() for k in ("mobile", "email", "username", "phone"))]

    has_active_login_form = (
        login_modal_active or
        (password_fields and any(k in all_dom_text for k in ("sign in", "log in", "login"))) or
        ("login" in page_title and (password_fields or login_inputs))
    )

    # B. Site requires login prior to cart/action, but user is unauthenticated
    # Detected by presence of prominent header "Login" button and absence of logged-in profile
    header_login_btns = [
        el for el in elements
        if el.role in ("button", "a") and el.label.strip().lower() in _LOGIN_BUTTON_LABELS
    ]
    unauthenticated_state = bool(header_login_btns) and not is_user_logged_in(elements, all_dom_text)

    # If an active login form is present, or if downstream action is requested while unauthenticated
    if has_active_login_form or (is_downstream_action and unauthenticated_state and login_modal_active):
        app_name = "the website"
        if "flipkart" in t_lower or "flipkart" in page_url:
            app_name = "Flipkart"
        elif "amazon" in t_lower or "amazon" in page_url:
            app_name = "Amazon"
        elif "myntra" in t_lower or "myntra" in page_url:
            app_name = "Myntra"

        return {
            "type": INTERVENTION_TYPE_LOGIN,
            "reason": f"User is not logged in on {app_name}. Authentication is required to proceed with this action.",
            "prompt": f"Please log in to your {app_name} account. I will wait for you to complete your login, and once you are logged in, I will automatically resume and complete adding the item to your cart.",
            "wait_for": "LOGIN_SUCCESS",
            "app_name": app_name,
            "element_id": header_login_btns[0].id if header_login_btns else (password_fields[0].id if password_fields else None)
        }

    return None


def verify_user_intervention_completed(
    elements: List[ElementMetadata],
    page: Optional[PageContext] = None,
    intervention_type: str = INTERVENTION_TYPE_LOGIN,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str]:
    """
    Evaluates whether the pending user intervention has been satisfied.
    Returns (is_completed, reason).
    """
    page_title = (page.title_sanitized if page else "").lower()
    page_url = (page.url_sanitized if page else "").lower()

    element_labels = [el.label.strip().lower() for el in elements if el.label]
    element_ids = [el.id.strip().lower() for el in elements if el.id]
    all_dom_text = " ".join(element_labels + element_ids) + " " + page_title

    # Check for explicit user completion flag in history / context
    if history:
        for h in reversed(history):
            if isinstance(h, dict):
                if h.get("user_intervention_completed") or h.get("user_logged_in"):
                    return True, "User explicitly confirmed completion."

    if intervention_type == INTERVENTION_TYPE_LOGIN:
        # Check if login modal has disappeared
        has_login_modal = any(k in all_dom_text for k in ("enter email/mobile number", "request otp", "login to continue"))
        # Check if logged in indicators appeared
        logged_in = is_user_logged_in(elements, all_dom_text)

        if logged_in and not has_login_modal:
            return True, "User login verified: Account profile element detected and login barrier cleared."

        # If login modal is gone and page returned to product/target page
        is_target_page = any(k in page_url for k in ("/p/", "/dp/", "/product/", "/item/"))
        if not has_login_modal and is_target_page and not any(el.label.strip().lower() == "login" for el in elements if el.role == "button"):
            return True, "User login verified: Returned to target product page with authentication modal cleared."

        return False, "Still awaiting user login completion."

    elif intervention_type in (INTERVENTION_TYPE_OTP, INTERVENTION_TYPE_CAPTCHA):
        # Verification succeeds when the input fields and prompts are no longer in DOM
        has_active_barrier = any(k in all_dom_text for k in ("enter otp", "verification code", "captcha", "recaptcha"))
        if not has_active_barrier:
            return True, f"{intervention_type} verified: Security barrier cleared from page."
        return False, f"Still awaiting {intervention_type} completion."

    return False, "Awaiting user completion."
