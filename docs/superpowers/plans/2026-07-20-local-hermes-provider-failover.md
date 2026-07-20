# Local Hermes Provider Failover Implementation Plan

> **For implementation:** Use `superpowers:executing-plans` to execute this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure the local Wallet Guru Hermes gateway to use two ordered Anthropic setup tokens and fall back to OpenAI Codex `gpt-5.6-sol`, without ever using the local Claude Code OAuth credential.

**Architecture:** Hermes core owns same-provider rotation through its Anthropic credential pool and cross-provider switching through `fallback_providers`. The existing subscription plugin adds one suppression-aware resolver guard so an exhausted pool cannot bypass `suppressed_sources` and read Claude Code credentials directly. No proxy or additional daemon is introduced.

**Tech Stack:** Python 3.11, Hermes Agent 0.18.2, Hermes plugin API, Anthropic Messages API, OpenAI Codex OAuth, macOS launchd, pytest, Ruff, SSH.

## Global Constraints

- Change only the local default Hermes profile at `/Users/evgenii/.hermes`.
- Treat `root@176.223.139.23` and `/root/.hermes/profiles/jarvis` as read-only.
- Primary model remains `anthropic/claude-opus-4-8`.
- Anthropic order is `local-setup-primary` then `vps-setup-backup` with strategy `fill_first`.
- The local Claude Code OAuth source must remain suppressed and its Keychain/file credentials must remain untouched.
- Cross-provider fallback is exactly `openai-codex/gpt-5.6-sol`.
- Use a separate Hermes OpenAI device-code session; do not import `/Users/evgenii/.codex/auth.json`.
- Never place a setup token in Git, documentation, shell history, process arguments, logs, or unmasked terminal output.
- Do not add a proxy, router, daemon, or Hermes fork.
- Do not use TDD; implement the focused change, then run automated and live verification.

---

### Task 1: Make Claude Code suppression apply to the direct resolver

**Files:**
- Create: `credential_guard.py`
- Modify: `__init__.py`
- Modify: `plugin.yaml`
- Modify: `README.md`
- Modify: `after-install.md`
- Create: `tests/test_credential_guard.py`
- Modify: `tests/test_registration.py`

**Interfaces:**
- Consumes: `hermes_cli.auth.is_source_suppressed(provider_id, source)` and `agent.anthropic_adapter.resolve_anthropic_token()`.
- Produces: `make_suppression_aware_resolver(original, *, is_source_suppressed, pool_token_resolver) -> Callable[[], Optional[str]]` and `install_claude_code_suppression_guard() -> bool`.

- [ ] **Step 1: Add the suppression-aware resolver guard**

Create `credential_guard.py` with the following behavior:

```python
"""Prevent suppressed Claude Code credentials from bypassing Hermes pools."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Optional


_GUARD_MARKER = "_anthropic_subscription_claude_suppression_guard"


def make_suppression_aware_resolver(
    original: Callable[[], Optional[str]],
    *,
    is_source_suppressed: Callable[[str, str], bool],
    pool_token_resolver: Callable[[], Optional[str]],
) -> Callable[[], Optional[str]]:
    """Wrap Hermes token resolution while preserving normal unsuppressed behavior."""

    def resolve() -> Optional[str]:
        if not is_source_suppressed("anthropic", "claude_code"):
            return original()

        token = os.getenv("ANTHROPIC_TOKEN", "").strip()
        if token:
            return token

        pool_token = (pool_token_resolver() or "").strip()
        if pool_token:
            return pool_token

        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        return api_key or None

    setattr(resolve, _GUARD_MARKER, True)
    setattr(resolve, "__wrapped__", original)
    return resolve


def install_claude_code_suppression_guard() -> bool:
    """Install the wrapper once; return True only on the first installation."""

    import agent.anthropic_adapter as adapter
    from hermes_cli.auth import is_source_suppressed

    current = adapter.resolve_anthropic_token
    if getattr(current, _GUARD_MARKER, False):
        return False

    pool_resolver = getattr(adapter, "_resolve_anthropic_pool_token", None)
    if not callable(pool_resolver):
        raise RuntimeError(
            "Hermes Anthropic resolver has no callable _resolve_anthropic_pool_token"
        )

    adapter.resolve_anthropic_token = make_suppression_aware_resolver(
        current,
        is_source_suppressed=is_source_suppressed,
        pool_token_resolver=pool_resolver,
    )
    return True
```

The suppressed branch intentionally ignores `CLAUDE_CODE_OAUTH_TOKEN`: that variable is another Claude Code source and must not re-enable the credential behind the user's suppression choice.

- [ ] **Step 2: Register the guard and bump the patch version**

In `__init__.py`, set `__version__ = "0.1.2"` and call `install_claude_code_suppression_guard()` before `install_transport_override()`:

```python
from .credential_guard import install_claude_code_suppression_guard

install_claude_code_suppression_guard()
compatibility = install_transport_override()
```

In `plugin.yaml`, set:

```yaml
version: 0.1.2
```

- [ ] **Step 3: Add post-implementation guard tests**

Create `tests/test_credential_guard.py`:

```python
from hermes_anthropic_subscription_provider.credential_guard import (
    make_suppression_aware_resolver,
)


def test_unsuppressed_delegates_to_upstream(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    wrapped = make_suppression_aware_resolver(
        lambda: "claude-code-token",
        is_source_suppressed=lambda provider, source: False,
        pool_token_resolver=lambda: None,
    )
    assert wrapped() == "claude-code-token"


def test_suppressed_never_calls_upstream_or_uses_claude_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "forbidden-token")

    def forbidden():
        raise AssertionError("Claude Code resolver must not run")

    wrapped = make_suppression_aware_resolver(
        forbidden,
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: None,
    )
    assert wrapped() is None


def test_suppressed_prefers_explicit_anthropic_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_TOKEN", "explicit-setup-token")
    wrapped = make_suppression_aware_resolver(
        lambda: "forbidden",
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: "pool-token",
    )
    assert wrapped() == "explicit-setup-token"


def test_suppressed_uses_available_pool_token(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    wrapped = make_suppression_aware_resolver(
        lambda: "forbidden",
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: "pool-token",
    )
    assert wrapped() == "pool-token"
```

Extend `tests/test_registration.py` to assert that, after plugin registration, `agent.anthropic_adapter.resolve_anthropic_token` carries `_anthropic_subscription_claude_suppression_guard = True`.

- [ ] **Step 4: Document the guard and rollback semantics**

Update `README.md` and `after-install.md` to state:

```text
When `hermes auth remove anthropic claude_code` suppresses the Claude Code
source, the plugin also prevents Hermes's direct fallback resolver from reading
the macOS Keychain or ~/.claude/.credentials.json. The files are not modified.
Re-adding Anthropic OAuth clears the suppression and restores upstream behavior.
```

- [ ] **Step 5: Run automated verification**

Run:

```bash
python -m pytest -q
python -m ruff check .
```

Expected: all tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 6: Commit and publish plugin version 0.1.2**

Run:

```bash
git add credential_guard.py __init__.py plugin.yaml README.md after-install.md tests
git commit -m "fix: honor suppressed Claude Code credentials"
git tag v0.1.2
git push origin main
git push origin v0.1.2
```

Expected: `main` and tag `v0.1.2` are visible in the private GitHub repository.

---

### Task 2: Back up and quiesce the local gateway

**Files:**
- Read: `/Users/evgenii/.hermes/auth.json`
- Read: `/Users/evgenii/.hermes/config.yaml`
- Read: `/Users/evgenii/.hermes/.env`
- Create: `/Users/evgenii/.hermes/backups/provider-failover-<timestamp>/`
- Read only: `/root/.hermes/profiles/jarvis/config.yaml` on `root@176.223.139.23`
- Read only: `/root/.hermes/profiles/jarvis/auth.json` on `root@176.223.139.23`

**Interfaces:**
- Consumes: installed Hermes CLI and configured SSH access.
- Produces: timestamped local rollback files plus before-state hashes for the VPS Jarvis profile.

- [ ] **Step 1: Capture non-secret baseline state**

Run the installed Hermes CLI, not the development checkout:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway status
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins list
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth list anthropic
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth list openai-codex
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback list
```

Expected: commands show labels, providers, and statuses without printing raw tokens.

- [ ] **Step 2: Record VPS before-state hashes without changing it**

Run:

```bash
ssh root@176.223.139.23 \
  'sha256sum /root/.hermes/profiles/jarvis/config.yaml /root/.hermes/profiles/jarvis/auth.json 2>/dev/null; systemctl is-active hermes-gateway-jarvis'
```

Expected: two hashes and `active`. Save this command output in the execution notes, never in the repository.

- [ ] **Step 3: Create restrictive local backups**

Run with a task-specific variable:

```bash
umask 077
FAILOVER_BACKUP="/Users/evgenii/.hermes/backups/provider-failover-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$FAILOVER_BACKUP"
cp -p /Users/evgenii/.hermes/auth.json "$FAILOVER_BACKUP/auth.json"
cp -p /Users/evgenii/.hermes/config.yaml "$FAILOVER_BACKUP/config.yaml"
cp -p /Users/evgenii/.hermes/.env "$FAILOVER_BACKUP/.env"
find "$FAILOVER_BACKUP" -maxdepth 1 -type f -exec stat -f '%Sp %N' {} \;
```

Expected: three files exist and are readable only by the user.

- [ ] **Step 4: Stop only the local default gateway**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway stop
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway status
```

Expected: the local default service is stopped. Do not use `--all`.

---

### Task 3: Install the plugin update and build the Anthropic pool

**Files:**
- Modify through Hermes CLI: `/Users/evgenii/.hermes/auth.json`
- Modify through Hermes CLI: `/Users/evgenii/.hermes/config.yaml`
- Modify through Hermes plugin manager: `/Users/evgenii/.hermes/plugins/anthropic-subscription-provider/`
- Read only over SSH: `/root/.hermes/profiles/jarvis/.env`

**Interfaces:**
- Consumes: plugin release `v0.1.2`, the user-provided local setup token through masked input, and the existing VPS `ANTHROPIC_TOKEN` through an SSH pipe.
- Produces: an Anthropic `fill_first` pool ordered `local-setup-primary`, `vps-setup-backup`, with `claude_code` suppressed.

- [ ] **Step 1: Update and verify the installed plugin**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins update anthropic-subscription-provider
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins list
```

Expected: `anthropic-subscription-provider` is enabled at version `0.1.2`.

- [ ] **Step 2: Add the local setup token through masked input**

Run in a PTY:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  auth add anthropic --type api-key --label local-setup-primary
```

At `Paste your API key:`, provide the local setup token through the non-echoing prompt. Do not use `--api-key` and do not paste it into a shell command.

Expected: `Added anthropic credential ... "local-setup-primary"`.

- [ ] **Step 3: Stream the VPS setup token directly into the local Hermes store**

Use an SSH-to-stdin pipeline so the token is never rendered locally:

```bash
ssh root@176.223.139.23 "python3 -c 'from pathlib import Path; p=Path(\"/root/.hermes/profiles/jarvis/.env\"); rows=[line.partition(\"=\")[2].strip().strip(chr(34)).strip(chr(39)) for line in p.read_text().splitlines() if line.partition(\"=\")[0].strip()==\"ANTHROPIC_TOKEN\"]; assert len(rows)==1; print(rows[0])'" \
| /Users/evgenii/.hermes/hermes-agent/venv/bin/python -c '
import sys
import uuid
from agent.credential_pool import AUTH_TYPE_API_KEY, SOURCE_MANUAL, PooledCredential, load_pool

token = "".join(sys.stdin.read().split())
if not token.startswith("sk-ant-oat") or len(token) < 40:
    raise SystemExit("VPS ANTHROPIC_TOKEN was missing or had an unexpected format")
pool = load_pool("anthropic")
if any(entry.label == "vps-setup-backup" for entry in pool.entries()):
    raise SystemExit("vps-setup-backup already exists")
entry = PooledCredential(
    provider="anthropic",
    id=uuid.uuid4().hex[:6],
    label="vps-setup-backup",
    auth_type=AUTH_TYPE_API_KEY,
    priority=0,
    source=SOURCE_MANUAL,
    access_token=token,
    base_url="https://api.anthropic.com",
)
pool.add_entry(entry)
print("Added anthropic credential: vps-setup-backup")
'
```

Expected: only the label is printed. The VPS token never appears in output, files under the repository, or process arguments.

- [ ] **Step 4: Suppress Claude Code after all additions**

Run this after both `auth add` operations because adding a credential intentionally clears provider suppressions:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  auth remove anthropic claude_code
```

Expected: Hermes reports that `claude_code` is suppressed and explicitly says `/Users/evgenii/.claude/.credentials.json` remains untouched.

If the baseline listed any Anthropic entry other than `claude_code`, `local-setup-primary`, and `vps-setup-backup`, remove it by exact label with the same `hermes auth remove anthropic <label>` command before continuing.

- [ ] **Step 5: Set deterministic selection and clear stale statuses**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  config set credential_pool_strategies.anthropic fill_first
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  auth reset anthropic
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  auth list anthropic
```

Expected order:

```text
#1  local-setup-primary
#2  vps-setup-backup
```

No `claude_code` entry may appear.

- [ ] **Step 6: Verify suppression survives a fresh resolver load**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -c '
from agent.credential_pool import load_pool
from hermes_cli.auth import is_source_suppressed

pool = load_pool("anthropic")
labels = [entry.label for entry in pool.entries()]
assert labels == ["local-setup-primary", "vps-setup-backup"], labels
assert is_source_suppressed("anthropic", "claude_code")
print("ANTHROPIC_POOL_OK")
'
```

Expected: `ANTHROPIC_POOL_OK`.

---

### Task 4: Authenticate OpenAI Codex and configure the fallback chain

**Files:**
- Modify through Hermes CLI: `/Users/evgenii/.hermes/auth.json`
- Modify through Hermes CLI: `/Users/evgenii/.hermes/config.yaml`

**Interfaces:**
- Consumes: OpenAI device-code OAuth and the live/local Codex model catalog.
- Produces: a Hermes-owned `openai-codex` credential and one fallback entry targeting `gpt-5.6-sol`.

- [ ] **Step 1: Create a separate Hermes Codex OAuth session**

Run in a PTY:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  auth add openai-codex --label local-codex-fallback
```

Open the displayed OpenAI device URL and complete the device-code login. Do not import `/Users/evgenii/.codex/auth.json`.

Expected: `Added openai-codex OAuth credential ... "local-codex-fallback"`.

- [ ] **Step 2: Verify GPT-5.6 Sol is visible to Hermes**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -c '
from agent.credential_pool import load_pool
from hermes_cli.codex_models import get_codex_model_ids

entry = load_pool("openai-codex").select()
assert entry is not None
models = get_codex_model_ids(entry.access_token)
assert "gpt-5.6-sol" in models, models
print("CODEX_MODEL_OK")
'
```

Expected: `CODEX_MODEL_OK`.

- [ ] **Step 3: Replace the fallback chain through Hermes's manager**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback clear
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback add
```

In the picker select `OpenAI Codex`, then `GPT-5.6-Sol` (`gpt-5.6-sol`). The manager restores the primary Anthropic model after recording the fallback.

- [ ] **Step 4: Verify provider configuration and direct Codex inference**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback list
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  chat -q "Reply with exactly CODEX_FALLBACK_READY" -Q \
  --provider openai-codex -m gpt-5.6-sol
```

Expected: the list shows Anthropic as primary and exactly one Codex fallback; the direct response contains `CODEX_FALLBACK_READY`.

---

### Task 5: Exercise rotation, cross-provider fallback, terminal tools, and gateway restart

**Files:**
- Temporarily modify and then reset statuses in `/Users/evgenii/.hermes/auth.json`
- Read: `/Users/evgenii/.hermes/logs/agent.log`
- Read: `/Users/evgenii/.hermes/logs/errors.log`
- Read only: VPS Jarvis configuration and service state

**Interfaces:**
- Consumes: the completed Anthropic pool, suppression guard, Codex fallback, and installed gateway.
- Produces: live evidence that both failover levels and terminal tools work, followed by a healthy local gateway.

- [ ] **Step 1: Verify the primary local setup token with text and terminal**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  chat -q "Reply with exactly LOCAL_ANTHROPIC_READY" -Q \
  --provider anthropic -m claude-opus-4-8
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  chat -q "Use terminal once to run pwd, then reply with TERMINAL_OK and the path." -Q \
  --provider anthropic -m claude-opus-4-8 -t terminal
```

Expected: the first response contains `LOCAL_ANTHROPIC_READY`; the second contains `TERMINAL_OK` and a real local path.

- [ ] **Step 2: Mark only the primary credential exhausted and verify backup selection**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -c '
from agent.credential_pool import load_pool

pool = load_pool("anthropic")
first = pool.select()
assert first and first.label == "local-setup-primary"
next_entry = pool.mark_exhausted_and_rotate(
    status_code=429,
    error_context={"reason": "controlled_failover_test", "message": "controlled test"},
)
assert next_entry and next_entry.label == "vps-setup-backup"
print("ANTHROPIC_ROTATED_TO_VPS")
'
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  chat -q "Reply with exactly VPS_ANTHROPIC_BACKUP_READY" -Q \
  --provider anthropic -m claude-opus-4-8
```

Expected: `ANTHROPIC_ROTATED_TO_VPS` followed by a valid response containing `VPS_ANTHROPIC_BACKUP_READY`.

- [ ] **Step 3: Exhaust both Anthropic entries and verify actual Codex fallback with a tool call**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -c '
from agent.credential_pool import load_pool

pool = load_pool("anthropic")
current = pool.select()
assert current and current.label == "vps-setup-backup"
assert pool.mark_exhausted_and_rotate(
    status_code=429,
    error_context={"reason": "controlled_failover_test", "message": "controlled test"},
) is None
assert not pool.has_available()
print("ANTHROPIC_POOL_EXHAUSTED_FOR_TEST")
'
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
  chat -q "Use terminal once to run pwd. Then reply with CODEX_FAILOVER_TERMINAL_OK and the path." \
  -Q -t terminal
```

Expected: logs show activation of `gpt-5.6-sol via openai-codex`; the response contains `CODEX_FAILOVER_TERMINAL_OK` and a real local path. No log line may show `source=claude_code` or `Using Claude Code credentials` for this turn.

- [ ] **Step 4: Restore healthy Anthropic statuses and recheck primary selection**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth reset anthropic
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth list anthropic
```

Expected: both entries are healthy and `local-setup-primary` carries the selection marker.

- [ ] **Step 5: Restart only the local default gateway**

Run:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway start
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway status
sleep 5
tail -n 120 /Users/evgenii/.hermes/logs/agent.log
tail -n 120 /Users/evgenii/.hermes/logs/errors.log
```

Expected: the service is running, the subscription plugin is active, Telegram polling connects, and there are no new startup/auth errors.

- [ ] **Step 6: Prove VPS Jarvis was unchanged**

Repeat:

```bash
ssh root@176.223.139.23 \
  'sha256sum /root/.hermes/profiles/jarvis/config.yaml /root/.hermes/profiles/jarvis/auth.json 2>/dev/null; systemctl is-active hermes-gateway-jarvis'
```

Expected: hashes exactly match Task 2 and the service remains `active`.

- [ ] **Step 7: Record final non-secret evidence**

Capture:

```bash
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins list
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth list anthropic
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main auth list openai-codex
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback list
/Users/evgenii/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway status
```

Expected final state: plugin `0.1.2`, two ordered Anthropic setup credentials, one separate Codex OAuth credential, fallback `openai-codex/gpt-5.6-sol`, and a running local gateway.

## Rollback

If any final verification fails, stop the local gateway, restore `auth.json`, `config.yaml`, and `.env` from the Task 2 backup with `cp -p`, update or reinstall plugin version `v0.1.1`, start the gateway, and verify Telegram polling. Do not change the VPS during rollback.
