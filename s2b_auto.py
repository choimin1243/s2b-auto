# -*- coding: utf-8 -*-
"""
S2B 자동화 — 로그인 → 중복확인 → 검색 → 상세 → [담기] (수량 입력 + fnSave) → 검증.

사용:
  python s2b_auto.py --sheet "<gsheet-url>" --account personal --mode dry
  python s2b_auto.py --csv items.csv --account school --mode run

자격증명 우선순위: --id/--pw > 환경변수 S2B_ID/S2B_PW > .env > 인터랙티브 입력.
자격증명은 메모리·로그·리포트에 기록하지 않는다.
"""
import argparse
import getpass
import io
import json
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

# ---------------- 자격증명 로더 ----------------
def load_credentials(args) -> tuple:
    """우선순위: CLI > env > .env > prompt. 어디에도 저장하지 않음."""
    sid = args.id or os.environ.get("S2B_ID")
    spw = args.pw or os.environ.get("S2B_PW")

    env_path = Path(__file__).parent / ".env"
    if (not sid or not spw) and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip(); v = v.strip()
                if k == "S2B_ID" and not sid: sid = v
                if k == "S2B_PW" and not spw: spw = v

    if not sid:
        sid = input("S2B 아이디: ").strip()
    if not spw:
        spw = getpass.getpass("S2B 비밀번호: ")
    return sid, spw

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

# ---------------- 로그인 ----------------
def login(page, account: str, sid: str, spw: str, recon_dir: Path) -> bool:
    cfg = ACCOUNT_MAP[account]
    print(f"[login] account={account} ({cfg['form']})")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(0.5)

    dialog_log = []
    page.on("dialog", lambda d: (dialog_log.append((d.type, d.message)), d.accept()))

    if cfg["tab"] != "#sclogin":
        try:
            page.locator(f"a[href='{cfg['tab']}']").first.click()
            time.sleep(0.3)
        except Exception as e:
            print(f"  탭 클릭 실패: {e}")

    page.locator(f"form[name='{cfg['form']}'] input[name='uid']").fill(sid)
    page.locator(f"form[name='{cfg['form']}'] input[name='pwd']").fill(spw)
    page.evaluate(f"retrieveLogin2('{cfg['form']}', {cfg['idx']})")

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PWTimeout:
        pass
    time.sleep(1)
    snap(page, "10_after_login", recon_dir)

    for dtype, msg in dialog_log:
        if "실패" in msg:
            print(f"  !! 로그인 실패: {msg[:80]}")
            return False

    if "pwd_changeinfo" in page.url:
        print("  pwd_changeinfo → modifyNext()")
        try:
            page.evaluate("modifyNext()")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(0.5)
        except Exception as e:
            print(f"  modifyNext 실패: {e}")
        snap(page, "11_after_pw_skip", recon_dir)

    ok = ("Login.do" not in page.url) and ("pwd_changeinfo" not in page.url)
    print(f"[login] success={ok} url={page.url}")
    return ok

# ---------------- 중복 확인 ----------------
def get_registered_item_nos(page, account: str) -> set:
    """현재 접수내역(개인) 또는 장바구니(학교)에 있는 물품번호 집합 반환."""
    url = EST_LIST_URL if account == "personal" else CART_URL_SCHOOL
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        body = page.content()
        # S2B 물품번호는 주로 15자리 숫자
        nos = set(re.findall(r'\b\d{15}\b', body))
        return nos
    except Exception as e:
        print(f"  [warn] 기등록 목록 조회 실패: {e}")
        return set()

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
    if account == "personal":
        page.goto(EST_LIST_URL, wait_until="domcontentloaded", timeout=30000)
    else:
        page.goto(CART_URL_SCHOOL, wait_until="domcontentloaded", timeout=30000)
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

# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="구글시트 URL (export csv 가능해야 함)")
    ap.add_argument("--sheet-name", dest="sheet_name", default="",
                    help="구글시트 내 탭(시트)명. 지정 시 gviz/tq CSV로 받음.")
    ap.add_argument("--csv", help="로컬 CSV 경로")
    ap.add_argument("--account", choices=["school", "personal"], default="personal")
    ap.add_argument("--mode", choices=["dry", "run"], default="dry",
                    help="dry=첫 1건만 / run=전체")
    ap.add_argument("--id", help="S2B 아이디 (안전상 환경변수/프롬프트 권장)")
    ap.add_argument("--pw", help="S2B 비밀번호 (안전상 환경변수/프롬프트 권장)")
    ap.add_argument("--headless", action="store_true", help="브라우저 숨김")
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
    print(f"[sheet] 로드 {len(items)}건")
    for it in items[:5]:
        print(f"  preview: {it}")
    if not items:
        print("!! 품목 0건. 종료.")
        return

    if args.mode == "dry":
        items = items[:1]
        print(f"[mode] dry-run → 1건만 진행")

    # 2) 자격증명 로드
    sid, spw = load_credentials(args)
    if not sid or not spw:
        print("!! 자격증명 누락. 종료.")
        return

    # 3) 자동화
    storage_state = workdir / "storage_state.json"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=50)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = ctx.new_page()
        try:
            ok = login(page, args.account, sid, spw, recon_dir)
            if not ok:
                print("!! 로그인 실패. 종료.")
                return
            ctx.storage_state(path=str(storage_state))

            # 중복 방지: 로그인 직후 기등록 물품 확인
            print("\n[dup-check] 기등록 물품 확인 중...")
            registered_nos = get_registered_item_nos(page, args.account)
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
                results.append(add_one(page, it, i, recon_dir))

            # 최종 검증
            non_skip_items = [it for it in items if it["no"] not in registered_nos]
            found = verify(page, args.account, items, recon_dir)

            # CHECK 상태 재판정: 팝업 캡처 실패했지만 접수내역에 있으면 OK
            for r in results:
                if r["status"] == "CHECK":
                    if found.get(r["no"]):
                        r["status"] = "OK"
                        r["reason"] = "팝업 캡처 실패했으나 접수내역에서 확인됨"
                    else:
                        r["status"] = "FAIL"

            report = write_report(out_dir, results, found, args.account)
            print(f"\n[report] {report}")

            ok_cnt   = sum(1 for r in results if r["status"] == "OK" and found.get(r["no"]))
            skip_cnt = sum(1 for r in results if r["status"] == "SKIP")
            fail_cnt = sum(1 for r in results if r["status"] == "FAIL")
            print(f"[summary] OK={ok_cnt}  SKIP(중복)={skip_cnt}  FAIL={fail_cnt}  / 총{len(results)}건")
        finally:
            ctx.tracing.stop(path=str(recon_dir / "trace.zip"))
            browser.close()

if __name__ == "__main__":
    main()
