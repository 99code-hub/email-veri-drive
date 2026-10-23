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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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

        try:
            email_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     "input[aria-label*='People'], input[aria-label*='Email'], textarea[aria-label*='People'], input[placeholder*='Add people'], input[placeholder*='Email']")
                )
            )
            email_input.clear()
            email_input.send_keys("\n".join(batch))
            print("  Pasted batch into share email box.")
        except Exception:
            print("  Could not find email input; skipping batch.")
            continue

        time.sleep(30)

        try:
            selector = driver.find_element(By.CSS_SELECTOR, "div.pYTkkf-Bz112c-RLmnJb")
            driver.execute_script("arguments[0].click();", selector)
            time.sleep(0.5)
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, "a")
            except Exception:
                try:
                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.COMMAND, "a")
                except Exception:
                    pass
        except Exception:
            pass

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


def click_google_docs_share_button(driver, timeout=25):
    exact_xpath = "//*[@id='docs-titlebar-share-client-button']//div[@role='button' and @aria-label='Share. Private to only me. ' and not(@aria-disabled='true')]"

    # strong exact selector first
    try:
        share_btn = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, exact_xpath)))
        time.sleep(0.5)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", share_btn)
        WebDriverWait(driver, 10).until(EC.visibility_of(share_btn))
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(share_btn))
        driver.execute_script("arguments[0].click();", share_btn)
        print("  Clicked Share button using exact DOM selector.")
        return True
    except Exception:
        pass

    # fallback: direct wrapper + inner button
    try:
        wrapper = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.ID, "docs-titlebar-share-client-button")))
        inner = wrapper.find_element(By.XPATH, ".//div[@role='button' and @aria-label='Share. Private to only me. ']")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", inner)
        WebDriverWait(driver, 10).until(EC.visibility_of(inner))
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(inner))
        driver.execute_script("arguments[0].click();", inner)
        print("  Clicked Share button via wrapper + inner button fallback.")
        return True
    except Exception:
        pass

    # fallback: generic share button with label text filter
    try:
        buttons = driver.find_elements(By.XPATH, "//*[@role='button' and contains(@aria-label, 'Share')]")
        for btn in buttons:
            label = (btn.get_attribute("aria-label") or "").strip()
            if "share" in label.lower() and "private to only me" in label.lower():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                WebDriverWait(driver, 10).until(EC.visibility_of(btn))
                WebDriverWait(driver, 10).until(EC.element_to_be_clickable(btn))
                driver.execute_script("arguments[0].click();", btn)
                print("  Clicked Share button via label filter fallback.")
                return True
    except Exception:
        pass

    print("  Share button not found or not clickable.")
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
            click_google_docs_share_button(driver)
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
