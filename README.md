# S2B Manual Login CLI

Google Sheet에 있는 S2B 물품번호/수량을 읽고, **같은 물품번호는 수량을 합산한 뒤**, **로그인은 사용자가 브라우저에서 직접 한 뒤**, 로그인 세션을 저장하고 자동으로 headless 모드로 전환해 물품을 담고 검증 리포트를 남깁니다.

## Windows 설치

PowerShell에서:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## 사용

### 1) 시트 파싱만 확인

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --preview-only
```

### 2) 1건만 실제 담기 테스트

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --account personal --mode dry --manual-login-then-headless
```

브라우저가 열리면 사용자가 직접 로그인하고, CLI 창에서 Enter를 누릅니다. 그러면 로그인 창은 닫히고 저장된 세션으로 headless 자동 담기가 이어집니다.

### 3) 전체 실행

```powershell
python s2b_auto.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" --account personal --mode run --manual-login-then-headless
```

## 주의

- 이 버전은 `--id`, `--pw` 옵션이 없습니다. 아이디/비밀번호를 코드나 명령행에 저장하지 않습니다.
- `--mode dry`는 로그인 후 첫 1건을 실제로 담아 검증합니다. 파싱만 보려면 `--preview-only`를 사용하세요.
- `--manual-login-then-headless`: 로그인만 보이는 브라우저에서 하고 이후 headless 자동 담기로 전환합니다.
- `--login-only`: 로그인 세션만 저장합니다.
- `--use-session --headless`: 저장된 세션으로 로그인 없이 headless 실행합니다.
- 같은 물품번호가 여러 행에 있으면 수량을 합산해 한 번만 담습니다.
- 전체 실행 전 현재 시트의 물품번호와 겹치는 기존 접수/담기 항목은 삭제한 뒤 합산 수량으로 다시 담습니다. 이전 dry 테스트 수량이 남아 있어도 최종 수량이 맞게 하기 위한 동작입니다.
- 결과는 `recon/` 스크린샷/HTML/trace와 `output/리포트_*.html`에 저장됩니다.
