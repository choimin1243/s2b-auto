# Codex Login Confirmation

This repository uses a manual S2B login flow. The Python script cannot read
Codex chat messages directly, so Codex should translate the user's chat
confirmation into the local signal file that the script watches.

When an S2B automation run is waiting for manual login and the user sends a
Codex chat message containing exactly `로그인`, `완료`, or `로그인 완료`, create
`login_done.signal` in the repository root:

```powershell
Set-Content -Path .\login_done.signal -Value "done" -Encoding ASCII
```

After creating the file, keep monitoring the run until `storage_state.json`,
`recon/30_verify_list.*`, and `output/리포트_*.html` are produced, or until the
script reports a clear failure.

Do not ask for or store S2B credentials. The user must type credentials only in
the visible S2B browser window.
