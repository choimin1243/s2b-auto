# -*- coding: utf-8 -*-
"""
S2B 자동화 — 구글시트/CSV 로드 → 사용자가 브라우저에서 직접 로그인 →
중복확인 → 상세페이지 [담기] (수량 입력 + fnSave) → 검증.

사용:
  python s2b_auto.py --sheet "<gsheet-url>" --account personal --preview-only
  python s2b_auto.py --sheet "<gsheet-url>" --account personal --mode run
  python s2b_auto.py --csv items.csv --account school --mode dry

기본값은 수동 로그인이다. 프로그램이 브라우저를 열면 사용자가 직접 S2B에 로그인하고,
로그인이 끝난 뒤 CLI에서 Enter를 누르면 세션을 저장하고, 옵션에 따라 보이는 창을 닫은 뒤
headless 브라우저로 전환해 물품을 담는다.
아이디/비밀번호를 코드, 명령행, 환경변수, 리포트, 로그에 저장하지 않는다.
"""
import argparse
import io
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# UTF-8 콘솔 (Windows)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from sheet_reader import load_items_from_sheet, load_items_from_csv

# ---------------- 상수 ----------------
LOGIN_URL = "https://www.s2b.kr/S2BNCustomer/Login.do"
DETAIL_URL = "https://www.s2b.kr/S2BNCustomer/rema100.do?forwardName=detail&f_re_estimate_code={no}"
SEARCH_URL = (
    "https://www.s2b.kr/S2BNCustomer/S2B/scrweb/remu/rema/searchengine/"
    "s2bCustomerSearch.jsp?actionType=MAIN_SEARCH&searchField=&startIndex="
    "&viewCount=50&viewType=LIST&sortField=RANK"
    "&priceMin=0&priceMax=0&priceMinSet=0&priceMaxSet=0"
    "&categoryLevel1Code=&categoryLevel2Code=&categoryLevel3Code=&categoryLevel3Name="
    "&areaCode=&categoryWinStatus=none&companyCodeParam=&priceNewSet=true"
    "&publicPurchaseCode=&f_edufine_code=&submit_yn=Y"
    "&searchQuery={kw}&searchRequery=&locationGbn=all"
)
EST_LIST_URL = "https://www.s2b.kr/S2BNCustomer/remc100.do?forwardName=estimateList"
CART_URL_SCHOOL = "https://www.s2b.kr/S2BNCustomer/Tomu400.do?forwardName=list"

ACCOUNT_MAP = {
    "school":   {"tab": "#sclogin", "form": "school_loginForm",   "idx": 1},
    "personal": {"tab": "#prlogin", "form": "personal_loginForm", "idx": 2},
}


def target_list_url(account: str) -> str:
    """S2B가 실제로 물품을 담는 선택물품함 목록 URL을 반환한다."""
    return EST_LIST_URL


def merge_duplicate_items(items: list) -> list:
    """같은 물품번호가 여러 행에 있으면 수량을 합산해 한 번만 담는다."""
    merged = {}
    order = []
    for item in items:
        no = str(item.get("no", "")).strip()
        if not no:
            continue
        qty = int(item.get("qty", 0))
        if no not in merged:
            merged[no] = dict(item)
            merged[no]["no"] = no
            merged[no]["qty"] = qty
            rows = item.get("rows") or [item.get("row")]
            merged[no]["rows"] = [r for r in rows if r is not None]
            order.append(no)
            continue
        merged[no]["qty"] += qty
        rows = item.get("rows") or [item.get("row")]
        merged[no].setdefault("rows", []).extend(r for r in rows if r is not None)
        if not merged[no].get("name") and item.get("name"):
            merged[no]["name"] = item["name"]

    out = [merged[no] for no in order]
    duplicates = [item for item in out if len(item.get("rows", [])) > 1]
    if duplicates:
        print(f"  [merge-final] 같은 물품번호 {len(duplicates)}종 합산 후 1회만 담기")
        for item in duplicates:
            print(
                f"    no={item['no']} rows={item.get('rows', [])} "
                f"합산수량={item['qty']} name={item.get('name', '')}"
            )
    return out

# ---------------- 로그인 보조 ----------------
def install_dialog_auto_accept(page):
    """S2B confirm/alert 창을 기록하고 자동 확인한다. 비밀번호는 다루지 않는다."""
    dialog_log = []

    def _accept(dialog):
        dialog_log.append((dialog.type, dialog.message))
        print(f"  [dialog:{dialog.type}] {dialog.message[:120]}")
        dialog.accept()

    page.on("dialog", _accept)
    return dialog_log


def is_logged_in(page) -> bool:
    """현재 페이지가 로그인 완료 상태인지 보수적으로 판정한다."""
    url = page.url or ""
    if "Login.do" in url or "pwd_changeinfo" in url:
        return False
    try:
        if page.locator("form[name='personal_loginForm'], form[name='school_loginForm']").count() > 0:
            return False
    except Exception:
        pass
    return True


def _wait_for_enter(timeout_seconds: int) -> bool:
    """Enter 입력을 timeout 동안 기다린다. Windows와 Unix 모두 지원."""
    prompt = "로그인이 완료되었으면 Enter를 누르세요... "
    print(prompt, end="", flush=True)
    if os.name == "nt":
        import msvcrt
        start = time.time()
        while time.time() - start < timeout_seconds:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    return True
            time.sleep(0.1)
        print()
        return False

    import select
    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if not ready:
        print()
        return False
    sys.stdin.readline()
    return True


def manual_login(page, account: str, recon_dir: Path, timeout_seconds: int = 300) -> bool:
    """브라우저를 열어 사용자가 직접 로그인하게 하고, 로그인 완료를 자동 감지한다."""
    print(f"[login] manual account={account}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(0.5)

    cfg = ACCOUNT_MAP[account]
    if cfg["tab"] != "#sclogin":
        try:
            page.locator(f"a[href='{cfg['tab']}']").first.click()
            time.sleep(0.3)
        except Exception as e:
            print(f"  탭 클릭 실패(수동으로 선택 가능): {e}")

    print("\n=== 수동 로그인 필요 ===")
    print("1) 열린 브라우저에서 S2B 아이디/비밀번호를 직접 입력해 로그인하세요.")
    print("2) 비밀번호 변경 안내/알림이 나오면 사용자가 직접 처리하거나 다음에 변경을 누르세요.")
    print("3) 로그인 완료가 감지되면 Enter 없이 자동으로 다음 단계로 진행합니다.")
    print(f"   제한시간: {timeout_seconds}초")

    start = time.time()
    last_notice = 0
    while True:
        remaining = max(0, int(timeout_seconds - (time.time() - start)))
        if remaining == 0:
            print("!! 수동 로그인 대기 시간이 초과되었습니다.")
            snap(page, "10_manual_login_timeout", recon_dir)
            return False
        try:
            page.wait_for_load_state("domcontentloaded", timeout=1000)
        except PWTimeout:
            pass
        ok = is_logged_in(page)
        if ok:
            snap(page, "10_after_manual_login", recon_dir)
            print(f"[login] manual success=True url={page.url}")
            return True
        if time.time() - last_notice >= 10:
            print(f"[login] 로그인 대기 중... 남은 시간: {remaining}초")
            last_notice = time.time()
        time.sleep(1)

# ---------------- 유틸 ----------------
def snap(page, name, root: Path):
    p = root / name
    try:
        page.screenshot(path=str(p) + ".png", full_page=True)
    except Exception:
        pass
    try:
        (root / (name + ".html")).write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    print(f"  [snap] {name}")


def read_registered_nos_from_current_page(page, target_nos=None) -> set:
    """현재 선택물품함 페이지에서 물품번호를 최대한 보수적으로 읽는다."""
    targets = set(target_nos or [])
    found = set()
    try:
        records = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('input[type="checkbox"]'))
              .map((cb) => {
                try { return JSON.parse(cb.value || '{}'); }
                catch (e) { return {}; }
              })
            """
        )
        for obj in records:
            no = str(
                obj.get("re_estimate_code")
                or obj.get("f_re_estimate_code")
                or obj.get("goods_code")
                or ""
            ).strip()
            if no and (not targets or no in targets):
                found.add(no)
    except Exception as e:
        print(f"  [warn] 체크박스 기반 목록 읽기 실패: {e}")

    try:
        body = page.content()
        for no in re.findall(r"\b\d{15}\b", body):
            if not targets or no in targets:
                found.add(no)
    except Exception as e:
        print(f"  [warn] HTML 기반 목록 읽기 실패: {e}")
    return found


def delete_existing_target_items(page, account: str, items: list, recon_dir: Path) -> list:
    """시트 대상 물품번호가 이미 접수/담기 목록에 있으면 삭제한다.

    전체 실행에서는 이전 dry 테스트나 과거 실행분이 남아 있으면 합산 수량과 맞지 않을 수 있다.
    그래서 현재 시트에 있는 물품번호만 선택 삭제한 뒤, 병합된 최종 수량으로 다시 담는다.
    """
    url = target_list_url(account)
    target_nos = [it["no"] for it in items]
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(1)
    snap(page, "14_before_replace_existing", recon_dir)
    try:
        matched = page.evaluate(
            """
            (targetNos) => {
              const targets = new Set(targetNos);
              const matched = [];
              document.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                try {
                  const obj = JSON.parse(cb.value || '{}');
                  const no = obj.re_estimate_code || obj.f_re_estimate_code || obj.goods_code || '';
                  if (targets.has(no)) {
                    cb.checked = true;
                    matched.push({
                      no,
                      qty: obj.estimate_quantity || '',
                      name: obj.goods_name || '',
                      rc: obj.rc_estimate_code || ''
                    });
                  }
                } catch (e) {}
              });
              return matched;
            }
            """,
            target_nos,
        )
    except Exception as e:
        print(f"  [warn] 기존 항목 선택 실패: {e}")
        return []

    if not matched:
        print("  [replace] 시트 물품번호와 겹치는 기존 항목 없음")
        return []

    print(f"  [replace] 시트와 겹치는 기존 항목 {len(matched)}건 삭제 후 재담기")
    for m in matched:
        print(f"    delete no={m.get('no')} qty={m.get('qty')} name={m.get('name')}")
    try:
        page.evaluate("estimateSelDelete()")
        time.sleep(2)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PWTimeout:
            pass
        snap(page, "14_after_replace_existing", recon_dir)
    except Exception as e:
        print(f"  [warn] 기존 항목 삭제 호출 실패: {e}")
    return matched


# ---------------- 중복 확인 ----------------
def get_registered_item_nos(page, account: str) -> set:
    """현재 접수내역(개인) 또는 장바구니(학교)에 있는 물품번호 집합 반환."""
    url = target_list_url(account)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        return read_registered_nos_from_current_page(page)
    except Exception as e:
        print(f"  [warn] 기등록 목록 조회 실패: {e}")
        return set()


def is_item_already_registered(page, account: str, item_no: str) -> bool:
    """추가 직전에 단일 물품번호 존재 여부를 다시 확인해 중복 담기를 막는다."""
    url = target_list_url(account)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        return item_no in read_registered_nos_from_current_page(page, [item_no])
    except Exception as e:
        print(f"  [warn] 중복 방지 확인 실패: {e}")
        return True

# ---------------- 담기 ----------------
def add_one(page, item: dict, idx: int, recon_dir: Path) -> dict:
    print(f"\n[item {idx}] no={item['no']} qty={item['qty']} name={item.get('name','')}")
    out = {"idx": idx, "no": item["no"], "qty": item["qty"],
           "name": item.get("name", ""), "status": "FAIL", "reason": ""}

    page.goto(DETAIL_URL.format(no=item["no"]), wait_until="domcontentloaded", timeout=30000)
    time.sleep(1)
    snap(page, f"21_detail_{idx}_{item['no']}", recon_dir)

    if page.locator("#qnt").count() == 0:
        out["reason"] = "상세페이지 #qnt 없음 (물품번호 무효 또는 단종 가능)"
        return out

    page.locator("#qnt").fill(str(item["qty"]))
    snap(page, f"22_qnt_{idx}", recon_dir)

    popup_holder = {"p": None}
    page.once("popup", lambda p: popup_holder.__setitem__("p", p))

    try:
        page.evaluate("fnSave()")
    except Exception as e:
        out["reason"] = f"fnSave 호출 예외: {e}"
        return out

    # 팝업이 열릴 시간을 대기
    time.sleep(2)
    snap(page, f"23_after_fnSave_{idx}", recon_dir)

    if popup_holder["p"]:
        pop = popup_holder["p"]
        try:
            pop.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            pop.screenshot(path=str(recon_dir / f"24_popup_{idx}.png"), full_page=True)
            content = pop.content()
            (recon_dir / f"24_popup_{idx}.html").write_text(content, encoding="utf-8")
            if "접수되었습니다" in content or "담겼습니다" in content:
                out["status"] = "OK"
            else:
                out["reason"] = "팝업 결과 확인 필요"
        except Exception as e:
            # 팝업이 빠르게 닫혔을 가능성 — 검증 단계에서 재판정
            out["status"] = "CHECK"
            out["reason"] = f"팝업 캡처 실패(검증단계 재확인): {e}"
        try:
            pop.close()
        except Exception:
            pass
    else:
        out["reason"] = "popup 미발생"
    return out

# ---------------- 검증 ----------------
def verify(page, account: str, items: list, recon_dir: Path) -> dict:
    """담은 후 목록 페이지에서 물품번호 존재 여부 확인."""
    page.goto(target_list_url(account), wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.5)
    snap(page, "30_verify_list", recon_dir)
    body = page.content()
    found = {it["no"]: (body.count(it["no"]) > 0) for it in items}
    return found

# ---------------- 리포트 ----------------
def write_report(out_dir: Path, results: list, found: dict, account: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    f = out_dir / f"리포트_{ts}.html"
    rows_html = []
    for r in results:
        no = r["no"]
        status = r["status"]
        if status == "SKIP":
            verified = "⏭"
            row_style = ' style="background:#f9f9f9;color:#888"'
        else:
            verified = "✅" if found.get(no) else "❌"
            row_style = ""
        rows_html.append(
            f"<tr{row_style}><td>{r['idx']}</td><td>{no}</td><td>{r['name']}</td>"
            f"<td>{r['qty']}</td><td>{status}</td>"
            f"<td>{verified}</td><td>{r['reason']}</td></tr>"
        )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>S2B 자동담기 리포트 {ts}</title>
<style>body{{font-family:맑은 고딕,sans-serif;padding:24px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left}}
th{{background:#f0f0f0}}</style></head>
<body>
<h2>S2B 자동담기 리포트</h2>
<p>실행: {ts} · 계정: <b>{account}</b> · 총 {len(results)}건</p>
<table><thead><tr><th>#</th><th>물품번호</th><th>품명</th><th>요청수량</th>
<th>담기 결과</th><th>목록 검증</th><th>비고</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>
</body></html>
"""
    f.write_text(html, encoding="utf-8")
    return f

def run_automation(page, account: str, items: list, recon_dir: Path, out_dir: Path) -> Path:
    """로그인된 page에서 중복 확인 → 담기 → 검증 → 리포트까지 실행한다."""
    print("\n[replace] 시트 대상 기존 항목 정리 중...")
    delete_existing_target_items(page, account, items, recon_dir)

    print("\n[dup-check] 기등록 물품 확인 중...")
    registered_nos = get_registered_item_nos(page, account)
    print(f"  현재 접수/담기 {len(registered_nos)}건 확인됨")
    snap(page, "15_precheck_list", recon_dir)

    results = []
    for i, it in enumerate(items, 1):
        if it["no"] in registered_nos:
            print(f"  [skip-dup] no={it['no']} name={it.get('name','')} → 이미 등록됨, 건너뜀")
            results.append({
                "idx": i, "no": it["no"], "qty": it["qty"],
                "name": it.get("name", ""), "status": "SKIP",
                "reason": "이미 접수/담기됨 (중복 방지)"
            })
            continue
        if is_item_already_registered(page, account, it["no"]):
            registered_nos.add(it["no"])
            print(f"  [skip-live-dup] no={it['no']} name={it.get('name','')} → 추가 직전 목록에서 발견, 건너뜀")
            results.append({
                "idx": i, "no": it["no"], "qty": it["qty"],
                "name": it.get("name", ""), "status": "SKIP",
                "reason": "추가 직전 이미 담긴 항목 확인 (중복 방지)"
            })
            continue
        results.append(add_one(page, it, i, recon_dir))
        if is_item_already_registered(page, account, it["no"]):
            registered_nos.add(it["no"])

    found = verify(page, account, items, recon_dir)

    # CHECK 상태 재판정: 팝업 캡처 실패했지만 접수내역에 있으면 OK
    for r in results:
        if r["status"] == "CHECK":
            if found.get(r["no"]):
                r["status"] = "OK"
                r["reason"] = "팝업 캡처 실패했으나 접수내역에서 확인됨"
            else:
                r["status"] = "FAIL"

    report = write_report(out_dir, results, found, account)
    print(f"\n[report] {report}")

    ok_cnt = sum(1 for r in results if r["status"] == "OK" and found.get(r["no"]))
    skip_cnt = sum(1 for r in results if r["status"] == "SKIP")
    fail_cnt = sum(1 for r in results if r["status"] == "FAIL")
    print(f"[summary] OK={ok_cnt}  SKIP(중복)={skip_cnt}  FAIL={fail_cnt}  / 총{len(results)}건")
    return report


# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="구글시트 URL (export csv 가능해야 함)")
    ap.add_argument("--sheet-name", dest="sheet_name", default="",
                    help="구글시트 내 탭(시트)명. 지정 시 gviz/tq CSV로 받음.")
    ap.add_argument("--csv", help="로컬 CSV 경로")
    ap.add_argument("--account", choices=["school", "personal"], default="personal")
    ap.add_argument("--mode", choices=["dry", "run"], default="dry",
                    help="dry=로그인 후 첫 1건만 담아 검증 / run=전체 담기")
    ap.add_argument("--preview-only", action="store_true",
                    help="브라우저/로그인 없이 시트 파싱 결과만 확인")
    ap.add_argument("--login-timeout", type=int, default=300,
                    help="수동 로그인 완료 대기 시간(초), 기본 300")
    ap.add_argument("--login-only", action="store_true",
                    help="수동 로그인 후 세션 파일만 저장하고 종료")
    ap.add_argument("--use-session", action="store_true",
                    help="이미 저장된 세션 파일로 로그인 없이 실행")
    ap.add_argument("--manual-login-then-headless", action="store_true",
                    help="수동 로그인 세션을 저장한 뒤 브라우저를 닫고 headless로 자동 담기")
    ap.add_argument("--session-file", default="",
                    help="Playwright 세션 파일 경로(기본: --workdir/storage_state.json)")
    ap.add_argument("--headless", action="store_true", help="--use-session 실행 시 브라우저 숨김")
    ap.add_argument("--workdir", default=".", help="recon/output 저장 위치 (기본 현재 폴더)")
    args = ap.parse_args()

    if not args.sheet and not args.csv:
        ap.error("--sheet 또는 --csv 중 하나 필수")

    workdir = Path(args.workdir).resolve()
    recon_dir = workdir / "recon"
    out_dir = workdir / "output"
    recon_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 시트 로드
    items = (load_items_from_sheet(args.sheet, args.sheet_name) if args.sheet
             else load_items_from_csv(args.csv))
    items = merge_duplicate_items(items)
    print(f"[sheet] 로드 {len(items)}건")
    for it in items[:5]:
        print(f"  preview: {it}")
    if not items:
        print("!! 품목 0건. 종료.")
        return

    if args.preview_only:
        print("[preview-only] 로그인/브라우저 실행 없이 시트 파싱만 확인하고 종료합니다.")
        return

    if args.mode == "dry":
        items = items[:1]
        print(f"[mode] dry → 로그인 후 1건만 담아 검증")

    session_file = Path(args.session_file).expanduser() if args.session_file else workdir / "storage_state.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)

    if args.headless and not args.use_session:
        ap.error("--headless는 --use-session 실행에서만 사용하세요. 수동 로그인 창은 항상 표시됩니다.")
    if args.manual_login_then_headless and args.login_only:
        ap.error("--manual-login-then-headless 와 --login-only 는 함께 사용할 수 없습니다.")
    if args.use_session and not session_file.exists():
        ap.error(f"세션 파일이 없습니다: {session_file}. 먼저 --login-only 또는 --manual-login-then-headless를 실행하세요.")

    with sync_playwright() as p:
        if args.use_session:
            print(f"[session] 저장된 세션 사용: {session_file}")
            browser = p.chromium.launch(headless=args.headless, slow_mo=50)
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="ko-KR",
                storage_state=str(session_file),
            )
            ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = ctx.new_page()
            install_dialog_auto_accept(page)
            try:
                run_automation(page, args.account, items, recon_dir, out_dir)
            finally:
                ctx.tracing.stop(path=str(recon_dir / "trace.zip"))
                browser.close()
            return

        # 2) 수동 로그인 창 실행 및 세션 저장
        browser = p.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
        page = ctx.new_page()
        install_dialog_auto_accept(page)
        try:
            ok = manual_login(page, args.account, recon_dir, args.login_timeout)
            if not ok:
                print("!! 수동 로그인 확인 실패. 종료.")
                return
            ctx.storage_state(path=str(session_file))
            print(f"[session] 로그인 세션 저장: {session_file}")
        finally:
            browser.close()

        if args.login_only:
            print("[login-only] 세션 저장 후 종료합니다.")
            return

        if args.manual_login_then_headless:
            print("[handoff] 수동 로그인 창을 닫고 headless 자동 담기로 전환합니다.")
            browser = p.chromium.launch(headless=True, slow_mo=50)
        else:
            print("[handoff] 저장된 세션으로 새 브라우저를 열어 자동 담기를 진행합니다.")
            browser = p.chromium.launch(headless=False, slow_mo=50)

        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="ko-KR",
            storage_state=str(session_file),
        )
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = ctx.new_page()
        install_dialog_auto_accept(page)
        try:
            run_automation(page, args.account, items, recon_dir, out_dir)
        finally:
            ctx.tracing.stop(path=str(recon_dir / "trace.zip"))
            browser.close()

if __name__ == "__main__":
    main()
