#!/usr/bin/env python3

import csv
import time
import random
import re
import sys
import os
import glob
import shutil
import traceback

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False

LOGIN_URL = "https://docs.google.com/document"
CREDENTIALS_FILE = "credentials.txt"
RESULTS_FILE = "login_test_results.csv"

WAIT_SECONDS = 10
DELAY_BETWEEN_ATTEMPTS = 2
NEXT_BUTTON_TEXTS = ["Next", "Continue", "Sign in", "Log in", "Login"]

EMAIL_INPUT_SELECTOR = (By.ID, "identifierId")
PASSWORD_INPUT_SELECTOR = (By.NAME, "Passwd")

CREATE_BUTTON_SELECTOR = (By.CSS_SELECTOR, "button.projtest-create-form-submit")
CONSENT_CHECKBOX_SELECTOR = (By.CSS_SELECTOR, "input.mdc-checkbox__native-control")
AGREE_BUTTON_XPATH = "//*[contains(text(), 'Agree and continue')]"
AFTER_CREATE_URL_CONTAINS = "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
AFTER_CREATE_URL_CONTAINS2 = "https://console.cloud.google.com/auth/overview"
ENABLE_BUTTON_XPATH = "//span[contains(@class,'mdc-button__label') and contains(text(),'Enable')]"
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
SUCCESS_URL_CONTAINS = "/dashboard"
SUCCESS_ELEMENT_SELECTOR = None
ERROR_ELEMENT_SELECTOR = None

TEXT_FILE_PATH = "Text File.txt"
INPUT_FALLBACK_PATH = "input.txt"
CONF_PATH = "conf"
VALID_OUTPUT_PATH = "valid.txt"
ERROR_OUTPUT_PATH = "error.txt"

add_user_batch_size = 0
all_add_users = []
global_email_index = 0


# ---------------------------------------------------------------------------
# Config / file helpers
# ---------------------------------------------------------------------------

def load_config_and_emails():
    global add_user_batch_size, all_add_users
    add_user_batch_size = 0
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

    add_user_filename = "add_user.txt"
    try:
        with open(add_user_filename, "r", encoding="utf-8") as auf:
            all_add_users = [line.strip() for line in auf if line.strip()]
        print(f"Loaded {len(all_add_users)} total emails from {add_user_filename}")
    except FileNotFoundError:
        print(f"{add_user_filename} not found, add_user feature will be skipped.")
    except Exception as e:
        print(f"Error reading {add_user_filename}: {e}")


def get_email_input_path():
    if os.path.exists(TEXT_FILE_PATH):
        return TEXT_FILE_PATH
    if os.path.exists(INPUT_FALLBACK_PATH):
        return INPUT_FALLBACK_PATH
    return TEXT_FILE_PATH


def read_limit_from_conf(conf_path=CONF_PATH):
    default_limit = 3000
    try:
        if not os.path.exists(conf_path):
            return default_limit
        for line in open(conf_path, "r", encoding="utf-8", errors="ignore"):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.lower().startswith("limit="):
                try:
                    val = int(s.split("=", 1)[1].strip())
                    if val > 0:
                        return val
                except ValueError:
                    pass
    except Exception:
        pass
    return default_limit


def normalize_line(line):
    return " ".join((line or "").split())


def write_unique_lines(path, values):
    if not values:
        return
    existing = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            existing = {ln.strip() for ln in f if ln.strip()}
    new_values = [v for v in values if v not in existing]
    if not new_values:
        return
    with open(path, "a", encoding="utf-8") as f:
        for v in new_values:
            f.write(v + "\n")


def parse_email_lines(lines):
    """Fallback parser if UI parsing fails."""
    valid, errors = [], []
    seen_valid, seen_error = set(), set()
    
    for raw in lines:
        line = normalize_line(raw)
        if not line:
            continue
            
        if "No Google account found" in line:
            if line not in seen_error:
                errors.append(line)
                seen_error.add(line)
            continue
            
        emails = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", line)
        if not emails:
            continue
            
        selected = emails[0]
        if selected and selected not in seen_valid:
            valid.append(selected)
            seen_valid.add(selected)
            
    return valid, errors


# ---------------------------------------------------------------------------
# UI Validation Parsing
# ---------------------------------------------------------------------------

def get_chips_from_ui(driver):
    """
    Reads the Google Drive Share dialog to see which emails succeeded and failed.
    Valid emails have standard chips.
    Invalid emails have chips where aria-label contains "No Google account found".
    """
    valid_list = []
    error_list = []
    
    try:
        # Find the dialog to scope our search
        dialog = wait_for_share_modal(driver, timeout=3)
        root = dialog if dialog else driver
        
        # Google Drive uses jsname="Njthtb" for the removal button on the chips
        chips = root.find_elements(By.XPATH, ".//span[@jsname='Njthtb'] | .//div[@jsname='Njthtb']")
        
        for chip in chips:
            try:
                aria_label = chip.get_attribute("aria-label") or ""
                
                # Extract the email text from the inner span
                email_spans = chip.find_elements(By.XPATH, ".//span[@jsname='V67aGc']")
                email = email_spans[0].text.strip() if email_spans else ""
                
                # Fallback to regex on aria-label if span not found
                if not email:
                    match = re.search(r"Remove (.*?)(No Google account found|$)", aria_label)
                    if match:
                        email = match.group(1).strip()
                
                if not email:
                    continue

                if "No Google account found" in aria_label:
                    error_list.append(email)
                else:
                    valid_list.append(email)
            except Exception:
                pass
                
    except Exception as e:
        print(f"  [ui-parse] Failed to read chips: {e}")
        
    return valid_list, error_list


# ---------------------------------------------------------------------------
# Share dialog discovery
# ---------------------------------------------------------------------------

def wait_for_share_modal(driver, timeout=20):
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            for d in driver.find_elements(By.XPATH, "//div[@role='dialog']"):
                try:
                    if d.is_displayed():
                        return d
                except Exception:
                    pass
        except Exception:
            pass
        try:
            h = driver.find_element(By.XPATH, "//*[contains(text(), 'Share \"Resume\"') or contains(@aria-label, 'Share')]")
            if h.is_displayed():
                return h
        except Exception:
            pass
        time.sleep(0.3)
    return None


def find_share_people_input(driver, timeout=20):
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, "input[jsname='YPqjbf'], input[aria-label='Add people, groups, spaces, and calendar events']")
            for el in els:
                try:
                    if el.is_displayed():
                        return el
                except Exception:
                    pass
        except Exception:
            pass

        try:
            el = driver.execute_script("""
                function findInput(root) {
                    if (!root) return null;
                    const inputs = root.querySelectorAll("input[jsname='YPqjbf'], input[aria-label='Add people, groups, spaces, and calendar events']");
                    for (let i = 0; i < inputs.length; i++) {
                        const rect = inputs[i].getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return inputs[i];
                        }
                    }
                    const all = root.querySelectorAll('*');
                    for (let i = 0; i < all.length; i++) {
                        if (all[i].shadowRoot) {
                            const found = findInput(all[i].shadowRoot);
                            if (found) return found;
                        }
                    }
                    return null;
                }
                return findInput(document);
            """)
            if el:
                return el
        except Exception:
            pass

        try:
            is_iframe = driver.execute_script("return window.self !== window.top;")
            if not is_iframe:
                for frame in driver.find_elements(By.TAG_NAME, "iframe"):
                    try:
                        driver.switch_to.frame(frame)
                        els = driver.find_elements(By.CSS_SELECTOR, "input[jsname='YPqjbf'], input[aria-label='Add people, groups, spaces, and calendar events']")
                        for el in els:
                            if el.is_displayed():
                                print("  [find_share] Found input in iframe (Selenium).")
                                return el
                        
                        el = driver.execute_script("""
                            function findInput(root) {
                                if (!root) return null;
                                const inputs = root.querySelectorAll("input[jsname='YPqjbf'], input[aria-label='Add people, groups, spaces, and calendar events']");
                                for (let i = 0; i < inputs.length; i++) {
                                    const rect = inputs[i].getBoundingClientRect();
                                    if (rect.width > 0 && rect.height > 0) {
                                        return inputs[i];
                                    }
                                }
                                const all = root.querySelectorAll('*');
                                for (let i = 0; i < all.length; i++) {
                                    if (all[i].shadowRoot) {
                                        const found = findInput(all[i].shadowRoot);
                                        if (found) return found;
                                    }
                                }
                                return null;
                            }
                            return findInput(document);
                        """)
                        if el:
                            print("  [find_share] Found input in iframe (JS).")
                            return el
                    except Exception:
                        pass
                    
                    driver.switch_to.default_content()
        except Exception:
            pass

        time.sleep(0.5)

    print("  [find_share] Timeout waiting for share input to appear.")
    return None


# ---------------------------------------------------------------------------
# Share-dialog paste
# ---------------------------------------------------------------------------

def describe_input(driver, input_field):
    try:
        return driver.execute_script("""
            const el = arguments[0];
            const rect = el.getBoundingClientRect();
            return {
                tag: el.tagName, type: el.type,
                jsname: el.getAttribute('jsname'),
                ariaLabel: el.getAttribute('aria-label'),
                ariaExpanded: el.getAttribute('aria-expanded'),
                disabled: el.disabled, readOnly: el.readOnly,
                visible: rect.width > 0 && rect.height > 0,
                value: el.value,
                focused: document.activeElement === el,
            };
        """, input_field)
    except Exception:
        return None


def _input_value(driver, input_field):
    try:
        return input_field.get_attribute("value")
    except Exception:
        return None


def _aria_expanded(driver, input_field):
    try:
        return input_field.get_attribute("aria-expanded")
    except Exception:
        return None


def _clear_share_input(driver, input_field):
    try:
        driver.execute_script("""
            const el = arguments[0];
            el.focus();
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
        """, input_field)
    except Exception:
        pass


def _insert_email_atomic(driver, input_field, email):
    try:
        actions = ActionChains(driver)
        actions.move_to_element(input_field).click()
        actions.pause(0.3)
        actions.send_keys(email)
        actions.pause(0.2)
        actions.perform()
        time.sleep(0.4)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [atomic] failed: {e}")
    return False


def _insert_email_send_keys(driver, input_field, email):
    try:
        input_field.click()
        time.sleep(0.1)
        input_field.send_keys(email)
        time.sleep(0.4)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [send_keys] failed: {e}")
    return False


def _insert_email_cdp_keys(driver, input_field, email):
    try:
        driver.execute_script("arguments[0].focus();", input_field)
        time.sleep(0.2)
        for char in email:
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                "type": "char",
                "text": char
            })
            time.sleep(0.02)
        time.sleep(0.4)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [cdp-keys] failed: {e}")
    return False


def _insert_email_exec_command(driver, input_field, email):
    try:
        driver.execute_script("""
            const el = arguments[0];
            const text = arguments[1];
            el.focus();
            document.execCommand('insertText', false, text);
        """, input_field, email)
        time.sleep(0.5)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [execCommand] failed: {e}")
    return False


def _insert_email_paste_event(driver, input_field, email):
    try:
        driver.execute_script("""
            const el = arguments[0];
            const text = arguments[1];
            el.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const pasteEvent = new ClipboardEvent('paste', {
                bubbles: true,
                cancelable: true,
                clipboardData: dt
            });
            el.dispatchEvent(pasteEvent);
        """, input_field, email)
        time.sleep(0.5)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [paste-event] failed: {e}")
    return False


def _insert_email_clipboard(driver, input_field, email):
    if not PYPERCLIP_AVAILABLE:
        return False
    try:
        pyperclip.copy(email)
        ActionChains(driver).move_to_element(input_field).click().perform()
        time.sleep(0.1)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(0.5)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [clipboard] failed: {e}")
    return False


def _insert_email_js_react(driver, input_field, email):
    try:
        driver.execute_script("""
            const el = arguments[0], text = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, text);
            el.dispatchEvent(new InputEvent('beforeinput', {
                bubbles: true, cancelable: true, inputType: 'insertText', data: text
            }));
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true, inputType: 'insertText', data: text
            }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, input_field, email)
        time.sleep(0.5)
        val = _input_value(driver, input_field) or ""
        return email in val
    except Exception as e:
        print(f"    [js-react] failed: {e}")
    return False


def _wait_for_dropdown(driver, input_field, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if _aria_expanded(driver, input_field) == "true":
            return True
        time.sleep(0.1)
    return False


def _confirm_chip(driver, input_field, email):
    value_before = _input_value(driver, input_field) or ""
    if not value_before.strip():
        return False

    for key in [Keys.ENTER, Keys.TAB, ",", ";"]:
        try:
            ActionChains(driver).send_keys(key).perform()
        except Exception:
            try:
                input_field.send_keys(key)
            except Exception:
                continue
        time.sleep(0.5)
        val_now = _input_value(driver, input_field)
        if val_now is not None and not val_now.strip():
            return True

    try:
        options = driver.find_elements(By.CSS_SELECTOR, "[role='listbox'] [role='option'], [role='listbox'] li")
        for opt in options:
            try:
                if not opt.is_displayed():
                    continue
                txt = (opt.text or "").strip()
                if email in txt or "Invite" in txt:
                    opt.click()
                    time.sleep(0.5)
                    val_now = _input_value(driver, input_field)
                    if val_now is not None and not val_now.strip():
                        return True
                    break
            except Exception:
                continue
    except Exception:
        pass

    return False


def insert_email_into_share(driver, input_field, email):
    email = str(email).strip()
    if not email:
        return False, False

    print(f"    [email] {email}")

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", input_field)
        driver.execute_script("arguments[0].focus();", input_field)
        time.sleep(0.15)
    except Exception:
        pass

    _clear_share_input(driver, input_field)
    time.sleep(0.2)

    methods = [
        ("atomic ActionChains",  lambda: _insert_email_atomic(driver, input_field, email)),
        ("element.send_keys",    lambda: _insert_email_send_keys(driver, input_field, email)),
        ("CDP dispatchKeyEvent", lambda: _insert_email_cdp_keys(driver, input_field, email)),
        ("execCommand",          lambda: _insert_email_exec_command(driver, input_field, email)),
        ("pyperclip + Ctrl+V",   lambda: _insert_email_clipboard(driver, input_field, email)),
        ("paste event",          lambda: _insert_email_paste_event(driver, input_field, email)),
        ("JS native-setter",     lambda: _insert_email_js_react(driver, input_field, email)),
    ]

    inserted = False
    used_method = None
    for name, fn in methods:
        try:
            if fn():
                inserted = True
                used_method = name
                print(f"    [insert] OK via {name}, value={(_input_value(driver, input_field) or '')!r}")
                break
            else:
                print(f"    [insert] {name} produced no change")
        except Exception as e:
            print(f"    [insert] {name} raised: {e}")

    if not inserted:
        print(f"    [insert] ALL METHODS FAILED")
        return False, False

    dropdown = _wait_for_dropdown(driver, input_field, timeout=3.0)
    print(f"    [dropdown] {'opened' if dropdown else 'DID NOT open'}")
    if not dropdown:
        time.sleep(1.5)
        dropdown = _aria_expanded(driver, input_field) == "true"

    chipped = _confirm_chip(driver, input_field, email)
    print(f"    [chip] {'created' if chipped else 'NOT created (method was ' + str(used_method) + ')'}")

    return inserted, chipped


def paste_batch_no_enter(driver, emails):
    time.sleep(2)

    input_field = find_share_people_input(driver, timeout=20)
    if input_field is None:
        print("  Share dialog input not found.")
        return False

    # --- SPEED ATTEMPT 1: BULK PASTE ---
    try:
        print(f"  [fast] Attempting bulk paste for {len(emails)} emails...")
        driver.execute_script("arguments[0].focus();", input_field)
        bulk_str = ", ".join([str(e).strip() for e in emails if str(e).strip()])
        
        driver.execute_cdp_cmd("Input.insertText", {"text": bulk_str})
        time.sleep(0.5)
        
        ActionChains(driver).send_keys(Keys.ENTER).perform()
        time.sleep(3)

        val = _input_value(driver, input_field)
        if not val or not val.strip():
            print("  [fast] Bulk paste successful. All emails chipped.")
            return True
        else:
            print(f"  [fast] Bulk paste left text in field. Falling back...")
    except Exception as e:
        print(f"  [fast] Bulk paste failed: {e}")

    _clear_share_input(driver, input_field)
    time.sleep(0.5)

    # --- SPEED ATTEMPT 2: FAST INDIVIDUAL LOOP ---
    try:
        print("  [fast] Switching to fast individual loop...")
        success_count = 0
        for email in emails:
            email = str(email).strip()
            if not email: continue
            try:
                driver.execute_script("arguments[0].focus();", input_field)
                ActionChains(driver).send_keys(email).send_keys(Keys.ENTER).perform()
                time.sleep(0.15)
                success_count += 1
            except Exception:
                break

        val = _input_value(driver, input_field)
        if not val or not val.strip():
            print(f"  [fast] Fast individual loop finished ({success_count} processed).")
            return True
    except Exception as e:
        print(f"  [fast] Fast loop failed: {e}")

    _clear_share_input(driver, input_field)
    time.sleep(0.5)

    # --- FALLBACK 3: ROBUST SLOW LOOP ---
    print("  [fallback] Fast methods failed. Falling back to ultra-robust method...")
    inserted_count = 0
    chipped_count = 0
    total = 0
    failed_emails = []

    for email in emails:
        email = str(email).strip()
        if not email:
            continue
        total += 1

        try:
            current_input = find_share_people_input(driver, timeout=5) or input_field
            inserted, chipped = insert_email_into_share(driver, current_input, email)
            if inserted:
                inserted_count += 1
            if chipped:
                chipped_count += 1
            else:
                failed_emails.append(email)
        except StaleElementReferenceException:
            print("    [fallback] Element went stale, re-finding...")
            current_input = find_share_people_input(driver, timeout=10)
            if current_input:
                inserted, chipped = insert_email_into_share(driver, current_input, email)
                if chipped: chipped_count += 1
            else:
                failed_emails.append(email)

        time.sleep(0.3)

    print(f"  Inserted {inserted_count}/{total}, confirmed chips {chipped_count}/{total}.")
    if failed_emails:
        print(f"  Emails that failed chip creation: {failed_emails[:10]}{'...' if len(failed_emails) > 10 else ''}")

    return chipped_count > 0 or inserted_count > 0


# ---------------------------------------------------------------------------
# Windows Save dialog helper
# ---------------------------------------------------------------------------

def click_save_in_windows_dialog(filename_hint=None, timeout=12):
    if "Desktop" not in globals() or Desktop is None:
        return False
    end_time = time.time() + timeout
    desktop = Desktop(backend="uia")
    while time.time() < end_time:
        try:
            candidates = []
            if filename_hint:
                candidates.append({"title_re": f".*{re.escape(filename_hint)}.*"})
            candidates += [{"title_re": ".*Save As.*"}, {"title_re": ".*File Download.*"}, {"title_re": ".*Open.*"}]
            dlg = None
            for c in candidates:
                try:
                    w = desktop.window(**c)
                    if w.exists(timeout=0.3):
                        dlg = w
                        break
                except Exception:
                    dlg = None
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
                    dlg.type_keys("{ENTER}")
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# Login helpers
# ---------------------------------------------------------------------------

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
    for text in NEXT_BUTTON_TEXTS:
        xpath = (
            f"//button[contains(normalize-space(.), '{text}')] | "
            f"//input[@type='submit' and contains(@value, '{text}')] | "
            f"//*[@role='button' and contains(normalize-space(.), '{text}')]"
        )
        for el in driver.find_elements(By.XPATH, xpath):
            try:
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    return True
            except Exception:
                continue
    return False


def find_visible_input(driver, input_type, manual_selector=None):
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


def click_google_docs_share_button(driver):
    for attempt in range(12):
        try:
            result = driver.execute_script("""
            const wrapper = document.querySelector('#docs-titlebar-share-client-button');
            if (!wrapper) return {ok: false, reason: 'wrapper_not_found'};
            const btn = wrapper.querySelector('div[role="button"][aria-label="Share. Private to only me. "]');
            if (!btn) return {ok: false, reason: 'button_not_found'};
            const rect = btn.getBoundingClientRect();
            if (!rect.width || !rect.height) return {ok: false, reason: 'not_visible'};
            const style = window.getComputedStyle(btn);
            if (style.pointerEvents === 'none' || style.display === 'none' || style.visibility === 'hidden') {
                return {ok: false, reason: 'blocked_by_css'};
            }
            btn.scrollIntoView({block: 'center', inline: 'center'});
            btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
            btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return {ok: true, reason: 'clicked'};
            """)
            print(f"  Share button live DOM click attempt {attempt + 1}: {result}")
            time.sleep(1)
            share_modal = driver.execute_script("""
            const candidates = ['[role="dialog"]', '[aria-label*="Share"]', '[aria-label*="People"]', '[aria-label*="Invite"]'];
            for (const sel of candidates) {
                if (document.querySelector(sel)) return true;
            }
            return false;
            """)
            if share_modal:
                print("  Share modal/dialog opened.")
                return True
        except Exception as e:
            print(f"  Share button JS click attempt {attempt + 1} failed: {e}")
        time.sleep(1)
    print("  Share button still not clickable from the live DOM.")
    return False


# ---------------------------------------------------------------------------
# Email batch processing
# ---------------------------------------------------------------------------

def process_email_file_in_batches(driver, input_path, limit):
    try:
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        print(f"  Input file not found: {input_path}")
        return
    if not lines:
        print("  No lines found in input file.")
        return

    total_batches = (len(lines) + limit - 1) // limit
    print(f"  Processing {len(lines)} lines in {total_batches} batch(es) with limit={limit}")

    for batch_index in range(0, len(lines), limit):
        batch = lines[batch_index:batch_index + limit]
        print(f"  Batch {batch_index // limit + 1}/{total_batches}: {len(batch)} lines")

        email_input = find_share_people_input(driver, timeout=20)
        if not email_input:
            print("  Could not find the share dialog email input; skipping batch.")
            continue

        try:
            if not paste_batch_no_enter(driver, batch):
                print("  Batch paste failed; skipping batch.")
                continue
        except Exception as e:
            print(f"  Batch paste raised exception: {e}")
            continue

        print("  Waiting exactly 10 seconds for Google to validate emails...")
        time.sleep(10)

        # NEW: Extract actual results directly from the Google Drive UI chips
        valid_list, error_list = get_chips_from_ui(driver)
        
        # Fallback if UI extraction completely fails (e.g. dialog closed unexpectedly)
        if not valid_list and not error_list:
            print("  [ui-parse] No chips found, falling back to text parsing.")
            valid_list, error_list = parse_email_lines(batch)

        write_unique_lines(VALID_OUTPUT_PATH, valid_list)
        write_unique_lines(ERROR_OUTPUT_PATH, error_list)
        print(f"  Valid emails saved: {len(valid_list)}")
        print(f"  Error emails saved: {len(error_list)}")

        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
        time.sleep(1)


# ---------------------------------------------------------------------------
# Main login flow
# ---------------------------------------------------------------------------

def attempt_login(driver, email, password):
    global global_email_index, add_user_batch_size, all_add_users

    def click_remind_me_later(timeout=3):
        try:
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
        try:
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

    home_page_default = "https://github.com"
    privacy_default = "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
    terms_default = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
    domain_default = "github.com"
    website_config_file = "website_config"
    home_page_url, privacy_url, terms_url, domain_value = home_page_default, privacy_default, terms_default, domain_default
    try:
        with open(website_config_file, "r", encoding="utf-8") as wf:
            for ln in wf:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    k, v = k.strip().lower(), v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    if k == "home": home_page_url = v or home_page_default
                    elif k == "privacy": privacy_url = v or privacy_default
                    elif k == "terms": terms_url = v or terms_default
                    elif k == "domain": domain_value = v or domain_default
        print(f"  website_config loaded: home={home_page_url}, privacy={privacy_url}, terms={terms_url}, domain={domain_value}")
    except FileNotFoundError:
        print("  website_config not found, using defaults for home/privacy/terms/domain.")
    except Exception as e:
        print(f"  Error reading website_config: {e}. Using defaults.")

    outcome = "UNKNOWN"
    driver.get(LOGIN_URL)

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

    try:
        if click_remind_me_later(timeout=3):
            print("  Handled 'Remind me later' after interstitial.")
    except Exception:
        pass

    try:
        if click_do_this_later(timeout=4):
            print("  Handled 'Do this later' link.")
    except Exception:
        pass

    outcome = check_outcome(driver)
    print(f"  Outcome: {outcome} | Current URL: {driver.current_url}")

    try:
        driver.get(LOGIN_URL)
        outcome = check_outcome(driver)
        print(f"  Retry outcome: {outcome} | Current URL: {driver.current_url}")
    except Exception:
        pass

    time.sleep(1)

    if CONSENT_CHECKBOX_SELECTOR:
        try:
            checkbox = WebDriverWait(driver, 3).until(EC.presence_of_element_located(CONSENT_CHECKBOX_SELECTOR))
            driver.execute_script("arguments[0].click();", checkbox)
            print("  Clicked consent checkbox.")
            time.sleep(1)
            agree_btn = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, AGREE_BUTTON_XPATH)))
            driver.execute_script("arguments[0].click();", agree_btn)
            print("  Clicked Agree and continue.")
            time.sleep(3)
        except Exception:
            print("  No consent checkbox/dialog appeared, skipping.")

    try:
        if click_remind_me_later(timeout=3):
            print("  Handled 'Remind me later' after consent checkbox (if present).")
    except Exception:
        pass

    try:
        print("  Starting email batch share flow after successful login.")
        time.sleep(10)
        try:
            template_img = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//img[contains(@src, '1wyFqxsRmKm9q--7j4WRmBMn694YdhV6hmNrfh4rVm2E_400.png')]")
                )
            )
            driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", template_img)
            print("  Clicked the Resume template image.")
        except Exception:
            print("  Resume template image not found; continuing.")

        time.sleep(25)

        try:
            if not click_google_docs_share_button(driver):
                print("  Sharing flow aborted: share button not clickable from live DOM.")
                return outcome
        except Exception as e:
            print(f"  Share button click wrapper raised exception: {e}")
            raise

        time.sleep(10)

        limit = read_limit_from_conf()
        input_path = get_email_input_path()
        process_email_file_in_batches(driver, input_path, limit)
    except Exception as e:
        print(f"  Error in share/email batch flow: {e}")

    return outcome


def main():
    load_config_and_emails()

    if not PYPERCLIP_AVAILABLE:
        print("NOTE: pyperclip not installed; clipboard method will be skipped.")
        print("      Install with: pip install pyperclip\n")

    creds = load_credentials(CREDENTIALS_FILE)
    print(f"Loaded {len(creds)} credential pairs from {CREDENTIALS_FILE}")

    print("Launching Chrome ...")
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
            try:
                driver = webdriver.Chrome(options=chrome_options)
                try:
                    driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
                    )
                except Exception:
                    pass
                try:
                    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                        "behavior": "allow",
                        "downloadPath": download_dir
                    })
                except Exception as e:
                    print(f"  Warning: Page.setDownloadBehavior failed: {e}")
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
