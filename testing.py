def find_share_people_input(driver, timeout=15):
    selectors = [
        (By.XPATH, "//input[@aria-label='Add people, groups, spaces, and calendar events']"),
        (By.XPATH, "//input[@aria-label='Add people, groups, and calendar events']"),
        (By.XPATH, "//input[@aria-label='Add people and groups']"),
        (By.XPATH, "//input[@jsname='YPqjbf']"),
        (By.XPATH, "//div[@jsname='w1sQPc']//input[@aria-label='Add people, groups, spaces, and calendar events']"),
        (By.XPATH, "//div[@jsname='w1sQPc']//input[@aria-label='Add people, groups, and calendar events']"),
        (By.CSS_SELECTOR, "input[aria-label='Add people, groups, spaces, and calendar events']"),
        (By.CSS_SELECTOR, "input[aria-label='Add people, groups, and calendar events']"),
        (By.CSS_SELECTOR, "input[aria-label*='Add people']"),
        (By.CSS_SELECTOR, "input[aria-label*='people']"),
        (By.XPATH, "//input[contains(@aria-label, 'Add people') and contains(@aria-label, 'calendar events')]"),
        (By.XPATH, "//input[contains(@aria-label, 'Add people') or contains(@aria-label, 'people') or contains(@placeholder, 'Add people')]"),
    ]

    end_time = time.time() + timeout
    while time.time() < end_time:
        for by, selector in selectors:
            try:
                el = WebDriverWait(driver, 1).until(
                    EC.visibility_of_element_located((by, selector))
                )
                if el and el.is_displayed():
                    return el
            except Exception:
                continue

        try:
            dialogs = driver.find_elements(By.XPATH, "//*[@role='dialog' or contains(@aria-label, 'Share') or contains(@aria-label, 'Invite')] ")
            for dialog in dialogs:
                if not dialog.is_displayed():
                    continue
                try:
                    inputs = dialog.find_elements(By.TAG_NAME, "input")
                except Exception:
                    inputs = []
                for el in inputs:
                    try:
                        if not el.is_displayed():
                            continue
                    except Exception:
                        continue
                    label = (el.get_attribute("aria-label") or "").lower()
                    placeholder = (el.get_attribute("placeholder") or "").lower()
                    if "people" in label or "people" in placeholder or "groups" in label or "groups" in placeholder:
                        return el
        except Exception:
            pass

        try:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for el in inputs:
                try:
                    if not el.is_displayed():
                        continue
                except Exception:
                    continue
                label = (el.get_attribute("aria-label") or "").lower()
                placeholder = (el.get_attribute("placeholder") or "").lower()
                if ("people" in label or "people" in placeholder) and ("add" in label or "groups" in label or "calendar" in label or "add" in placeholder):
                    return el
                if "add people" in label or "add people" in placeholder:
                    return el
        except Exception:
            pass

        time.sleep(0.25)

    return None
