# Hermes Anthropic Subscription Provider Plugin — Design

Date: 2026-07-17  
Status: approved for implementation  
Target repository: `evgeniy270894/hermes-anthropic-subscription-provider` (private)  
Target Hermes baseline: `NousResearch/hermes-agent` `0.18.2`, verified through
`origin/main` commit `780e0980773a875322abd720e5e126a4fe448e7b`

## 1. Objective

Build a small, separately installable Hermes plugin that lets native Anthropic
OAuth/setup-token requests use Claude Pro/Max subscription allowance without
maintaining a Hermes fork or running a local proxy.

The finished result must:

- install into an unmodified Hermes checkout with `hermes plugins install`;
- keep the canonical provider id `anthropic` so Hermes continues to own token
  resolution, Bearer authentication, model selection, SDK clients, streaming,
  conversation state, and tool execution;
- correct the OAuth request identity mismatch that empirically sends Hermes
  requests to Anthropic's `extra usage` rejection path;
- preserve exact internal tool names inside Hermes while using accepted aliases
  only on the Anthropic wire;
- work in CLI, one-shot, gateway, Telegram, skills, MCP, terminal/Bash, replay,
  and auxiliary-task paths;
- leave Anthropic API-key requests and third-party Anthropic-compatible
  providers unchanged;
- never log, persist, commit, or expose an OAuth/setup token.

## 2. Evidence and constraints

The empirical investigation is preserved in:

`/Users/evgenii/Desktop/My projects/real-estate/galitsin/ANTHROPIC-OAUTH-EXTRA-USAGE-INVESTIGATION.md`

Its decisive findings are:

1. A wire tool name beginning with single-underscore `mcp_` triggers the
   subscription rejection; `mcp__...` is accepted.
2. The bare trio `skill_manage`, `skill_view`, and `skills_list` triggers the
   rejection when exposed together.
3. Hermes already aliases OAuth tool schemas and replayed tool calls, but its
   generated system instructions still mention the internal tool names. The
   resulting mismatch, for example schema `mcp__skill_manage` versus prompt
   reference `skill_manage(...)`, is the reproduced root cause.
4. Rewriting the explicit prompt reference with the same wire alias made a real
   full Hermes request and a real tool round trip succeed.
5. `Hermes`, `OpenAI`, `Nous Research`, Hermes URLs, local paths containing
   `hermes-agent`, Bash commands, and SDK/User-Agent fingerprints were not
   independent triggers in isolated A/B tests.
6. Billing metadata and additional beta headers are compatibility hardening,
   not substitutes for consistent tool identity.

Hermes imposes these architectural constraints:

- `ProviderProfile` is declarative and does not own the request/response path.
- `api_mode` is restricted to a fixed core allow-list, so a new custom mode
  cannot be selected without changing Hermes.
- native setup-token handling is enabled only when the provider id is exactly
  `anthropic` and the token is recognized as native OAuth.
- `agent.transports.register_transport()` allows a plugin to replace the class
  registered for `anthropic_messages`.
- transport instances are cached per `AIAgent`, allowing request-scoped alias
  state to survive from request construction to response normalization.
- the generic plugin installer clones repositories to
  `$HERMES_HOME/plugins/<name>`, while the separate model-provider loader scans
  `$HERMES_HOME/plugins/model-providers/<name>`. A repository declared only as
  `kind: model-provider` therefore does not provide the desired one-command
  standalone installation layout.

## 3. Considered approaches

### 3.1 Selected: standalone plugin registering an Anthropic transport override

The plugin is installed and enabled through the normal generic plugin manager.
Its `register(ctx)` function forces the built-in Anthropic transport to load,
runs compatibility probes, then registers the plugin transport under the
existing `anthropic_messages` mode.

Advantages:

- one-command installation and update;
- no Hermes source edits;
- no extra process or port;
- retains all native Hermes authentication, streaming, and dispatch behavior;
- pure Python and therefore no platform-specific binary build;
- can delegate every non-OAuth request to the original implementation.

Trade-off: the plugin depends on Hermes transport APIs and must feature-probe
them on each load.

### 3.2 Rejected: pure model-provider directory override

This matches Hermes's provider registry conceptually, but the standard plugin
installer does not place a standalone repository in the nested directory the
provider loader scans. Solving that would require a special installer, manual
file movement, or occupying the shared `model-providers` category directory.

### 3.3 Rejected: proxy, runtime-wide monkeypatch, or full fork

A proxy adds process lifecycle, networking, secret forwarding, and packaging
complexity. A full fork permanently owns all upstream merges. A runtime-wide
function monkeypatch would cover otherwise inaccessible internals but is more
fragile than the existing transport registry. None is required for the proven
main-agent request path.

## 4. Architecture

The plugin intentionally keeps the built-in `anthropic` provider profile. A
duplicate profile would copy authentication/catalog logic without gaining a
request hook and could drift from Hermes. The plugin instead replaces the
provider's transport layer, which is the component that actually owns message,
tool, request, and response conversion.

Expected repository layout:

```text
hermes-anthropic-subscription-provider/
├── __init__.py
├── plugin.yaml
├── compat.py
├── transport.py
├── tool_aliases.py
├── prompt_rewrite.py
├── README.md
├── after-install.md
├── LICENSE
├── pyproject.toml
├── tests/
│   ├── test_compat.py
│   ├── test_registration.py
│   ├── test_tool_aliases.py
│   ├── test_prompt_rewrite.py
│   ├── test_transport.py
│   └── test_pristine_hermes_integration.py
└── scripts/
    └── live_e2e.py
```

`plugin.yaml` uses `kind: standalone` so Hermes imports `register(ctx)` from
the installed top-level directory. The implementation still behaves as a
model-provider extension; the manifest kind only selects the correct loader.

Registration order:

1. Generic Hermes plugin discovery imports the plugin before constructing an
   agent for CLI, one-shot, dashboard, cron, or gateway work.
2. The plugin imports/instantiates the built-in `AnthropicTransport`, ensuring
   Hermes has completed its native transport discovery.
3. Compatibility probes validate the host version and expected behavior.
4. The plugin registers `SubscriptionAnthropicTransport` for
   `anthropic_messages`.
5. Every subsequently created agent caches the plugin transport.
6. Enabling, disabling, installing, or updating requires a gateway/process
   restart; hot-swapping an already cached agent is out of scope.

## 5. Components and responsibilities

### 5.1 `compat.py`

Checks:

- Hermes version is at least `0.18.2`;
- `register_transport`, `get_transport`, `ProviderTransport`, and the built-in
  `AnthropicTransport` exist;
- the transport accepts the expected `build_kwargs` and `normalize_response`
  contract;
- an offline synthetic OAuth build produces the expected double-underscore
  tool alias shape;
- normalized tool calls remain mutable `ToolCall` objects.

If the basic APIs exist but a behavioral probe fails, the plugin registers a
guarded transport: non-OAuth calls delegate to Hermes, while OAuth calls fail
locally with a clear compatibility error before any network request. This is
safer than silently falling back to a known-bad OAuth request shape.

Newer Hermes releases are allowed when feature probes pass. The plugin records
the newest tested commit/version in its own metadata and warns on an untested
newer host without blocking it solely by version number.

### 5.2 `tool_aliases.py`

Builds both maps for every request:

```text
internal name -> OAuth wire name
OAuth wire name -> internal name
```

The proven mapping is retained:

```text
skill_manage         -> mcp__skill_manage
read_file            -> mcp__read_file
mcp_linear_get_issue -> mcp__linear_get_issue
mcp__already_safe    -> mcp__already_safe
```

Requirements:

- no emitted wire name may match `^mcp_[^_]`;
- mapping must be injective for the enabled request tool set;
- if two internal names collapse to one wire name, raise a local `ValueError`
  listing only the conflicting names, never request contents or credentials;
- callers' tool dictionaries and nested schemas must not be mutated;
- exact request-scoped reverse mapping wins over guesses from the global tool
  registry.

### 5.3 `prompt_rewrite.py`

Rewrites only explicit references to tools present in the request map. Safe
contexts include:

- function-call syntax such as `skill_manage(...)`;
- backtick-quoted identifiers;
- explicit tool labels/directives used by Hermes's prompt builder;
- underscore-containing identifier tokens with proper boundaries.

It must not blindly replace ordinary words such as `memory`, `process`, or
`terminal`, and must not alter paths, URLs, code-qualified names, tool
arguments, or user content. The same function is applied to all text blocks in
the Anthropic `system` field after Hermes has built the native request.

### 5.4 `SubscriptionAnthropicTransport`

The class subclasses/wraps Hermes's built-in transport.

For `is_oauth=False` it clears any prior alias state and delegates without
semantic changes.

For `is_oauth=True` it:

1. Deep-copies only data it will modify.
2. Builds and collision-checks the request alias maps from the original tools.
3. Calls the upstream OAuth request builder so model normalization, vision,
   thinking/reasoning, signed thinking replay, prompt caching, output limits,
   fast mode, and future upstream fixes remain owned by Hermes.
4. Reconstructs the request's non-identity system blocks from the original
   converted system content. This retains Hermes's upstream OAuth identity
   block but reverses its broad product-name substitutions, which otherwise
   corrupt valid Hermes URLs and identifiers despite not being a confirmed
   classifier requirement.
5. Verifies that upstream schema/history aliases agree with the computed map.
6. Rewrites explicit system-prompt tool references using the same map.
7. Maps a specific `tool_choice` to the wire alias.
8. Asserts the forbidden single-underscore invariant before returning kwargs.
9. Saves the reverse map on the cached transport instance for this serial agent
   request.

The initial plugin deliberately does not replace Hermes's complete beta-header
set or client identity. The live matrix proved those fields are not the root
cause, and leaving them upstream-owned reduces expiry/version drift. It also
does not request the optional 1M-context beta, avoiding subscription plans that
reject that feature. Billing-header/cache-TTL parity can be added later as an
independent, tested optimization, not mixed into the correctness fix.

For response normalization, the plugin first invokes the upstream normalizer
without heuristic prefix stripping, preserving text, reasoning, signed block
order, stop reasons, and usage. It then rewrites each returned tool name through
the exact request map. Unknown names remain unknown and are handled by Hermes's
existing dispatcher error path.

Hermes serializes turns for an agent/session, which makes one alias map per
cached transport instance sufficient. Tests will lock in this assumption; a
future concurrent-per-agent host contract must add a request identifier before
the plugin claims compatibility.

### 5.5 Auxiliary calls

Current Hermes auxiliary Anthropic calls build requests directly through
`agent.anthropic_adapter` and use the transport registry only for response
normalization. They do not carry the full default tool set or Hermes's
skill-management system guidance, so the reproduced classifier mismatch is
absent from normal title/compression/search calls.

The plugin will not install a global function monkeypatch merely for theoretical
parity. Instead, live acceptance requires native setup-token auxiliary title
and compression calls to pass. If a future Hermes release begins sending agent
tools through that bypass path, the compatibility probe/test suite will mark
that release unsupported until Hermes exposes a transport hook or a separately
reviewed narrow adapter is added.

## 6. Request and tool-call data flow

```text
Hermes messages + internal tool schemas
    -> built-in Anthropic OAuth conversion
    -> plugin collision/invariant validation
    -> plugin system/tool_choice identity alignment
    -> Anthropic Messages API
    -> Anthropic response with OAuth wire tool name
    -> built-in response normalization (no heuristic stripping)
    -> plugin exact reverse map
    -> original Hermes internal name
    -> existing registry and dispatcher
    -> tool result replayed through the same forward map
```

Only tool identifiers are translated. Tool arguments, including terminal
commands and absolute paths containing `Hermes Agent` or `hermes-agent`, remain
byte-for-byte equivalent at the request-shaping boundary.

## 7. Error handling and security

- Alias collisions and forbidden names fail before the HTTP call.
- Compatibility failures block only native OAuth request creation; API-key and
  third-party requests retain upstream behavior.
- The plugin never reads or prints the raw token. Hermes remains the credential
  owner.
- Test helpers receive live credentials only through the existing local Hermes
  environment or a dedicated opt-in variable and redact all authorization
  headers.
- No request/body dump is enabled during live tests.
- Fixtures contain synthetic tokens only.
- Logs may contain model, provider, test case, status code/category, tool name,
  and response marker, but never credential values, fingerprints, headers, or
  complete private prompts.
- A setup-token HTTP 401 is surfaced as requiring token replacement; the plugin
  does not attempt to refresh a non-refreshable setup token.
- An `extra usage` response remains an empirical compatibility/billing signal,
  not proof that the user must buy credits.

## 8. Installation and user experience

Private-repository installation uses an authenticated Git URL, preferably SSH:

```bash
hermes plugins install \
  git@github.com:evgeniy270894/hermes-anthropic-subscription-provider.git \
  --enable
hermes gateway restart
```

The user then selects the normal Anthropic provider and existing setup-token
flow. No new provider name, base URL, proxy port, binary, or duplicate token
store is introduced.

README and `after-install.md` cover:

- private GitHub access prerequisites;
- setup-token provisioning through `claude setup-token`/Hermes's existing
  Anthropic setup;
- enable, disable, update, and remove commands;
- required restart;
- compatibility diagnostics;
- token rotation guidance;
- rollback by disabling/removing the plugin.

## 9. Testing strategy

### 9.1 Offline unit and contract tests

Tests are written before implementation and must cover:

1. Native and MCP name forward mapping.
2. Exact reverse mapping.
3. Single-underscore invariant.
4. Collision detection.
5. All three skill-tool schemas use wire aliases.
6. System references use those same aliases.
7. Apart from explicit tool references, original system text, paths, URLs,
   ordinary prose, and tool arguments are unchanged.
8. Specific `tool_choice` mapping.
9. Replayed `tool_use` mapping.
10. Non-OAuth passthrough equality.
11. API-key and third-party endpoint passthrough.
12. No caller-owned message/schema mutation.
13. Response reasoning/signed-block preservation.
14. Registration ordering and last-writer-wins transport behavior.
15. Compatible and incompatible host probes.
16. Secret-redaction invariants.

### 9.2 Pristine Hermes integration

A separate clean worktree is created from the newest `origin/main`, with a
temporary `HERMES_HOME`. The plugin is installed through the real
`hermes plugins install <local-file-or-git-url> --enable` path, Hermes starts,
and the test asserts that a newly constructed `AIAgent` receives the plugin
transport while the source checkout remains unmodified.

### 9.3 Opt-in live setup-token E2E

Use model `claude-opus-4-8` and real Hermes paths, never a payload-only mock.
Required cases:

1. Minimal no-tool `OK`.
2. Full default 28-tool request without a requested tool.
3. `skills_list` round trip.
4. Temporary `skill_manage` create round trip with cleanup.
5. `read_file` against the pristine Hermes checkout.
6. Real `terminal` call executing `bash -lc` in a path containing
   `hermes-agent`.
7. Real `terminal` call in a temporary absolute path containing literal
   `Hermes Agent` and spaces.
8. Synthetic registered `mcp_diag_echo` handler round trip.
9. Second-turn replay of a prior tool call/result.
10. At least one auxiliary title call and one compression/summary call.
11. Telegram gateway startup, bot connection, a plain response, and a harmless
    tool call from the user's existing bot.

Every positive case must show no `extra usage` rejection, correct final marker,
correct internal tool name, and successful local dispatch. Terminal cases also
require exact working directory, marker output, and exit code zero.

### 9.4 Cross-platform CI

Credential-free tests run on:

- macOS ARM64;
- macOS Intel where the CI provider still offers an x86 runner;
- Linux x86_64;
- Python versions supported by Hermes (`3.11` through `3.13`).

No binary artifact is required because the plugin is pure Python. A tagged
source release is the distributable artifact.

## 10. Delivery and completion criteria

The work is complete only when all of the following are true:

- private GitHub repository exists under `evgeniy270894`;
- design and implementation plans are committed;
- plugin implementation and tests are committed on `main`;
- offline tests and static checks pass;
- installation through the actual Hermes plugin command succeeds in a clean
  `HERMES_HOME` against fresh upstream Hermes;
- the required live E2E matrix passes with the real setup token;
- the installed local Hermes gateway and Telegram bot run with
  `claude-opus-4-8` through the plugin;
- no Hermes core file is modified in the clean integration environment;
- repository history and logs contain no secret;
- version `v0.1.0` is tagged and pushed only after verification;
- README contains reproducible install, update, rollback, and troubleshooting
  instructions.

The existing modified Hermes research branch remains local evidence/reference;
it is not the distributed solution.
