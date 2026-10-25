def find_share_people_input(driver, timeout=15):
    selectors = [
        (By.XPATH, "//input[@aria-label='Add people, groups, spaces, and calendar events']"),
        (By.XPATH, "//input[@jsname='YPqjbf']"),
        (By.XPATH, "//div[@jsname='w1sQPc']//input[@aria-label='Add people, groups, spaces, and calendar events']"),
        (By.XPATH, "//input[@role='combobox' and @jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "input[aria-label='Add people, groups, spaces, and calendar events']"),
        (By.CSS_SELECTOR, "input[jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "input.zeumMd.d2j1H[role='combobox'][jsname='YPqjbf']"),
        (By.CSS_SELECTOR, "input.zeumMd.d2j1H[aria-label='Add people, groups, spaces, and calendar events']"),
        (By.XPATH, "//input[contains(@aria-label, 'Add people') and contains(@aria-label, 'calendar events')]"),
    ]

    for by, selector in selectors:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((by, selector))
            )
            return el
        except Exception:
            continue
    return None
