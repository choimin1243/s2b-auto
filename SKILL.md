---
name: s2b
description: "S2B(학교장터, www.s2b.kr) 자동화 스킬. 구글시트의 물품번호·수량 리스트를 받아 자동 로그인 → 검색 → 상세페이지 [담기] 까지 수행. s2b, 학교장터, 견적, 물품 자동담기 요청 시 사용."
---

# S2B 자동화 스킬

S2B(www.s2b.kr) 학교장터에서 구글시트 기반으로 물품을 자동 탐색·담기까지 수행한다. Playwright(Chromium) 사용. 로그인은 사용자 계정 종류(수요기관/개인이용자)와 관계없이 동작.

## ⚠️ 매우 중요 — 사용 전 필독

**개인이용자 계정**의 상세페이지 [담기] 버튼은 단순 장바구니가 아니라 **실제 견적서 접수**(공급업체에 견적요청 발송)이다. 한번 [담기] = 한번 견적 접수.

- **수요기관(학교) 계정**: 진짜 장바구니(`Tomu400.do`) 사용 가능 → 안전한 add/remove 가능
- **개인이용자 계정**: 장바구니 미지원, [담기]는 즉시 견적접수 → **취소는 `선택물품함`에서 수동/자동 삭제 필요**

스킬 실행 전 사용자에게 계정 종류를 반드시 확인하고, 개인이용자라면 **1건만 dry-run** 후 사용자 승인을 받고 나머지 진행할 것.

## 입력

1. **로그인 자격증명** — 다음 중 하나로 받는다. 코드/메모리에 저장 금지.
   - 환경변수 `S2B_ID`, `S2B_PW`
   - 같은 폴더의 `.env` 파일 (`S2B_ID=...` / `S2B_PW=...`)
   - CLI 인자 `--id`, `--pw`
   - 인자 모두 비면 `getpass`로 안전 입력 프롬프트
2. **계정 종류** — `--account school|personal` (기본 personal)
3. **품목 리스트** — 다음 중 하나
   - 구글시트 URL: `--sheet "https://docs.google.com/spreadsheets/d/.../edit"`
     (시트는 "공개 링크로 보기" 권한이거나 export csv 가능 상태여야 한다)
   - 시트(탭) 이름 지정 시: `--sheet-name "발주_4월"` 추가 (gviz/tq CSV로 받음).
     `--sheet-name` 미지정이면 URL의 `#gid` 또는 첫 시트.
   - 로컬 CSV: `--csv path.csv`
   - 시트 스키마: 헤더에 `물품번호`, `수량` 열 필수. `물품명`/`옵션`/`비고` 등은 무시되거나 로깅용
4. **동작 모드** — `--mode dry|run` (기본 dry: 1건만)

## 실행

```bash
cd <skill_dir>
pip install -r requirements.txt
python -m playwright install chromium  # 1회

# dry-run (1건만)
python s2b_auto.py --sheet "<gsheet-url>" --account personal --mode dry

# 전체 실행 (사용자 승인 후)
python s2b_auto.py --sheet "<gsheet-url>" --account personal --mode run
```

## 흐름 (자동화 단계)

1. **로그인**
   - `https://www.s2b.kr/S2BNCustomer/Login.do` 진입
   - 계정에 맞는 탭 활성화 (`a[href='#sclogin']` 또는 `a[href='#prlogin']`)
   - 폼 입력: `form[name='<form>'] input[name='uid'|'pwd']`
   - 제출: `page.evaluate("retrieveLogin2('<form>', <idx>)")` (school=1, personal=2)
   - 비번변경 안내 페이지(`pwd_changeinfo.jsp`) 도달 시 `page.evaluate("modifyNext()")`
   - 성공 판정: URL이 `Login.do`/`pwd_changeinfo`가 아님

2. **시트 → 품목 리스트 정규화**
   - 헤더에서 `물품번호`/`수량` 열 인덱스 추출
   - 빈 행, 물품번호 없는 행(예: 배송비) 자동 스킵
   - 수량 정수 변환, 0/음수는 거부

3. **품목별 처리**
   - 상세페이지 직링크: `/S2BNCustomer/rema100.do?forwardName=detail&f_re_estimate_code={no}`
   - 수량 입력: `#qnt`
   - 담기 호출: `page.evaluate("fnSave()")`
   - confirm("물품견적서를 접수하시겠습니까?")는 `page.on("dialog", ...)`로 자동 accept
   - 새 popup 창(`remc100.do`)이 "견적서가 접수되었습니다." 표시
   - 결과는 `recon/24_popup_<idx>.png`/.html 로 저장 (검증용)

4. **검증 (개인이용자)**
   - `https://www.s2b.kr/S2BNCustomer/remc100.do?forwardName=estimateList` 로 이동
   - HTML에 우리 물품번호가 존재하는지 검사 → 모두 존재하면 PASS

5. **검증 (수요기관)**
   - `https://www.s2b.kr/S2BNCustomer/Tomu400.do?forwardName=list` 로 이동
   - 행 파싱하여 입력 vs 실제 diff (물품번호+수량 모두 일치)

## 산출물

- `recon/*.png`, `recon/*.html` — 단계별 스크린샷·DOM
- `recon/trace.zip` — Playwright trace (`playwright show-trace recon/trace.zip`)
- `output/리포트_YYYYMMDD_HHMM.html` — 사람이 읽기 좋은 결과 표
- `storage_state.json` — 다음 실행 시 세션 재사용 (만료되면 자동 재로그인)

## 주의

- 로그인 정보는 절대 코드/메모리/리포트에 기록하지 않는다.
- 구글시트의 행에 토큰/비밀번호가 우연히 들어있을 수 있으니, 시트 첫 1회 읽기 후 데이터 미리보기를 사용자에게 보여주고 진행한다.
- 같은 계정으로 여러 곳에서 동시 로그인 시 세션 충돌 가능 → 사용자 양해 필요.
- S2B는 EUC-KR 페이지가 일부 있다. urllib로 직접 받을 때는 `decode('euc-kr', errors='replace')`.

## 셀렉터·URL 레퍼런스 (트러블슈팅용)

| 단계 | URL/셀렉터 |
|---|---|
| 로그인 | `/S2BNCustomer/Login.do` |
| 탭 활성화 | `a[href='#sclogin']` / `a[href='#prlogin']` / `a[href='#splogin']` |
| 로그인 폼 | `form[name='school_loginForm'/'personal_loginForm'/'vendor_loginForm']` |
| ID/PW 인풋 | `input[name='uid']`, `input[name='pwd']` (각 폼 안) |
| 제출 함수 | `retrieveLogin2('<form>', <1\|2\|3>)` |
| 비번변경 패스 | `modifyNext()` |
| 검색 | `/S2BNCustomer/S2B/scrweb/remu/rema/searchengine/s2bCustomerSearch.jsp?actionType=MAIN_SEARCH&searchQuery={no}&...&locationGbn=all` |
| 상세 | `/S2BNCustomer/rema100.do?forwardName=detail&f_re_estimate_code={no}` |
| 수량 인풋 | `#qnt` |
| 담기 함수 | `fnSave()` (개인이용자에선 견적 접수임) |
| 접수내역(개인) | `/S2BNCustomer/remc100.do?forwardName=estimateList` |
| 장바구니(학교) | `/S2BNCustomer/Tomu400.do?forwardName=list` |
