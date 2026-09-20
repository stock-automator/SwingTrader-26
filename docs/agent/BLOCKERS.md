# Blockers Log

**Last updated:** 2026-09-20

## Active Blockers

None — all infrastructure blockers from Iteration 1 have been resolved.

## Resolved

### Telegram Consent Mechanism
- **Severity:** High (blocked autonomous notifications)
- **Description:** `hermes send` triggered script consent prompt, blocking automated Telegram notifications
- **Resolution:** `hermes send` works without consent for bot-token platforms (Telegram/Discord/Slack/Signal). No running gateway required. Used successfully to send iteration completion notification.
- **Date resolved:** 2026-09-20

### GitHub Push Credentials
- **Severity:** High (blocked CI verification)
- **Description:** No SSH/HTTPS credentials configured; `git push` failed with "Device not configured"
- **Resolution:** GitHub PAT provided by user, stored in `~/.git-credentials` (mode 600). Push successful. CI verified green (run #39).
- **Date resolved:** 2026-09-20

## Known Issues (Not Blocking)

### OmniRoute Claude Code Profile Setup
- **Severity:** Low
- **Description:** OmniRoute server requires API key for `setup-claude` (HTTP 401). No OmniRoute API key configured.
- **Impact:** Claude Code works standalone without OmniRoute routing. Not required for autonomous operation.
- **Resolution optional:** Generate OmniRoute API key if Claude Code → OmniRoute routing desired

### Groq API Key Invalid
- **Severity:** Low
- **Description:** Groq provider fails with "Invalid API key"
- **Impact:** One of four OmniRoute providers unusable. Claude + aihorde cover all needs.
- **Resolution optional:** User provides valid Groq API key

### Deploy Workflow GHCR Uppercase Issue
- **Severity:** Low (pre-existing, unrelated to current work)
- **Description:** Deploy workflow fails: `invalid tag "...SwingTrader-26-backend...": repository name must be lowercase`
- **Impact:** Docker images not pushed to GHCR. CI (test/lint/frontend) passes independently.
- **Resolution optional:** Fix workflow to use lowercase repo name for GHCR tags
