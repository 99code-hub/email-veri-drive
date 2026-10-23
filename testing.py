#!/usr/bin/env python3

import csv
import time
import random
import re
import sys
import os, glob, shutil, time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


LOGIN_URL = "https://docs.google.com/document"
CREDENTIALS_FILE = "credentials.txt"
RESULTS_FILE = "login_test_results.csv"

WAIT_SECONDS = 10

DELAY_BETWEEN_ATTEMPTS = 2

NEXT_BUTTON_TEXTS = ["Next", "Continue", "Sign in", "Log in", "Login"]


EMAIL_INPUT_SELECTOR = (By.ID, "identifierId")      # e.g. (By.NAME, "username")
PASSWORD_INPUT_SELECTOR =  (By.NAME, "Passwd")  # e.g. (By.ID, "pass")

CREATE_BUTTON_SELECTOR = (By.CSS_SELECTOR, "button.projtest-create-form-submit") # e.g. (By.ID, "create-btn") or (By.XPATH, "//button[contains(text(),'Create')]")
CONSENT_CHECKBOX_SELECTOR = (By.CSS_SELECTOR, "input.mdc-checkbox__native-control")
AGREE_BUTTON_XPATH = "//*[contains(text(), 'Agree and continue')]"
AFTER_CREATE_URL_CONTAINS = "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
AFTER_CREATE_URL_CONTAINS2 = "https://console.cloud.google.com/auth/overview"
ENABLE_BUTTON_XPATH = "//span[contains(@class,'mdc-button__label') and contains(text(),'Enable')]"
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
SUCCESS_URL_CONTAINS = "/dashboard"     # e.g. URL changes after login
SUCCESS_ELEMENT_SELECTOR = None         # e.g. (By.ID, "logout-button")


ERROR_ELEMENT_SELECTOR = None           # e.g. (By.CLASS_NAME, "error-message")
# ========================================================================
# ============ GLOBALS FOR ROTATING EMAIL BATCHES ============
add_user_batch_size = 0
all_add_users = []
global_email_index = 0

# ============ HELPER FUNCTIONS ============

def load_config_and_emails():
    """Load config (add_user batch size) and all emails from add_user.txt once at startup."""
    global add_user_batch_size, all_add_users

    # ---- Read config for add_user batch size ----
    add_user_batch_size = 0  # Default: don't add users
    try:
        cfg_file = "config"
        with open(cfg_file, "r", encoding="utf-8") as cf:
            for ln in cf:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "add_user":
                        try:
                            add_user_batch_size = int(v)
                            print(f"Config: add_user batch size = {add_user_batch_size}")
                        except ValueError:
                            print(f"Warning: Invalid add_user value '{v}', ignoring.")
    except FileNotFoundError:
        print("Config file not found.")
    except Exception as e:
        print(f"Error reading config: {e}")

    # ---- Load all emails from add_user.txt once ----
    add_user_filename = "add_user.txt"
    try:
        with open(add_user_filename, "r", encoding="utf-8") as auf:
            all_add_users = [line.strip() for line in auf if line.strip()]
        print(f"Loaded {len(all_add_users)} total emails from {add_user_filename}")
    except FileNotFoundError:
        print(f"{add_user_filename} not found, add_user feature will be skipped.")
    except Exception as e:
        print(f"Error reading {add_user_filename}: {e}")


def click_save_in_windows_dialog(filename_hint=None, timeout=12):
    """
    Wait for a Windows Save/Open download dialog and click the Save button.
    filename_hint: optional substring to help detect dialogs (e.g. ".json").
    Returns True if Save was clicked, False on timeout.
    """
    if Desktop is None:
        return False

    end_time = time.time() + timeout
    desktop = Desktop(backend="uia")
    while time.time() < end_time:
        try:
            # prioritized title matches (hint first)
            candidates = []
            if filename_hint:
                candidates.append({"title_re": f".*{re.escape(filename_hint)}.*"})
            candidates += [
                {"title_re": ".*Save As.*"},
                {"title_re": ".*File Download.*"},
                {"title_re": ".*Open.*"},
            ]

            dlg = None
            for c in candidates:
                try:
                    w = desktop.window(**c)
                    if w.exists(timeout=0.3):
                        dlg = w
                        break
                except Exception:
                    dlg = None

            # fallback: scan top-level windows for a Save button
            if dlg is None:
                for w in desktop.windows():
                    try:
                        if w.child_window(title="Save", control_type="Button").exists(timeout=0.05):
                            dlg = w
                            break
                    except Exception:
                        continue

            if dlg is not None and dlg.exists():
                try:
                    # prefer exact "Save" button, then variants
                    btn = dlg.child_window(title="Save", control_type="Button")
                    if not btn.exists():
                        for alt in ("&Save", "Save(&S)", "Save &As", "OK"):
                            try:
                                btn = dlg.child_window(title=alt, control_type="Button")
                                if btn.exists():
                                    break
                            except Exception:
                                continue
                    if btn.exists():
                        btn.click_input()
                        return True
                    # fallback: press Enter
                    dlg.type_keys("{ENTER}")
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.4)
    return False


def load_credentials(path):
    creds = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or "|" not in line:
                continue
            email, password = line.split("|", 1)
            creds.append((email.strip(), password.strip()))
    return creds


def click_next_button(driver):
    """Find and click a 'Next'/'Continue'/'Sign in' style button by visible text."""
    for text in NEXT_BUTTON_TEXTS:
        xpath = (
            f"//button[contains(normalize-space(.), '{text}')] | "
            f"//input[@type='submit' and contains(@value, '{text}')] | "
            f"//*[@role='button' and contains(normalize-space(.), '{text}')]"
        )
        elements = driver.find_elements(By.XPATH, xpath)
        for el in elements:
            try:
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    return True
            except Exception:
                continue
    return False


def find_visible_input(driver, input_type, manual_selector=None):
    """Find a visible input field. Uses manual_selector if given,
    otherwise falls back to input[type='email'/'password']."""
    try:
        if manual_selector:
            candidates = driver.find_elements(*manual_selector)
        else:
            candidates = driver.find_elements(By.CSS_SELECTOR, f"input[type='{input_type}']")
        for el in candidates:
            if el.is_displayed():
                return el
    except Exception:
        pass
    return None


def wait_for_visible_input(driver, input_type, manual_selector=None, timeout=30):
    end_time = time.time() + timeout
    while time.time() < end_time:
        el = find_visible_input(driver, input_type, manual_selector)
        if el:
            return el
        time.sleep(0.3)
    return None


def check_outcome(driver):
    """Returns 'SUCCESS', 'FAILED', or 'UNKNOWN'."""
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        try:
            if SUCCESS_URL_CONTAINS and SUCCESS_URL_CONTAINS in driver.current_url:
                return "SUCCESS"
        except Exception:
            pass
        if SUCCESS_ELEMENT_SELECTOR:
            try:
                driver.find_element(*SUCCESS_ELEMENT_SELECTOR)
                return "SUCCESS"
            except Exception:
                pass
        if ERROR_ELEMENT_SELECTOR:
            try:
                el = driver.find_element(*ERROR_ELEMENT_SELECTOR)
                if el.is_displayed():
                    return "FAILED"
            except Exception:
                pass
        time.sleep(0.3)
    return "UNKNOWN"


# ============ MAIN LOGIN FUNCTION ============

def attempt_login(driver, email, password):
    global global_email_index, add_user_batch_size, all_add_users

    def click_remind_me_later(timeout=3):
        """Look for 'Remind me later' button/span anywhere and click it. Returns True if clicked."""
        try:
            # Prefer a button ancestor whose label span matches
            try:
                btn = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Remind me later']/ancestor::button[1]")
                    )
                )
                try:
                    driver.execute_script("arguments[0].click();", btn)
                except Exception:
                    btn.click()
                print("  Clicked 'Remind me later' (button ancestor).")
                time.sleep(0.8)
                return True
            except Exception:
                # Fallback: clickable span itself
                spans = driver.find_elements(By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Remind me later']")
                if spans:
                    try:
                        driver.execute_script("arguments[0].click();", spans[0])
                        print("  Clicked 'Remind me later' (span fallback).")
                        time.sleep(0.8)
                        return True
                    except Exception:
                        try:
                            spans[0].click()
                            print("  Clicked 'Remind me later' (span.click fallback).")
                            time.sleep(0.8)
                            return True
                        except Exception:
                            print("  Found 'Remind me later' but could not click it.")
        except Exception:
            pass
        return False

    def click_do_this_later(timeout=4):
        """Click Google's 'Do this later' anchor if present. Returns True if clicked."""
        try:
            # Prefer anchor with visible text "Do this later"
            try:
                do_later = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[normalize-space(.)='Do this later' or contains(normalize-space(.),'Do this later')]"))
                )
                try:
                    driver.execute_script("arguments[0].click();", do_later)
                    print("  Clicked 'Do this later' link (direct wait).")
                except Exception:
                    try:
                        do_later.click()
                        print("  Clicked 'Do this later' link (element.click fallback).")
                    except Exception:
                        print("  Could not click 'Do this later' with element.click; attempted JS click already.")
                time.sleep(1)
                return True
            except Exception:
                # Fallback: scan anchors whose href contains 'ServiceLogin' and visible text contains 'Do this later'
                anchors = driver.find_elements(By.XPATH, "//a[contains(@href,'ServiceLogin') and (normalize-space(.)='Do this later' or contains(normalize-space(.),'Do this later'))]")
                if anchors:
                    try:
                        driver.execute_script("arguments[0].click();", anchors[0])
                        print("  Clicked 'Do this later' anchor (href fallback).")
                    except Exception:
                        try:
                            anchors[0].click()
                            print("  Clicked 'Do this later' anchor (href fallback, element.click).")
                        except Exception:
                            print("  Found 'Do this later' anchor but could not click it.")
                    time.sleep(1)
                    return True
        except Exception:
            pass
        return False

    # ---- Load website_config (optional) ----
    home_page_default = "https://github.com"
    privacy_default = "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
    terms_default = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
    domain_default = "github.com"

    website_config_file = "website_config"
    home_page_url = home_page_default
    privacy_url = privacy_default
    terms_url = terms_default
    domain_value = domain_default

    try:
        with open(website_config_file, "r", encoding="utf-8") as wf:
            for ln in wf:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    # remove optional surrounding quotes
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    if k == "home":
                        home_page_url = v or home_page_default
                    elif k == "privacy":
                        privacy_url = v or privacy_default
                    elif k == "terms":
                        terms_url = v or terms_default
                    elif k == "domain":
                        domain_value = v or domain_default
        print(f"  website_config loaded: home={home_page_url}, privacy={privacy_url}, terms={terms_url}, domain={domain_value}")
    except FileNotFoundError:
        print("  website_config not found, using defaults for home/privacy/terms/domain.")
    except Exception as e:
        print(f"  Error reading website_config: {e}. Using defaults.")

    outcome = "UNKNOWN"
    driver.get(LOGIN_URL)

    # ---- Step 1: email ----
    email_field = wait_for_visible_input(driver, "email", EMAIL_INPUT_SELECTOR)
    if not email_field:
        print("  Could not find email field.")
        return "ERROR (no email field)"
    try:
        email_field.clear()
    except Exception:
        pass
    email_field.send_keys(email)
    time.sleep(1)

    if not click_next_button(driver):
        print("  Could not find a Next button after entering email.")
        return "ERROR (no next button, step 1)"

    # ---- Step 2: password ----
    password_field = wait_for_visible_input(driver, "password", PASSWORD_INPUT_SELECTOR)
    if not password_field:
        print("  Could not find password field (maybe email step failed).")
        return "FAILED (bad email or no password step)"
    try:
        password_field.clear()
    except Exception:
        pass
    password_field.send_keys(password)
    time.sleep(3)

    if not click_next_button(driver):
        print("  Could not find a Next/Sign-in button after entering password.")
        return "ERROR (no next button, step 2)"

    # If an interstitial appears with "I understand", click it and continue
    try:
        iunder = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, "//span[@jsname='V67aGc' and normalize-space(.)='I understand']"))
        )
        driver.execute_script("arguments[0].click();", iunder)
        print("  Clicked 'I understand' (by jsname).")
        time.sleep(1)
    except Exception:
        try:
            iunder = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//span[normalize-space(.)='I understand']"))
            )
            driver.execute_script("arguments[0].click();", iunder)
            print("  Clicked 'I understand' (by text).")
            time.sleep(1)
        except Exception:
            pass

    # Handle 'Remind me later' if it appears after the interstitial
    try:
        if click_remind_me_later(timeout=3):
            print("  Handled 'Remind me later' after interstitial.")
    except Exception:
        pass

    # Handle "Do this later" anchor if it appears (e.g., Google's ServiceLogin link)
    try:
        if click_do_this_later(timeout=4):
            print("  Handled 'Do this later' link.")
    except Exception:
        pass

    outcome = check_outcome(driver)
    print(f"  Outcome: {outcome} | Current URL: {driver.current_url}")

    # Ensure logged-in landing or retry as in original script
    try:
        driver.get(LOGIN_URL)
        outcome = check_outcome(driver)
        print(f"  Retry outcome: {outcome} | Current URL: {driver.current_url}")
    except Exception:
        pass

    time.sleep(1)

    # Consent checkbox handling, if configured
    if CONSENT_CHECKBOX_SELECTOR:
        try:
            checkbox = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located(CONSENT_CHECKBOX_SELECTOR)
            )
            driver.execute_script("arguments[0].click();", checkbox)
            print("  Clicked consent checkbox.")
            time.sleep(1)

            agree_btn = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.XPATH, AGREE_BUTTON_XPATH))
            )
            driver.execute_script("arguments[0].click();", agree_btn)
            print("  Clicked Agree and continue.")
            time.sleep(3)
        except Exception:
            print("  No consent checkbox/dialog appeared, skipping.")

    # Handle 'Remind me later' if it appears after consent
    try:
        if click_remind_me_later(timeout=3):
            print("  Handled 'Remind me later' after consent checkbox (if present).")
    except Exception:
        pass

    # --- Create project flow ---
    if CREATE_BUTTON_SELECTOR:
        try:
            # Handle 'Remind me later' just before project name entry
            try:
                if click_remind_me_later(timeout=2):
                    print("  Handled 'Remind me later' just before project name entry.")
            except Exception:
                pass

            # Set Project name to "Gmail Api" BEFORE clicking Create
            try:
                proj_label_xpath = "//mat-label[normalize-space(.)='Project name']"
                try:
                    lbl = WebDriverWait(driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, proj_label_xpath))
                    )
                    driver.execute_script("arguments[0].click();", lbl)
                    time.sleep(0.2)
                except Exception:
                    pass

                proj_input_xpath = proj_label_xpath + "/ancestor::mat-form-field//input | " + proj_label_xpath + "/following::input[1]"
                proj_input = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, proj_input_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", proj_input)
                time.sleep(0.1)
                try:
                    proj_input.clear()
                except Exception:
                    driver.execute_script("arguments[0].value = '';", proj_input)
                proj_input.send_keys("Gmail Api")
                print("  Set Project name to 'Gmail Api'")
                time.sleep(0.4)
            except Exception as e:
                print(f"  Could not set Project name: {e}")

            # Click Create
            create_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                EC.presence_of_element_located(CREATE_BUTTON_SELECTOR)
            )
            driver.execute_script("arguments[0].click();", create_btn)
            print("  Clicked Create button.")
            time.sleep(10)

            # Navigate to expected site after Create
            try:
                driver.get(AFTER_CREATE_URL_CONTAINS)
                outcome = check_outcome(driver)
                print(f"  Arrived at target site: {driver.current_url}")
            except Exception:
                pass

            # Enable button, with fallback to Manage if enable fails
            try:
                try:
                    enable_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.presence_of_element_located((By.XPATH, ENABLE_BUTTON_XPATH))
                    )
                    driver.execute_script("arguments[0].click();", enable_btn)
                    print("  Clicked Enable button.")
                    time.sleep(15)
                except Exception as e_enable:
                    print(f"  Error clicking Enable button: {e_enable}")
                    print("  Trying fallback: Manage button...")
                    try:
                        manage_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                            EC.element_to_be_clickable((By.XPATH,
                                "//button[contains(@aria-label,'manage') or contains(@aria-label,'Manage') or .//span[normalize-space(.)='Manage']]"))
                        )
                        driver.execute_script("arguments[0].click();", manage_btn)
                        print("  Clicked Manage button (fallback).")
                        time.sleep(15)
                    except Exception as e_manage:
                        print(f"  Error clicking Manage button: {e_manage}")
                        print("  Continuing without clicking either button...")
                        time.sleep(5)

                # Optional follow-on navigation
                try:
                    driver.get(AFTER_CREATE_URL_CONTAINS2)
                    outcome = check_outcome(driver)
                    print(f"  Arrived at second target site: {driver.current_url}")
                except Exception:
                    print("  Could not navigate to AFTER_CREATE_URL_CONTAINS2, continuing on current page.")
            except Exception as e:
                print(f"  Error during enable/manage flow: {e}")

            # Get started, app name, select account, etc.
            try:
                try:
                    get_started_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Get started']")
                        )
                    )
                    driver.execute_script("arguments[0].click();", get_started_btn)
                    print("  Clicked Get started button.")
                    time.sleep(7)
                except Exception as e:
                    print(f"  Error clicking Get started button: {e}")

                # Fill App name from app_names.txt
                try:
                    app_names_file = "app_names.txt"
                    with open(app_names_file, "r", encoding="utf-8") as nf:
                        names = [line.strip() for line in nf if line.strip()]
                    if not names:
                        raise ValueError("No app names found")
                    app_name = random.choice(names)

                    label_xpath = "//mat-label[normalize-space(.)='App name']"
                    input_xpath = label_xpath + "/ancestor::mat-form-field//input | " + label_xpath + "/following::input[1]"
                    name_input = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, input_xpath))
                    )
                    try:
                        name_input.clear()
                    except Exception:
                        pass
                    name_input.send_keys(app_name)
                    print(f"  Filled App name: {app_name}")
                    time.sleep(1)
                except Exception as e:
                    print(f"  Error filling App name: {e}")
            except Exception:
                pass

            # Select account from cfc select
            try:
                select_div = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "div.cfc-select-value"))
                )
                driver.execute_script("arguments[0].click();", select_div)
                time.sleep(0.5)

                options = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.presence_of_all_elements_located((By.XPATH, "//mat-option"))
                )

                matched = None
                fallback = None
                for opt in options:
                    try:
                        txt = opt.text.strip()
                    except Exception:
                        txt = ""
                    if not txt:
                        continue
                    if txt == email:
                        matched = opt
                        break
                    if "@" in txt and fallback is None:
                        fallback = opt

                to_click = matched or fallback
                if to_click:
                    driver.execute_script("arguments[0].click();", to_click)
                    time.sleep(0.5)
                    print(f"  Selected option: {to_click.text.strip()}")
                else:
                    print("  No selectable mat-option found for email.")
            except Exception as e:
                print(f"  Error selecting cfc select email option: {e}")

            # Click Next
            try:
                next_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Next']/ancestor::button[1]")
                    )
                )
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(1)
                print("  Selected option and clicked Next.")
            except Exception as e:
                print(f"  Error clicking Next after selection: {e}")

            # Select 'External' radio robustly
            try:
                verified = False
                try:
                    wrapper = WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, "//mat-radio-button[.//span[normalize-space(.)='External']]"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", wrapper)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", wrapper)
                except Exception:
                    pass

                for _ in range(int(WAIT_SECONDS * 1)):
                    ok = driver.execute_script(
                        "var inputs = document.querySelectorAll(\"input[type='radio'][value='external']\");"
                        "for(var i=0;i<inputs.length;i++){ if(inputs[i].checked) return true; }"
                        "var xpath = \"//mat-radio-button[.//span[normalize-space(.)='External']]\";"
                        "var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;"
                        "if(res){ if(res.getAttribute('aria-checked')==='true' || res.getAttribute('aria-selected')==='true') return true; }"
                        "return false;"
                    )
                    if ok:
                        verified = True
                        break
                    time.sleep(0.5)

                if not verified:
                    driver.execute_script(
                        "var r=document.querySelector(\"input[type='radio'][value='external']\");"
                        "if(r){ r.click(); r.checked=true; r.dispatchEvent(new Event('change',{bubbles:true})); }"
                    )
                    time.sleep(0.5)
                    ok = driver.execute_script(
                        "var inputs = document.querySelectorAll(\"input[type='radio'][value='external']\");"
                        "for(var i=0;i<inputs.length;i++){ if(inputs[i].checked) return true; }"
                        "var xpath = \"//mat-radio-button[.//span[normalize-space(.)='External']]\";"
                        "var res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;"
                        "if(res){ if(res.getAttribute('aria-checked')==='true' || res.getAttribute('aria-selected')==='true') return true; }"
                        "return false;"
                    )
                    verified = bool(ok)

                if verified:
                    print("  Clicked and verified radio input for 'external'.")
                else:
                    print("  Clicked radio for 'external' but could not verify selection.")
            except Exception as e:
                print(f"  Error clicking external radio: {e}")

            # Next
            try:
                step_next_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Next']/ancestor::button[1]")
                    )
                )
                driver.execute_script("arguments[0].click();", step_next_btn)
                time.sleep(1)
                print("  Clicked Next after selecting external.")
            except Exception as e:
                print(f"  Error clicking Next after radio selection: {e}")

            # Fill the emails chip input and proceed
            try:
                chip_input = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[aria-label='Text field for emails']"))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", chip_input)
                time.sleep(0.2)
                try:
                    chip_input.clear()
                except Exception:
                    pass
                chip_input.send_keys(email)
                chip_input.send_keys("\n")
                time.sleep(1)

                next_btn_after_email = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Next']/ancestor::button[1]"))
                )
                driver.execute_script("arguments[0].click();", next_btn_after_email)
                time.sleep(1)
                print("  Entered email in chip input and clicked Next.")
            except Exception as e:
                print(f"  Error entering email in chip input or clicking Next: {e}")

            # Agreement checkbox, Continue, Create
            try:
                checkbox = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='checkbox'].mdc-checkbox__native-control"))
                )
                driver.execute_script("arguments[0].click();", checkbox)
                time.sleep(0.5)
                print("  Clicked agreement checkbox.")
            except Exception as e:
                print(f"  Error clicking agreement checkbox: {e}")

            try:
                continue_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Continue']/ancestor::button[1]"))
                )
                driver.execute_script("arguments[0].click();", continue_btn)
                time.sleep(1)
                print("  Clicked Continue.")
            except Exception as e:
                print(f"  Error clicking Continue: {e}")

            try:
                create_btn_final = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Create']/ancestor::button[1]"))
                )
                driver.execute_script("arguments[0].click();", create_btn_final)
                print("  Clicked Create.")
                time.sleep(10)
            except Exception as e:
                print(f"  Error clicking Create: {e}")

            # ============ BRANDING SECTION / Add domain ============
            try:
                branding_tab = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'cfc-page-displayName') and normalize-space(.)='Branding']"))
                )
                driver.execute_script("arguments[0].click();", branding_tab)
                print("  Clicked Branding tab.")
                time.sleep(3)

                # Fill home/privacy/terms fields from website_config (or defaults)
                try:
                    home_page_label_xpath = "//mat-label[normalize-space(.)='Application home page']"
                    home_page_input_xpath = home_page_label_xpath + "/ancestor::mat-form-field//input | " + home_page_label_xpath + "/following::input[1]"
                    home_page_input = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, home_page_input_xpath))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", home_page_input)
                    time.sleep(0.2)
                    try:
                        home_page_input.clear()
                    except Exception:
                        pass
                    home_page_input.send_keys(home_page_url)
                    print(f"  Filled Application home page: {home_page_url}")
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  Error filling Application home page: {e}")

                try:
                    privacy_label_xpath = "//mat-label[normalize-space(.)='Application privacy policy link']"
                    privacy_input_xpath = privacy_label_xpath + "/ancestor::mat-form-field//input | " + privacy_label_xpath + "/following::input[1]"
                    privacy_input = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, privacy_input_xpath))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", privacy_input)
                    time.sleep(0.2)
                    try:
                        privacy_input.clear()
                    except Exception:
                        pass
                    privacy_input.send_keys(privacy_url)
                    print("  Filled Application privacy policy link")
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  Error filling Application privacy policy link: {e}")

                try:
                    terms_label_xpath = "//mat-label[normalize-space(.)='Application terms of service link']"
                    terms_input_xpath = terms_label_xpath + "/ancestor::mat-form-field//input | " + terms_label_xpath + "/following::input[1]"
                    terms_input = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, terms_input_xpath))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", terms_input)
                    time.sleep(0.2)
                    try:
                        terms_input.clear()
                    except Exception:
                        pass
                    terms_input.send_keys(terms_url)
                    print("  Filled Application terms of service link")
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  Error filling Application terms of service link: {e}")

                # ---- Click Add domain ----
                try:
                    add_domain_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'cfc-form-stack-add-text') and contains(normalize-space(.),'Add domain')]"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", add_domain_btn)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", add_domain_btn)
                    print("  Clicked Add domain button.")
                    time.sleep(2)
                except Exception as e:
                    print(f"  Error clicking Add domain: {e}")

                # ---- Fill domain field (handle already focused vs not focused) ----
                try:
                    domain_input = None

                    # Primary: find by formcontrolname
                    try:
                        domain_input = WebDriverWait(driver, 3).until(
                            EC.presence_of_element_located((By.XPATH, "//input[@formcontrolname='domain'][@placeholder='example.com']"))
                        )
                        print("  Found domain input field (by formcontrolname+placeholder).")
                    except Exception:
                        pass

                    # Fallback: last input with formcontrolname
                    if domain_input is None:
                        try:
                            all_domain_inputs = driver.find_elements(By.XPATH, "//input[@formcontrolname='domain']")
                            if all_domain_inputs:
                                domain_input = all_domain_inputs[-1]
                                print("  Found domain input as last formcontrolname input.")
                        except Exception:
                            pass

                    if domain_input is not None:
                        print("  Domain input located; checking focus state...")
                        time.sleep(0.3)

                        # Check if it's already focused (after clicking Add domain)
                        try:
                            is_focused = driver.execute_script("return document.activeElement === arguments[0];", domain_input)
                        except Exception:
                            is_focused = False
                        print(f"  Domain input focused state: {is_focused}")

                        # If not focused, click the label to focus or focus the input directly
                        if not is_focused:
                            try:
                                label = driver.find_element(By.XPATH, "//cfc-form-stack-field-label[contains(., 'Authorized domain')]")
                                driver.execute_script("arguments[0].click();", label)
                                time.sleep(0.5)
                                print("  Clicked Authorized domain label to focus.")
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].focus();", domain_input)
                                    time.sleep(0.3)
                                    print("  Focused domain input via JS.")
                                except Exception:
                                    pass

                        # Now type the domain (character-by-character to be robust)
                        try:
                            domain_input.send_keys(Keys.CONTROL + "a")
                            time.sleep(0.1)
                            domain_input.send_keys(Keys.BACKSPACE)
                        except Exception:
                            pass
                        for ch in domain_value:
                            try:
                                domain_input.send_keys(ch)
                            except Exception:
                                pass
                            time.sleep(0.03)

                        # Dispatch events for reactive forms
                        try:
                            driver.execute_script("""
                                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                            """, domain_input)
                        except Exception:
                            pass
                        time.sleep(0.5)

                        # Verify value
                        try:
                            value = domain_input.get_attribute("value")
                        except Exception:
                            value = None
                        if value and domain_value in value:
                            print(f"  ✓ Domain filled successfully: {value}")
                        else:
                            print(f"  Warning: Domain value is '{value}', attempting JS fallback...")
                            try:
                                driver.execute_script(f"""
                                    arguments[0].value = '{domain_value}';
                                    arguments[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    arguments[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
                                """, domain_input)
                                time.sleep(0.4)
                                final_value = domain_input.get_attribute("value")
                                print(f"  After JS fallback, domain value: '{final_value}'")
                            except Exception:
                                print("  JS fallback failed to set domain value.")
                    else:
                        print("  ✗ Could not find domain input field after Add domain.")
                except Exception as e:
                    print(f"  Error filling domain: {e}")
                    traceback.print_exc()

                # Save branding
                try:
                    save_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Save']/ancestor::button[1]"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", save_btn)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", save_btn)
                    print("  Clicked Save button on Branding page.")
                    time.sleep(8)
                except Exception as e:
                    print(f"  Error clicking Save button on Branding: {e}")
            except Exception as e:
                print(f"  Error during Branding flow: {e}")

            # ============ ADD USERS FROM add_user.txt WITH ROTATING BATCH ============
            try:
                audience_tab = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'cfc-page-displayName') and normalize-space(.)='Audience']"))
                )
                driver.execute_script("arguments[0].click();", audience_tab)
                time.sleep(1)
                print("  Clicked Audience tab.")

                add_users_list = []

                if add_user_batch_size > 0 and all_add_users:
                    has_op = any(e.lower() == "op" for e in all_add_users)

                    if has_op:
                        add_users_list.append(email)
                        print(f"  Found 'op' in add_user.txt: adding current session email {email}")
                        remaining_batch = add_user_batch_size - 1
                        if remaining_batch > 0:
                            other_emails = [e for e in all_add_users if e.lower() != "op"]
                            emails_to_add = other_emails[global_email_index:global_email_index + remaining_batch]
                            add_users_list.extend(emails_to_add)
                            global_email_index += len(emails_to_add)
                        print(f"  Add Users: batch_size={add_user_batch_size}, op=True, other_emails_added={len(emails_to_add) if remaining_batch > 0 else 0}, global_index now={global_email_index}")
                    else:
                        remaining_emails = all_add_users[global_email_index:]
                        emails_to_add = remaining_emails[:add_user_batch_size]
                        add_users_list.extend(emails_to_add)
                        global_email_index += len(emails_to_add)
                        print(f"  No 'op' in add_user.txt: using file emails, batch_size={add_user_batch_size}, emails_to_add={len(emails_to_add)}, global_index now={global_email_index}")
                    print(f"  Add Users list (before dedup): {add_users_list}")
                else:
                    if add_user_batch_size == 0:
                        print("  Add Users feature disabled (add_user_batch_size=0).")
                    elif not all_add_users:
                        print("  No emails loaded from add_user.txt.")

                # Deduplicate
                if add_users_list:
                    seen = set()
                    unique_users = []
                    for u in add_users_list:
                        ul = u.lower()
                        if ul not in seen:
                            seen.add(ul)
                            unique_users.append(u)
                    add_users_list = unique_users
                    print(f"  Loaded {len(add_users_list)} unique user(s) (after dedup): {add_users_list}")

                if add_users_list:
                    try:
                        add_users_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Add users']"))
                        )
                        driver.execute_script("arguments[0].click();", add_users_btn)
                        print("  Clicked 'Add users' button.")
                        time.sleep(2)

                        email_chip_input = WebDriverWait(driver, WAIT_SECONDS).until(
                            EC.element_to_be_clickable((By.XPATH, "//input[@aria-label='Text field for emails']"))
                        )
                        driver.execute_script("arguments[0].scrollIntoView(true);", email_chip_input)
                        time.sleep(0.2)

                        for user_email in add_users_list:
                            email_chip_input.send_keys(user_email)
                            email_chip_input.send_keys("\n")
                            time.sleep(0.3)
                            print(f"    Added email: {user_email}")

                        time.sleep(1)
                        print("  All emails added to chip input.")

                        # Save button robust click
                        try:
                            save_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                                EC.presence_of_element_located((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Save']/ancestor::button[1]"))
                            )
                            driver.execute_script("arguments[0].scrollIntoView(true);", save_btn)
                            time.sleep(0.5)
                            try:
                                save_btn.click()
                                print("  Clicked Save button (direct click).")
                            except Exception:
                                driver.execute_script("arguments[0].click();", save_btn)
                                print("  Clicked Save button (JS click).")
                            time.sleep(2)

                            try:
                                save_btn_after = WebDriverWait(driver, 2).until(
                                    EC.presence_of_element_located((By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Save']/ancestor::button[1]"))
                                )
                                print("  Save button still visible after click; trying again...")
                                driver.execute_script("arguments[0].click();", save_btn_after)
                                time.sleep(2)
                            except Exception:
                                print("  Save button disappeared after click (click successful).")
                            time.sleep(3)
                            print("  Completed Add Users flow.")
                        except Exception as e:
                            print(f"  Error clicking Save button: {e}")
                    except Exception as e:
                        print(f"  Error clicking 'Add users' button or filling chips: {e}")
            except Exception as e:
                print(f"  Error during Add Users flow: {e}")

            # ============ AUDIENCE: Publish/Internal Logic ============
            try:
                status_value = None
                try:
                    cfg_file = "config"
                    with open(cfg_file, "r", encoding="utf-8") as cf:
                        for ln in cf:
                            ln = ln.strip()
                            if not ln or ln.startswith("#"):
                                continue
                            if "=" in ln:
                                k, v = ln.split("=", 1)
                                if k.strip().lower() == "status":
                                    status_value = v.strip().lower()
                                    break
                except FileNotFoundError:
                    print("  Config file not found; defaulting to publish action.")

                # Decide action: 'internal' or 'publish'
                action = "publish"
                if status_value == "internal":
                    action = "internal"
                elif status_value == "testing":
                    action = "publish"
                else:
                    action = "publish"

                if action == "publish":
                    btn_xpath = "//span[normalize-space(.)='Publish app']/ancestor::button[1]"
                else:
                    btn_xpath = "//span[normalize-space(.)='Make internal']/ancestor::button[1]"

                try:
                    action_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, btn_xpath))
                    )
                    driver.execute_script("arguments[0].click();", action_btn)
                    print(f"  Clicked {'Publish app' if action=='publish' else 'Make internal'}.")
                    time.sleep(1)
                except Exception as e:
                    print(f"  Could not click target action button: {e}")

                try:
                    confirm_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[normalize-space(.)='Confirm']/ancestor::button[1]"))
                    )
                    driver.execute_script("arguments[0].click();", confirm_btn)
                    print("  Clicked Confirm.")
                    time.sleep(10)
                except Exception as e:
                    print(f"  Could not click Confirm button: {e}")
            except Exception as e:
                print(f"  Error during Audience publish/internal flow: {e}")

            # ============ CLIENTS & DOWNLOAD ============
            try:
                clients_tab = WebDriverWait(driver, WAIT_SECONDS).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'cfc-page-displayName') and normalize-space(.)='Clients']"))
                )
                driver.execute_script("arguments[0].click();", clients_tab)
                time.sleep(2)

                clicked_anchor = False
                for anchor_id in ("_2rif_action-bar-create-client-button", "_0rif_action-bar-create-client-button", "_4rif_action-bar-create-client-button"):
                    try:
                        anchor = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.ID, anchor_id))
                        )
                        driver.execute_script("arguments[0].click();", anchor)
                        clicked_anchor = True
                        time.sleep(2)
                        break
                    except Exception:
                        continue
                if not clicked_anchor:
                    try:
                        create_client_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[contains(normalize-space(.),'Create client')]/ancestor::button[1]"))
                        )
                        driver.execute_script("arguments[0].click();", create_client_btn)
                        time.sleep(2)
                    except Exception as e:
                        print(f"  Fallback Create client click failed: {e}")

                try:
                    sel = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.cfc-select-value"))
                    )
                    driver.execute_script("arguments[0].click();", sel)
                    time.sleep(1)

                    desktop_option = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(@class,'mdc-list-item__primary-text') and normalize-space(.)='Desktop app']"))
                    )
                    driver.execute_script("arguments[0].click();", desktop_option)
                    time.sleep(3)
                except Exception as e:
                    print(f"  Could not open/select 'Desktop app': {e}")

                try:
                    app_names_file = "app_names.txt"
                    with open(app_names_file, "r", encoding="utf-8") as nf:
                        names2 = [line.strip() for line in nf if line.strip()]
                    if not names2:
                        raise ValueError("No app names found")
                    client_name = random.choice(names2)

                    label_xpath = "//mat-label[normalize-space(.)='Name']"
                    try:
                        label_el = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.XPATH, label_xpath))
                        )
                        driver.execute_script("arguments[0].click();", label_el)
                        time.sleep(0.3)
                    except Exception:
                        pass

                    input_xpath = label_xpath + "/ancestor::mat-form-field//input | " + label_xpath + "/following::input[1]"
                    name_input = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, input_xpath))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", name_input)
                    time.sleep(0.2)
                    try:
                        name_input.clear()
                    except Exception:
                        driver.execute_script("arguments[0].value = '';", name_input)
                    name_input.send_keys(client_name)
                    print(f"  Filled client Name: {client_name}")
                    time.sleep(1)
                except Exception as e:
                    print(f"  Error setting client Name via label/input XPath: {e}")

                try:
                    client_create_btn = WebDriverWait(driver, WAIT_SECONDS).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(normalize-space(.),'Create')]/ancestor::button[1]"))
                    )
                    driver.execute_script("arguments[0].click();", client_create_btn)
                    print("  Clicked Create client.")
                    time.sleep(3)
                except Exception as e:
                    print(f"  Error clicking Create client button: {e}")

                # snapshot existing .json files
                download_dir = DOWNLOAD_DIR if 'DOWNLOAD_DIR' in globals() else os.getcwd()
                if not os.path.isdir(download_dir):
                    download_dir = os.getcwd()
                existing = set(glob.glob(os.path.join(download_dir, "*.json")))

                download_trigger_xpath = (
                    "//span[contains(normalize-space(.),'Download JSON')]/ancestor::button[1] | "
                    "//span[contains(normalize-space(.),'Download JSON')]/ancestor::a[1] | "
                    "//span[contains(normalize-space(.),'Download')]/ancestor::button[1] | "
                    "//span[contains(normalize-space(.),'Download')]/ancestor::a[1]"
                )
                try:
                    dl_el = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, download_trigger_xpath))
                    )
                    driver.execute_script("arguments[0].click();", dl_el)
                    print("  Clicked Download JSON trigger.")
                except Exception:
                    print("  Download trigger not found/clickable; skipping click and still waiting for download.")

                # Fallback: click Save in Windows dialog if it appears
                try:
                    clicked_save = click_save_in_windows_dialog(filename_hint=".json", timeout=12)
                    if clicked_save:
                        print("  Clicked Save in Windows dialog.")
                    else:
                        print("  No Save dialog detected (or timed out).")
                except Exception as e:
                    print(f"  Error while interacting with Windows Save dialog: {e}")

                timeout = max(20, WAIT_SECONDS * 5)
                end_time = time.time() + timeout
                downloaded = None
                while time.time() < end_time:
                    candidates = [p for p in glob.glob(os.path.join(download_dir, "*.json")) if p not in existing]
                    if candidates:
                        candidates.sort(key=os.path.getmtime)
                        candidate = candidates[-1]
                        if not candidate.endswith(".crdownload") and os.path.getsize(candidate) > 0:
                            stable = False
                            prev_size = -1
                            for _ in range(6):
                                size = os.path.getsize(candidate)
                                if size == prev_size and size > 0:
                                    stable = True
                                    break
                                prev_size = size
                                time.sleep(0.5)
                            if stable:
                                downloaded = candidate
                                break
                    time.sleep(0.5)

                if downloaded:
                    safe_email = email.replace(os.path.sep, "_").replace(":", "_")
                    dest = os.path.join(download_dir, f"{safe_email}.json")
                    if os.path.exists(dest):
                        base, ext = os.path.splitext(dest)
                        i = 1
                        while os.path.exists(f"{base}_{i}{ext}"):
                            i += 1
                        dest = f"{base}_{i}{ext}"
                    try:
                        shutil.move(downloaded, dest)
                        print(f"  Renamed downloaded JSON to: {dest}")
                        outcome = "SUCCESS"
                        print(f"  JSON download detected and renamed; marking outcome: {outcome} for {email}")
                        return outcome
                    except Exception as e:
                        print(f"  Could not move/rename downloaded file: {e} (source: {downloaded})")
                else:
                    print("  No new JSON download detected within timeout.")
            except Exception as e:
                print(f"  Error during Clients flow: {e}")

        except Exception as e:
            print(f"  Error clicking Create button: {e}")

    return outcome

# ============ MAIN FUNCTION ============

def main():
    load_config_and_emails()  # Load config and emails once at startup

    creds = load_credentials(CREDENTIALS_FILE)
    print(f"Loaded {len(creds)} credential pairs from {CREDENTIALS_FILE}")

    print("Launching Chrome ...")

    # ensure Downloads folder exists (uses DOWNLOAD_DIR from your config)
    download_dir = DOWNLOAD_DIR if 'DOWNLOAD_DIR' in globals() else os.getcwd()
    os.makedirs(download_dir, exist_ok=True)

    chrome_options = ChromeOptions()
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--guest")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    print("Chrome options prepared. Starting login attempts ...")
    results = []

    try:
        driver = None
        for i, (email, password) in enumerate(creds, 1):
            print(f"[{i}/{len(creds)}] Testing {email} ...")

            # Start a fresh Chrome instance for this attempt
            try:
                driver = webdriver.Chrome(options=chrome_options)
                # hide navigator.webdriver if possible
                try:
                    driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                    )
                except Exception:
                    pass

                # enable downloads via CDP for this driver (must be after driver created)
                try:
                    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                        "behavior": "allow",
                        "downloadPath": download_dir
                    })
                except Exception as e:
                    print(f"  Warning: Page.setDownloadBehavior failed: {e}")

                # run the login flow
                try:
                    outcome = attempt_login(driver, email, password)
                except Exception as e:
                    print(f"  attempt_login raised an exception for {email}: {e}")
                    traceback.print_exc()
                    outcome = "ERROR"

            except Exception as e:
                print(f"  Could not start Chrome for {email}: {e}")
                outcome = "ERROR"

            finally:
                # ensure this browser instance is closed before the next iteration
                try:
                    if driver is not None:
                        driver.quit()
                except Exception:
                    pass
                driver = None

            print(f"  -> {outcome}")
            results.append((email, outcome))

            time.sleep(DELAY_BETWEEN_ATTEMPTS)
    finally:
        # extra safety: ensure no stray browser processes remain
        try:
            if 'driver' in locals() and driver is not None:
                driver.quit()
        except Exception:
            pass

    with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["email", "result"])
        writer.writerows(results)

    print(f"\nDone. Results saved to {RESULTS_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n" + "=" * 60)
        print("SCRIPT CRASHED WITH AN ERROR:")
        traceback.print_exc()
        print("=" * 60)
        print(f"\nError summary: {e}")
    finally:
        try:
            input("\nPress Enter to close this window...")
        except Exception:
            pass
