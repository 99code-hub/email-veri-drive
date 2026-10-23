#!/usr/bin/env python3

import csv
import os
import re
import time
import traceback
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://docs.google.com/document"
CREDENTIALS_FILE = "credentials.txt"
RESULTS_FILE = "login_test_results.csv"

NEXT_BUTTON_TEXTS = ["Next", "Continue", "Sign in", "Log in", "Login"]
WAIT_SECONDS = 10
DELAY_BETWEEN_ATTEMPTS = 2

SCRIPT_DIR = Path(__file__).resolve().parent
TEXT_FILE_PATH =  "Text File.txt"
INPUT_FALLBACK_PATH =  "input.txt"
CONF_PATH =  "conf"
VALID_OUTPUT_PATH =  "valid.txt"
ERROR_OUTPUT_PATH =  "error.txt"


def get_email_input_path():
    if TEXT_FILE_PATH.exists():
        return TEXT_FILE_PATH
    if INPUT_FALLBACK_PATH.exists():
        return INPUT_FALLBACK_PATH
    return TEXT_FILE_PATH


def read_limit_from_conf(conf_path=CONF_PATH):
    default_limit = 3000
    try:
        if not conf_path.exists():
            return default_limit
        for line in conf_path.read_text(encoding="utf-8", errors="ignore").splitlines():
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
    path = Path(path)
    existing = set()
    if path.exists():
        existing = {ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()}
    new_values = [v for v in values if v not in existing]
    if not new_values:
        return
    with path.open("a", encoding="utf-8") as f:
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


def load_credentials(path):
    credentials = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            email, password = line.split("|", 1)
            credentials.append((email.strip(), password.strip()))
    return credentials


def click_next_like_button(driver):
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


def click_if_present(driver, xpath, timeout=3):
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False


def process_email_file_in_batches(driver, input_path, limit):
    try:
        lines = [ln.strip() for ln in input_path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    except FileNotFoundError:
        print(f"  Input file not found: {input_path}")
        return

    if not lines:
        print("  No lines found in input file.")
        return

    batches = [lines[i:i + limit] for i in range(0, len(lines), limit)]
    print(f"  Processing {len(lines)} lines in {len(batches)} batch(es) with limit={limit}")

    for index, batch in enumerate(batches, 1):
        print(f"  Batch {index}/{len(batches)}: {len(batch)} lines")

        try:
            share_input = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     "input[aria-label*='People'], input[aria-label*='Email'], textarea[aria-label*='People'], input[placeholder*='Add people'], input[placeholder*='Email']")
                )
            )
            share_input.clear()
            share_input.send_keys("\n".join(batch))
            print("  Pasted batch into share-email field.")
        except Exception:
            print("  Could not find share email box; skipping this batch.")
            continue

        time.sleep(30)

        try:
            select_box = driver.find_element(By.CSS_SELECTOR, "div.pYTkkf-Bz112c-RLmnJb")
            driver.execute_script("arguments[0].click();", select_box)
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

        time.sleep(1)


def perform_docs_share_flow(driver):
    print("  Starting post-login Docs share flow.")
    time.sleep(10)

    try:
        preview = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "div.docs-homescreen-templates-templateview-preview"))
        )
        driver.execute_script("arguments[0].click();", preview)
        print("  Clicked template preview.")
    except Exception:
        print("  Template preview not found; continuing.")

    time.sleep(5)

    try:
        share_btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[@role='button' and contains(@class,'docs-titlebar-button') and contains(.,'Share')]")
            )
        )
        driver.execute_script("arguments[0].click();", share_btn)
        print("  Clicked Share.")
    except Exception:
        print("  Share button not found; continuing.")

    time.sleep(3)

    limit = read_limit_from_conf()
    input_path = get_email_input_path()
    process_email_file_in_batches(driver, input_path, limit)


def attempt_login(driver, email, password):
    driver.get(LOGIN_URL)

    email_field = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "identifierId")))
    email_field.clear()
    email_field.send_keys(email)
    time.sleep(1)
    if not click_next_like_button(driver):
        raise RuntimeError("Could not click next after email.")

    password_field = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.NAME, "Passwd")))
    password_field.clear()
    password_field.send_keys(password)
    time.sleep(2)
    if not click_next_like_button(driver):
        raise RuntimeError("Could not click next after password.")

    click_if_present(driver, "//span[normalize-space(.)='I understand']", timeout=4)
    click_if_present(driver, "//span[normalize-space(.)='Remind me later']", timeout=3)
    click_if_present(driver, "//a[contains(normalize-space(.),'Do this later')]", timeout=4)

    try:
        WebDriverWait(driver, 20).until(lambda d: "accounts.google.com" not in d.current_url.lower())
    except Exception:
        pass

    try:
        driver.get(LOGIN_URL)
        time.sleep(1)
    except Exception:
        pass

    perform_docs_share_flow(driver)
    return "SUCCESS"


def main():
    creds = load_credentials(CREDENTIALS_FILE)
    print(f"Loaded {len(creds)} credential pairs from {CREDENTIALS_FILE}")

    chrome_options = ChromeOptions()
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    results = []
    for i, (email, password) in enumerate(creds, 1):
        print(f"[{i}/{len(creds)}] Testing {email} ...")
        driver = None
        try:
            driver = webdriver.Chrome(options=chrome_options)
            outcome = attempt_login(driver, email, password)
        except Exception as e:
            outcome = f"ERROR: {e}"
            print(f"  Login flow failed: {e}")
            traceback.print_exc()
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

        print(f"  -> {outcome}")
        results.append((email, outcome))
        time.sleep(DELAY_BETWEEN_ATTEMPTS)

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
