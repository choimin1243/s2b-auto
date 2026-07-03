---
name: s2b-auto
description: "Use when working with this S2B automation repository to register estimate items after manual S2B login. Supports two workflows: (1) registering item numbers and quantities from a Google Sheet or CSV, and (2) condition-based registration where the user gives product keywords, quantity constraints, and a target total amount, then Codex searches S2B after login, extracts item numbers and unit prices, solves a valid quantity combination, creates a temporary CSV, registers it, and writes verification artifacts."
---

# S2B Auto Skill

Use this skill for S2B estimate reception tasks in this repository. Never ask for, store, or type S2B credentials. The user must log in directly in the visible S2B browser window.

This skill has two supported workflows:

1. Google Sheet registration: read item numbers and quantities from a Google Sheet or CSV, then register them in S2B.
2. Condition-based registration: after login, search S2B by product keywords, extract item numbers and unit prices, solve quantity constraints against a target amount, then register the selected items.

## Setup

Run once in the repository folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

If `py` is unavailable, use the bundled or system Python executable.

Use `.\.venv\Scripts\python.exe` for all commands when possible.

## Manual Login

For first-time login or expired sessions:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --csv "<csv-path>" --account personal --mode run --manual-login-then-headless
```

If the user says login is complete in chat, create the signal file:

```powershell
Set-Content -Path .\login_done.signal -Value "done" -Encoding ASCII
```

After a successful login, the script saves `storage_state.json`. For later runs, prefer:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --csv "<csv-path>" --account personal --mode run --use-session --headless
```

Use `--account school` only when the user explicitly wants the school account tab.

## Workflow 1: Google Sheet Registration

Use this when the user provides a Google Sheet URL or a CSV containing S2B item numbers and quantities.

1. Preview the parsed rows:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --sheet "<sheet-url>" --preview-only
```

2. Confirm the detected rows, duplicate merges, and quantities.

3. Register all rows:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --sheet "<sheet-url>" --account personal --mode run --manual-login-then-headless
```

If `storage_state.json` already exists and is still valid:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --sheet "<sheet-url>" --account personal --mode run --use-session --headless
```

The script automatically merges duplicate item numbers by summing quantities, removes existing matching estimate-list rows, adds the final quantities, and writes `recon/` plus `output/` artifacts.

## Workflow 2: Condition-Based Registration

Use this when the user gives conditions such as:

- "간식 20개 이상 30개 이하, 연필 5개 이상으로 50,800원 맞춰줘"
- "A는 10개 이상, B는 3개 이상, 총액 100,000원 이하로 접수해줘"

Do not rely on an external price list unless the user explicitly provides one. Use S2B after login as the source of item numbers and unit prices.

### Required Steps

1. Parse the request into structured constraints:
   - product keyword per group, such as `간식`, `연필`
   - minimum and maximum quantity per group
   - target total amount or budget rule
   - account type, default `personal`

2. Ensure login:
   - If `storage_state.json` exists, use it first.
   - If S2B redirects to login, start a manual login flow and wait for the user.

3. Search S2B for each product keyword using Playwright with the saved session.

4. Extract candidate rows from the search result:
   - item number from `input[name="checkFlag"]`, `chk+<number>`, `goViewPage('<number>')`, or detail links
   - item name from the nearest result row text
   - unit price from the row text by matching Korean won amounts
   - ignore filter checkboxes such as region, certification, and category controls

5. Prefer candidates whose row text actually contains the requested keyword or a close synonym. Avoid unrelated rows even if they appear in broad S2B search results.

6. Solve integer quantities:
   - satisfy every min/max quantity constraint
   - match the target amount exactly when the user says "맞춰줘"
   - if exact match is impossible, report the nearest alternatives and do not register unless the user approves

7. Create a temporary CSV with these columns:

```csv
물품명,수량,예상단가,물품번호,총금액
```

8. Preview the CSV:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --csv "<temp-csv>" --preview-only
```

9. Register using the saved session:

```powershell
.\.venv\Scripts\python.exe s2b_auto.py --csv "<temp-csv>" --account personal --mode run --use-session --headless
```

10. Verify success from the summary line and final report. Report selected item names, item numbers, unit prices, quantities, total amount, and report path to the user.

### S2B Search and Encoding Pitfalls

Use this field-tested path for condition-based registration. It avoids the failures seen in live runs.

1. Do not build S2B Korean search URLs by hand from PowerShell. Korean query strings may arrive at S2B as `??` or mojibake and produce irrelevant or empty results.

2. Instead, open the S2B search page with an empty query, fill the visible search input, then call S2B's own search function:

```python
page.goto(
    "https://www.s2b.kr/S2BNCustomer/S2B/scrweb/remu/rema/searchengine/"
    "s2bCustomerSearch.jsp?loggerCP=/즉시견적/물품정보%20검색&search=1",
    wait_until="domcontentloaded",
    timeout=30000,
)
page.locator("#searchQuery").fill(keyword)
page.evaluate("startSearch('MAIN_SEARCH')")
page.wait_for_load_state("domcontentloaded", timeout=30000)
```

3. When running inline Python from PowerShell, avoid literal Korean in regexes, variable filters, or source strings if the command is passed through a here-string. Use Unicode escapes or `chr()` for fragile characters:

```python
queries = [("snack", "\uac04\uc2dd"), ("pencil", "\uc5f0\ud544")]
won = chr(0xC6D0)
price_matches = re.findall(r"([0-9][0-9,]*)\s*" + won, row_text)
```

4. Extract actual product candidates from product checkboxes only. Ignore region, category, certification, and option checkboxes.

```python
rows = page.evaluate("""
() => Array.from(document.querySelectorAll('input[name="checkFlag"]')).map((cb) => {
  const row = cb.closest('tr') || cb.closest('li') || cb.closest('div');
  return {
    id: cb.id || '',
    value: cb.value || '',
    text: row ? row.innerText.replace(/\\s+/g, ' ').trim() : '',
    html: row ? row.innerHTML : ''
  };
})
""")
```

5. Derive item numbers from `value`, `id`, or row HTML. Product checkbox ids often look like `chk+202509184660686`.

```python
code = re.search(r"\d{13,15}", value + " " + checkbox_id + " " + html)
```

6. Do not trust the script's first numeric token as price. Product names often contain counts, sizes, grams, model numbers, or review counts. Parse only amounts followed by the Korean won character:

```python
prices = []
for match in re.finditer(r"([0-9][0-9,]*)\s*" + won, row_text):
    price = int(match.group(1).replace(",", ""))
    if 100 <= price <= 200000:
        prices.append(price)
unit_price = prices[0] if prices else None
```

7. Save raw search artifacts before solving. This makes the run recoverable without searching again:

```python
page.screenshot(path=f"recon/ui_search_{label}.png", full_page=True)
Path(f"recon/ui_search_{label}.html").write_text(page.content(), encoding="utf-8")
Path(f"recon/ui_goods_{label}.json").write_text(json.dumps(goods, ensure_ascii=False, indent=2), encoding="utf-8")
```

8. If keyword filtering is needed inside inline PowerShell/Python, prefer filtering by the already-separated query label (`snack`, `pencil`) or by Unicode-escaped strings. Literal Korean lists inside a PowerShell here-string can silently become corrupted.

9. Choose a simple exact-match solution from the top relevant S2B results, then create a temporary CSV and register through `s2b_auto.py`. Example from a verified run:

```csv
물품명,수량,예상단가,물품번호,총금액
행사용 달달간식세트 과자 선물세트 간식 꾸러미 달달간식세트,26,1300,202509184660686,33800
2B 연필 스테들러 134 옐로우연필 지우개연필 12자루,5,3400,202311277631000,17000
```

This example satisfies: snack quantity 20-30, pencil quantity at least 5, total 50,800.

### Combination Solver Guidance

For small candidate sets, brute force is acceptable:

```python
for snack in snack_candidates:
    for pencil in pencil_candidates:
        for snack_qty in range(snack_min, snack_max + 1):
            for pencil_qty in range(pencil_min, pencil_max + 1):
                total = snack.price * snack_qty + pencil.price * pencil_qty
                if total == target:
                    return selection
```

When a quantity has no maximum, set a practical upper bound from the target:

```python
max_qty = target // unit_price
```

If many candidates exist, first deduplicate by `(item_number, price)`, then keep a manageable set of relevant candidates per keyword before solving.

## Output Expectations

After either workflow, check:

- `output/리포트_*.html`
- `recon/30_verify_list.html`
- `recon/30_verify_list.png`
- `recon/trace.zip`

Tell the user whether registration succeeded, how many rows were OK/SKIP/FAIL, and where the report is saved.

## Safety Rules

- Never collect S2B credentials.
- Never complete final purchasing or contract approval.
- Never register condition-based results if the total cannot be matched exactly and the user requested an exact amount.
- Do not silently substitute unrelated products for broad keywords. If S2B search results are noisy, refine the keyword or ask the user to confirm the candidate class before registration.
- If a run may overwrite or delete existing matching estimate-list items, mention that the script replaces matching item numbers before re-adding final quantities.
