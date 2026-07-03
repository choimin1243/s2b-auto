# -*- coding: utf-8 -*-
"""
구글시트 또는 로컬 CSV에서 품목 리스트(`물품번호`, `수량`)를 읽어 정규화.
구글시트는 공개 export(csv) 방식만 지원 (서비스계정 미사용).

헤더 탐지:
- 첫 5행 중 물품번호/수량 관련 키워드가 가장 많이 포함된 행을 헤더로 자동 선택.
- 정확 일치 → 포함 일치 순으로 열 인덱스 탐색.
"""
import csv
import io
import re
import urllib.request
import urllib.parse
import ssl
from typing import List, Dict

# 열 이름 후보 (정확 일치 우선, 포함 일치 fallback)
HEADER_CANDIDATES = {
    "no": [
        "물품번호", "상품번호", "S2B번호", "goodsCode",
        "코드", "품번", "S2B코드", "물품코드", "제품번호", "품목코드",
    ],
    "qty": [
        "수량", "qty", "quantity", "주문수량", "발주수량", "요청수량", "개수",
    ],
    "name": [
        "물품명", "품명", "상품명", "name", "품목명", "제품명", "품목",
    ],
}


def _gsheet_csv_url(url: str, sheet_name: str = "") -> str:
    """구글시트 일반 URL → export csv URL 변환."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError(f"Not a Google Sheets URL: {url}")
    sid = m.group(1)
    if sheet_name:
        sn = urllib.parse.quote(sheet_name)
        return f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={sn}"
    gid = "0"
    g = re.search(r"[?&#]gid=(\d+)", url)
    if g:
        gid = g.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


def _fetch_csv_text(url: str, timeout: int = 30) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "utf-8-sig", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _find_col(header: list, key: str) -> int:
    """헤더 리스트에서 key에 해당하는 열 인덱스 반환. 없으면 -1."""
    candidates = HEADER_CANDIDATES[key]
    h_clean = [c.strip() for c in header]
    # 정확 일치
    for i, h in enumerate(h_clean):
        if h in candidates:
            return i
    # 포함 일치 (헤더 셀이 후보 문자열을 포함)
    for i, h in enumerate(h_clean):
        if any(c in h for c in candidates):
            return i
    return -1


def _find_header_row(rows: list) -> int:
    """처음 5행 중 물품번호/수량 키워드가 가장 많은 행을 헤더 행 인덱스로 반환."""
    all_kw = sum(HEADER_CANDIDATES.values(), [])
    best_row, best_score = 0, 0
    for i, row in enumerate(rows[:5]):
        score = sum(1 for cell in row if any(kw in str(cell).strip() for kw in all_kw))
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def _merge_duplicate_items(items: List[Dict]) -> List[Dict]:
    """같은 물품번호가 여러 행에 있으면 수량을 합산해 한 번만 담도록 병합한다."""
    merged = {}
    order = []
    for item in items:
        no = item["no"]
        if no not in merged:
            merged[no] = dict(item)
            merged[no]["rows"] = [item.get("row")]
            order.append(no)
            continue
        merged[no]["qty"] += item["qty"]
        merged[no]["rows"].append(item.get("row"))
        if not merged[no].get("name") and item.get("name"):
            merged[no]["name"] = item["name"]
    out = [merged[no] for no in order]
    duplicate_groups = [it for it in out if len(it.get("rows", [])) > 1]
    if duplicate_groups:
        print(f"  [merge] 같은 물품번호 {len(duplicate_groups)}종 병합")
        for it in duplicate_groups:
            print(f"    no={it['no']} rows={it['rows']} 합산수량={it['qty']} name={it.get('name','')}")
    return out


def _parse_rows(csv_text: str) -> List[Dict]:
    """CSV 텍스트 → 정규화된 품목 dict 리스트. 같은 물품번호는 수량 합산."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return []

    header_row_idx = _find_header_row(rows)
    header = [c.strip() for c in rows[header_row_idx]]
    print(f"  [sheet] 헤더 감지: {header_row_idx+1}행 → {header}")

    no_idx   = _find_col(header, "no")
    qty_idx  = _find_col(header, "qty")
    name_idx = _find_col(header, "name")

    if no_idx < 0 or qty_idx < 0:
        raise ValueError(
            f"헤더({header_row_idx+1}행)에 물품번호/수량 열을 찾을 수 없습니다.\n"
            f"현재 헤더: {header}\n"
            f"물품번호 후보: {HEADER_CANDIDATES['no']}\n"
            f"수량 후보: {HEADER_CANDIDATES['qty']}"
        )

    items = []
    for r_idx, row in enumerate(rows[header_row_idx + 1:], start=header_row_idx + 2):
        if not row or len(row) <= max(no_idx, qty_idx):
            continue
        no = (row[no_idx] or "").strip()
        qty_raw = (row[qty_idx] or "").strip().replace(",", "")
        name = (row[name_idx].strip() if name_idx >= 0 and name_idx < len(row) else "")

        if not no:
            continue  # 배송비 등 물품번호 없는 행 skip

        # 물품번호 형식 검증: 숫자와 하이픈만 허용
        if not re.fullmatch(r'[\d\-]+', no):
            print(f"  [skip] row {r_idx}: 물품번호 형식 불일치 '{no}'")
            continue

        try:
            qty = int(float(qty_raw))
        except ValueError:
            print(f"  [skip] row {r_idx}: 수량 파싱 실패 '{qty_raw}'")
            continue
        if qty <= 0:
            print(f"  [skip] row {r_idx}: 수량<=0 '{qty}'")
            continue

        items.append({"row": r_idx, "no": no, "qty": qty, "name": name})
    return _merge_duplicate_items(items)


def load_items_from_sheet(sheet_url: str, sheet_name: str = "") -> List[Dict]:
    csv_url = _gsheet_csv_url(sheet_url, sheet_name)
    text = _fetch_csv_text(csv_url)
    return _parse_rows(text)


def load_items_from_csv(path: str) -> List[Dict]:
    for enc in ("utf-8-sig", "utf-8", "euc-kr", "cp949"):
        try:
            with open(path, "r", encoding=enc) as f:
                return _parse_rows(f.read())
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"인코딩 자동감지 실패: {path}")


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    items = (load_items_from_sheet(arg) if arg.startswith("http") else load_items_from_csv(arg))
    print(f"총 {len(items)}건")
    for it in items:
        print(it)
