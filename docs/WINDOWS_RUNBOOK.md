# AlphaPulse Windows worker runbook

The public Mini App and API stay on Vercel. The permanent AUTO runtime runs only on this Windows host and accepts only DEMO execution. The browser talks to Vercel/Neon; it never receives the computer's residential IP or Pocket session.

## Layout

- `app/` — Git checkout and Python virtual environment.
- `config/worker.env` — local worker secrets; never committed.
- `logs/` — daily worker logs, retained for 14 days.
- `runtime/` — local runtime state.

The scripts resolve this layout from the repository location, so both `C:\AlphaPulse\app` and the non-admin fallback under the Codex workspace are supported.

## One-time preparation

1. Install current Python, Node.js LTS (with npm) and Git from their official sources.
2. Run `scripts\windows\bootstrap.ps1` in normal PowerShell.
3. Run `scripts\windows\install-service.ps1` once from PowerShell **as Administrator**.
4. Fill `config\worker.env`. Required values are `DATABASE_URL`, `ADMIN_ID`, `POCKET_OPTION_SSID`, and a random `WORKER_SHARED_SECRET` of at least 32 characters. Add `TELEGRAM_BOT_TOKEN` so scheduled VIP signals can be delivered. Keep `POCKET_OPTION_DEMO=true`.
5. Start the task: `Start-ScheduledTask -TaskName 'AlphaPulse Worker'`.
6. Check it with `scripts\windows\status.ps1` and inspect the newest file under `logs\`.

The installer creates a standard `AlphaPulseWorker` account, removes inherited access from the secret directory, gives the worker read-only access to code/secrets and modify access only to logs/runtime, registers one startup task with a 30-second delay, automatic restarts and single-instance policy, and disables AC sleep. Screen locking remains enabled.

## Updating

Run `scripts\windows\update.ps1`. It refuses to update when a session is active, a broker position is unresolved, or the checkout contains local changes. A successful update is fast-forward-only, reinstalls locked dependencies, builds the Mini App, and restarts the task.

## Recovery

- `ONLINE`: heartbeat is at most 10 seconds old.
- `DEGRADED`: heartbeat is 10–20 seconds old.
- `OFFLINE`: heartbeat is older than 20 seconds or missing.
- An order timeout moves execution to `UNKNOWN/RECONCILING`. Never resend it manually; confirm the broker result first.
- Do not run two workers for the same account. The Neon lease prevents a second worker from trading, while the Windows mutex prevents a duplicate local process.

## Security boundaries

- Do not open inbound router or Windows Firewall ports. The optional realtime server binds only to `127.0.0.1`; polling through Vercel is the safe default.
- Do not put `POCKET_OPTION_SSID` in Vercel, Neon, GitHub Actions, chat, screenshots, logs, or commits.
- Keep Windows Firewall and Defender enabled, BitLocker/device encryption on when supported, Windows Update automatic, RDP and SSH disabled unless accessed through a private VPN with MFA.
- Rotate the Pocket session, Telegram token, database password and worker shared secret immediately if any credential may have been exposed.
- BIOS auto-power-on after an outage is hardware-specific and must be enabled manually if the device supports it.

## Removing the task

Run `scripts\windows\uninstall-service.ps1` as Administrator. Add `-RemoveServiceUser` only when the dedicated local account should also be deleted. Configuration and logs are preserved by default.
