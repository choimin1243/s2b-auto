---
name: s2b-manual-login
description: "Use when placing S2B(학교장터) items from a Google Sheet on Windows while keeping login fully manual. Opens a visible Playwright browser, waits for the user to log in, then automatically adds sheet items and writes verification artifacts."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [s2b, windows, playwright, google-sheets, procurement]
    related_skills: [s2b]
---

# S2B Manual Login Automation

## Overview

This repository automates S2B(학교장터) item placement from a Google Sheet or CSV **without receiving, storing, or typing the user's S2B password**.

The flow is intentionally human-in-the-loop:

1. CLI reads the Google Sheet and previews detected items.
2. CLI opens a visible Chromium browser at S2B login.
3. The user logs in manually in the browser.
4. The user returns to the CLI and presses Enter.
5. The script confirms login state, checks duplicates, adds items, and writes screenshots/HTML/report files.

This is suitable for Windows PowerShell/cmd because all interaction happens through a normal visible browser and a standard CLI prompt.

## When to Use

Use this when:

- The user provides only a Google Sheet URL of S2B item numbers and quantities.
- Login must be performed by the human user, not by the agent or stored credentials.
- The user wants a Windows-compatible CLI workflow.
- The task is to add items to S2B's estimate/cart flow and produce verification artifacts.

Do **not** use this for:

- Storing S2B credentials in code, `.env`, shell history, or Hermes memory.
- Headless unattended login.
- Final purchasing/contract approval; the user must still review S2B results.

## Windows Setup

Open PowerShell in the repository folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

If `py` is unavailable, use `python` instead.

## Sheet Format

The first few rows are scanned for headers. Supported header names include:

- Item number: `물품번호`, `상품번호`, `S2B번호`, `품번`, `물품코드`, `제품번호`
- Quantity: `수량`, `qty`, `quantity`, `주문수량`, `발주수량`, `요청수량`, `개수`
- Name: `물품명`, `품명`, `상품명`, `name`, `품목명`, `제품명`

Rows with blank item numbers are skipped. Quantity must parse to a positive integer.
If the same item number appears more than once, quantities are always summed and the item is placed once with the combined quantity.

## Commands

### 1. Preview the Google Sheet only

No browser and no login:

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --preview-only
```

### 2. Safe first run: one item only

This opens the browser, waits for manual login, then adds only the first detected item:

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --account personal --mode dry --manual-login-then-headless
```

### 3. Full run

After the one-item test is correct:

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --account personal --mode run --manual-login-then-headless
```

For a school/institution account, use:

```powershell
python s2b_auto.py --sheet "<sheet-url>" --account school --mode run --manual-login-then-headless
```

## Runtime Flow

When the script starts a real run:

1. A Chromium browser opens at `https://www.s2b.kr/S2BNCustomer/Login.do`.
2. If `--account personal` is set, the script tries to select the personal login tab.
3. The CLI prints:
   `로그인이 완료되었으면 Enter를 누르세요...`
4. The user logs in manually in the browser.
5. The user presses Enter in the CLI.
6. If the page still looks like the login page, the script asks again until `--login-timeout` expires.
7. After login, the script saves `storage_state.json`.
8. With `--manual-login-then-headless`, the visible login browser closes and a headless browser continues the automatic add/verify flow.
9. After login/session handoff, the script deletes existing estimate/cart rows whose item numbers appear in the current sheet, so prior dry runs or repeated sheet rows do not leave wrong quantities.
10. It then checks any remaining existing items and skips still-present item numbers.
11. For each remaining item, it opens the detail page, sets the merged `#qnt`, calls `fnSave()`, accepts S2B dialogs, and verifies in the estimate/cart list.

## Outputs

Generated under `--workdir` (default: current folder):

- `recon/*.png` and `recon/*.html`: page snapshots for verification/troubleshooting.
- `recon/trace.zip`: Playwright trace (`playwright show-trace recon/trace.zip`).
- `output/리포트_YYYYMMDD_HHMM.html`: summary report with item-level result and verification status.

## Important Safety Notes

- Do not pass `--id`, `--pw`, environment variables, or `.env` credentials. This version intentionally has no credential arguments.
- Do not run the same full sheet twice without checking S2B. The duplicate pre-check skips known item numbers, but users should still review the S2B estimate/cart list.
- `--headless` is only for `--use-session` runs. For one-command manual login handoff, use `--manual-login-then-headless`.
- `--mode dry` still performs a real one-item add after login. Use `--preview-only` if you only want parsing.
- For personal accounts, S2B may treat `[담기]` as estimate reception rather than a harmless shopping cart. Always do the one-item dry run first.

## Verification Checklist

- [ ] `python -m py_compile s2b_auto.py sheet_reader.py` passes.
- [ ] `python s2b_auto.py --sheet "<sheet-url>" --preview-only` detects the expected rows.
- [ ] `python s2b_auto.py --help` shows no credential options.
- [ ] A visible browser opens for `--mode dry`.
- [ ] Login is performed manually by the user.
- [ ] The report file and `recon/30_verify_list.*` artifacts are created after a real run.
