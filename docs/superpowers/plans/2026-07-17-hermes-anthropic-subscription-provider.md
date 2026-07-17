# Hermes Anthropic Subscription Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan inline. The owner
> explicitly requested implementation-first work followed by focused offline
> checks and real-agent E2E, rather than test-first development.

**Goal:** Deliver a private, one-command-installable Hermes plugin that fixes
native Anthropic OAuth/setup-token request identity and runs the real Hermes
agent on Claude Pro/Max subscription allowance.

**Architecture:** A normal top-level Hermes plugin registers a replacement for
the existing `anthropic_messages` transport while retaining Hermes's canonical
`anthropic` provider, credential handling, SDK client, streaming loop, and tool
dispatcher. The replacement delegates non-OAuth traffic unchanged and applies
request-scoped forward/reverse tool aliases only to native OAuth traffic.

**Tech Stack:** Python 3.11–3.13, Hermes Agent 0.18.2 transport/plugin APIs,
Anthropic Python SDK supplied by Hermes, PyYAML, pytest for focused checks,
Ruff, GitHub Actions.

## Global Constraints

- Keep provider id `anthropic` and api mode `anthropic_messages`.
- Do not modify the clean upstream Hermes worktree.
- Activate transformations only when Hermes passes `is_oauth=True`.
- Delegate API-key, Bedrock, Azure, and third-party compatible calls unchanged.
- No emitted OAuth tool name may match `^mcp_[^_]`.
- Use exact request-scoped reverse maps and reject alias collisions locally.
- Do not alter tool arguments, Bash commands, workdirs, URLs, or paths.
- Do not log or commit setup tokens, Authorization headers, or credential
  fingerprints.
- Do not request the optional Anthropic 1M-context beta in plugin v0.1.0.
- Target macOS ARM64, macOS Intel, and Linux x86_64 without native binaries.
- Use `claude-opus-4-8` for the real acceptance matrix.

---

### Task 1: Create the installable plugin package

**Files:**

- Create: `plugin.yaml`
- Create: `__init__.py`
- Create: `pyproject.toml`
- Create: `LICENSE`

**Implementation:**

- [ ] Declare manifest name `anthropic-subscription-provider`, version `0.1.0`,
  `manifest_version: 1`, and `kind: standalone`.
- [ ] Export `register(ctx)` from root `__init__.py`; import heavy Hermes modules
  only inside that function.
- [ ] Make `register(ctx)` call
  `compat.install_transport_override()` and log one redacted activation line.
- [ ] Configure Ruff for Python 3.11 and pytest discovery under `tests/`.
- [ ] Commit as `chore: scaffold Hermes provider plugin`.

**Quick check:**

```bash
python -m compileall -q .
python -c "import yaml; print(yaml.safe_load(open('plugin.yaml'))['kind'])"
```

Expected output: `standalone`.

### Task 2: Implement collision-safe OAuth tool identity

**Files:**

- Create: `tool_aliases.py`
- Create: `prompt_rewrite.py`
- Create: `tests/test_tool_identity.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ToolNameMaps:
    forward: dict[str, str]
    reverse: dict[str, str]

def to_oauth_wire_name(name: str) -> str: ...
def build_tool_name_maps(tools: list[dict] | None) -> ToolNameMaps: ...
def rewrite_explicit_tool_references(
    text: str, forward_map: dict[str, str]
) -> str: ...
```

**Implementation:**

- [ ] Map bare names to `mcp__<name>`, promote `mcp_<name>` to
  `mcp__<name>`, and preserve already-safe `mcp__<name>`.
- [ ] Extract OpenAI-format names from `tool["function"]["name"]` and reject
  non-string or empty names.
- [ ] Raise `ValueError` when two enabled internal names produce one wire name.
- [ ] Reject every output matching the forbidden single-underscore pattern.
- [ ] Port the syntax-aware prompt rewrite proven in the Hermes research branch:
  function calls, backticks, explicit tool labels, and natural-language tool
  directives only; protect slash/dot path and qualified-code boundaries.
- [ ] Add focused checks for the three skill tools, native MCP name, collision,
  `tests/read_file.py`, Hermes URL, and a terminal command/workdir payload.
- [ ] Commit as `feat: add OAuth tool identity mapping`.

**Quick check:**

```bash
python -m pytest -q tests/test_tool_identity.py
```

Expected result: all tool-identity checks pass.

### Task 3: Implement the Anthropic subscription transport

**Files:**

- Create: `transport.py`
- Create: `tests/test_transport_contract.py`

**Interface:**

```python
def make_subscription_transport(
    base_transport_class: type,
    *,
    compatibility_error: str = "",
) -> type:
    """Return the transport class registered for anthropic_messages."""
```

**Implementation:**

- [ ] Return a subclass of Hermes's currently registered Anthropic transport,
  marked with `_anthropic_subscription_provider = True`.
- [ ] In `build_kwargs`, clear stored maps and delegate byte-for-byte when
  `is_oauth` is false.
- [ ] For OAuth, deep-copy messages/tools before invoking upstream
  `build_kwargs`, build the exact name maps, and store the reverse map on the
  cached transport instance.
- [ ] Reconstruct non-identity system blocks from
  `convert_messages_to_anthropic()` so upstream's broad Hermes/Nous text
  substitutions do not corrupt valid URLs or identifiers; retain the upstream
  Claude Code OAuth identity block.
- [ ] Rewrite only explicit tool references in the reconstructed system blocks.
- [ ] Verify upstream tool-schema aliases equal the request map.
- [ ] Translate a specific Anthropic `tool_choice.name` to the same wire alias.
- [ ] Check schema names and replayed `tool_use` names for the forbidden
  single-underscore form before returning request kwargs.
- [ ] In `normalize_response`, invoke upstream normalization with heuristic
  prefix stripping disabled, then map returned tool calls through the exact
  reverse map.
- [ ] Preserve response text, reasoning, signed content-block order, finish
  reason, usage, tool IDs, and JSON arguments.
- [ ] Add focused contract checks for OAuth/non-OAuth, prompt alignment,
  response round trip, no caller mutation, tool choice, replay, and collisions.
- [ ] Commit as `feat: add Anthropic subscription transport`.

**Quick check:**

```bash
PYTHONPATH="/Users/evgenii/Desktop/My projects/hermes-agent" \
  python -m pytest -q tests/test_transport_contract.py
```

Expected result: all transport contracts pass without network access.

### Task 4: Register safely against current and future Hermes releases

**Files:**

- Create: `compat.py`
- Create: `tests/test_registration.py`
- Modify: `__init__.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class HostCompatibility:
    supported: bool
    hermes_version: str
    error: str = ""
    warning: str = ""

def inspect_host(base_transport: object) -> HostCompatibility: ...
def install_transport_override() -> HostCompatibility: ...
```

**Implementation:**

- [ ] Require Hermes `>=0.18.2` and feature-probe `get_transport`,
  `register_transport`, upstream `build_kwargs`, upstream `normalize_response`,
  and mutable normalized `ToolCall.name`.
- [ ] Build one synthetic offline OAuth request and require the upstream
  double-underscore alias contract expected by the wrapper.
- [ ] Force built-in transport discovery before registering the replacement.
- [ ] Make repeated plugin discovery idempotent; never subclass an already
  installed plugin transport again.
- [ ] On a behavioral compatibility failure, register a guarded wrapper that
  delegates non-OAuth calls but raises a clear local error for OAuth before a
  network call.
- [ ] Allow feature-compatible newer versions with a warning rather than a hard
  upper bound.
- [ ] Verify actual registry replacement and idempotence against current Hermes.
- [ ] Commit as `feat: add Hermes compatibility registration`.

**Quick check:**

```bash
PYTHONPATH="/Users/evgenii/Desktop/My projects/hermes-agent" \
  python -m pytest -q tests/test_registration.py
```

Expected result: registry returns a transport whose class carries
`_anthropic_subscription_provider = True`.

### Task 5: Verify installation in pristine upstream Hermes

**Files:**

- Create: `tests/test_pristine_install.py`
- Create during verification only: a clean Hermes worktree from
  `origin/main` and a temporary `HERMES_HOME`

**Implementation and verification:**

- [ ] Create an isolated clean worktree at fresh `origin/main`; do not merge it
  into the dirty research branch.
- [ ] Ensure the worktree has a usable Hermes environment and CLI executable.
- [ ] Install this repository with the actual command:

```bash
HERMES_HOME="<temporary-home>" hermes plugins install \
  "file:///Users/evgenii/Desktop/My projects/hermes-anthropic-subscription-provider" \
  --enable
```

- [ ] Start a fresh Python process, call Hermes plugin discovery, retrieve
  `get_transport("anthropic_messages")`, and assert the plugin marker.
- [ ] Build an offline native OAuth-shaped request with the real default tool
  schemas and assert schema/prompt identity agreement.
- [ ] Assert `git status --porcelain` is empty in the pristine Hermes worktree.
- [ ] Commit the integration check as `test: verify pristine Hermes install`.

**Expected result:** installation is one command, the transport is active after
restart/new process, and no upstream Hermes source file changes.

### Task 6: Add operator documentation and live E2E harness

**Files:**

- Create: `README.md`
- Create: `after-install.md`
- Create: `scripts/live_e2e.py`
- Create: `.gitignore`

**Implementation:**

- [ ] Document private GitHub SSH installation, enable/disable/update/remove,
  gateway restart, normal Anthropic provider selection, setup-token handling,
  rollback, compatibility errors, and token rotation.
- [ ] Explain that the plugin does not create, refresh, print, or store tokens.
- [ ] Implement a live harness that uses the installed plugin and real Hermes
  `AIAgent`, prints only case/status/tool/final markers, and refuses to run
  unless an explicit live-test flag is present.
- [ ] Add cases for no-tool, full tools, `skills_list`, temporary
  `skill_manage`, `read_file`, terminal/Bash, spaced Hermes path, synthetic MCP,
  replay, and auxiliary title/compression.
- [ ] Clean temporary skills, temporary directories, sessions, and MCP handlers
  in `finally` blocks.
- [ ] Ignore local environments, logs, `.env`, credential files, and live test
  output.
- [ ] Run a repository secret scan using pattern checks for setup-token and
  Authorization header shapes.
- [ ] Commit as `docs: add installation and live verification guide`.

### Task 7: Run the real acceptance matrix and Telegram gateway

**Verification:**

- [ ] Install the plugin into the user's real active `HERMES_HOME` using the
  actual plugin command.
- [ ] Restart Hermes so no old transport instance remains cached.
- [ ] Run the live matrix on `claude-opus-4-8`; use as many authorized Anthropic
  requests as required, but never dump request authorization.
- [ ] Require successful internal dispatch and final response for:
  `skills_list`, `skill_manage`, `read_file`, terminal/Bash, synthetic MCP, and
  replay.
- [ ] Require exact `cwd`, marker text, and exit code zero for both terminal path
  cases.
- [ ] Require auxiliary title/compression to complete without `extra usage`.
- [ ] Restart the existing Telegram gateway, verify `@wallet_guru_bot` connects,
  and send a plain response plus a harmless tool request through the bot.
- [ ] Inspect gateway logs for plugin activation, model
  `claude-opus-4-8`, successful tool dispatch, and absence of credential text.
- [ ] If any real case fails, diagnose the exact request surface, fix the plugin,
  repeat focused checks, reinstall, restart, and rerun the failed and baseline
  cases.

**Completion signal:** all positive cases finish without Anthropic's
third-party/extra-usage rejection and Telegram remains ready for the owner.

### Task 8: Publish the private repository and source release

**Files:**

- Create: `.github/workflows/ci.yml`
- Modify: `README.md` with the final private-repository install command

**Implementation and release:**

- [ ] Add credential-free CI on `ubuntu-24.04` x86_64, `macos-15` ARM64, and
  `macos-15-intel` x86_64, using Python 3.11 and 3.13 across the matrix.
- [ ] In CI, check out fresh `NousResearch/hermes-agent`, install its Python
  environment, run plugin checks, and exercise the real local-file plugin
  installer with a temporary `HERMES_HOME`.
- [ ] Run Ruff, compileall, focused pytest, `git diff --check`, and a repository
  secret scan locally before publishing.
- [ ] Switch GitHub CLI to `evgeniy270894` and create:

```bash
gh repo create evgeniy270894/hermes-anthropic-subscription-provider \
  --private --source=. --remote=origin --push
```

- [ ] Confirm repository visibility is private and default branch is `main`.
- [ ] Wait for CI and fix any actual platform incompatibility.
- [ ] Tag and push `v0.1.0` only after local live acceptance and CI both pass.
- [ ] Create a private GitHub release containing source installation notes; do
  not attach platform binaries because the plugin is pure Python.

## Final verification command set

```bash
python -m compileall -q .
python -m pytest -q
python -m ruff check .
git diff --check
git status --short
```

Final evidence must include the plugin commit/tag, clean check output, actual
installation location, clean upstream Hermes status, E2E case summary, gateway
status, Telegram bot identity, and private GitHub repository URL.
