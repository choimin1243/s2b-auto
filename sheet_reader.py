# -*- coding: utf-8 -*-
"""
구글시트 또는 로컬 CSV에서 품목 리스트(`물품번호`, `수량`)를 읽어 정규화.
구글시트는 공개 export(csv) 방식만 지원 (서비스계정 미사용).
"""
import csv
import io
import re
import urllib.request
import urllib.parse
import ssl
from typing import List, Dict


def _gsheet_csv_url(url: str, sheet_name: str = "") -> str:
    """구글시트 일반 URL → export csv URL 변환.
    sheet_name이 주어지면 gviz/tq 엔드포인트로 탭 이름 기반 CSV를 받는다.
    그렇지 않으면 export?gid=... 사용 (URL의 #gid 또는 0).
    """
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
        # 리다이렉트 후 응답
        raw = r.read()
    # UTF-8 우선, 실패 시 EUC-KR 폴백
    for enc in ("utf-8", "utf-8-sig", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_rows(csv_text: str) -> List[Dict]:
    """CSV 텍스트 → 정규화된 품목 dict 리스트."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return []
    header = [c.strip() for c in rows[0]]

    def find_col(candidates):
        for i, h in enumerate(header):
            if any(c == h or c in h for c in candidates):
                return i
        return -1

    no_idx = find_col(["물품번호", "상품번호", "S2B번호", "goodsCode"])
    qty_idx = find_col(["수량", "qty", "quantity"])
    name_idx = find_col(["물품명", "품명", "상품명", "name"])
    if no_idx < 0 or qty_idx < 0:
        raise ValueError(f"헤더에 '물품번호'/'수량' 열이 필요. 현재: {header}")

    items = []
    for r_idx, row in enumerate(rows[1:], start=2):
        if not row or len(row) <= max(no_idx, qty_idx):
            continue
        no = (row[no_idx] or "").strip()
        qty_raw = (row[qty_idx] or "").strip().replace(",", "")
        name = (row[name_idx].strip() if name_idx >= 0 and name_idx < len(row) else "")
        if not no:
            continue  # 배송비 등 물품번호 없는 행 skip
        if not no.replace("-", "").isalnum():
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
    return items


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
