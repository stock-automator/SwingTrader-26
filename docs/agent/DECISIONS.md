# Decisions Log

**Last updated:** 2026-09-20

## 2026-09-20

### Telegram Integration
- **Decision:** Use `hermes send` CLI for outbound notifications
- **Rationale:** `hermes send` works without Gateway consent for bot-token platforms; no running gateway required
- **Result:** ✅ Working — sent iteration completion notification

### GitHub Credentials
- **Decision:** Use provided PAT, store in `~/.git-credentials` (mode 600)
- **Rationale:** Need push access for CI verification; PAT provided by user
- **Result:** ✅ Working — push successful, CI green (run #39)

### Groq Provider
- **Decision:** Mark as broken, do not use until credential rotated
- **Rationale:** `omniroute test groq` returned "Invalid API key"
- **Action required:** User must provide valid Groq API key or we drop Groq from routing

### Claude Code Skills
- **Decision:** Do not install marketplace skills — evaluate and reject
- **Rationale:** 
  - code-review: PR-focused, requires `gh` CLI, not applicable to direct repo work
  - code-simplifier: JavaScript-focused, not applicable to Python repo
  - feature-dev: Interactive workflow requiring user approval at multiple steps, not autonomous
  - Project's existing `code_reviewer.md` agent is purpose-built for this repo's quant context
- **Result:** No skills installed, existing reviewer agent retained

### OmniRoute Routing
- **Decision:** Claude Code works standalone; OmniRoute Claude Code profiles not configured
- **Rationale:** 
  - OmniRoute server requires API key for `setup-claude` (HTTP 401)
  - No OmniRoute API key configured (only a Groq test key exists)
  - Claude Code functions independently without OmniRoute routing
  - Hermes (this session) already uses OmniRoute → Claude for model calls
- **Result:** Not required — no action needed

### First Iteration
- **Decision:** Implement short-position support for backtest engine
- **Rationale:** Well-documented gap in AGENTS.md, bounded scope, high value, low risk
- **Result:** ✅ Complete — pushed, CI green (run #39), 856 tests passing
