# Local Hermes Provider Failover Design

Date: 2026-07-20  
Scope: local default Hermes profile used by the Wallet Guru Telegram bot  
Out of scope: the Jarvis profile and services on the VPS

## Objective

Keep the local Hermes gateway responsive when an Anthropic subscription
credential reaches a usage or rate limit. Use two independent Anthropic setup
tokens in a deterministic order, then switch to OpenAI Codex through Hermes's
native cross-provider fallback.

The solution must use Hermes's built-in credential pool and
`fallback_providers` mechanisms. It must not introduce a proxy, router, or
additional background service.

## Final Provider Chain

The local agent keeps `anthropic` and `claude-opus-4-8` as its primary route.
Hermes evaluates credentials and providers in this order:

1. A local Anthropic setup token supplied specifically for the local agent.
2. The setup token used by the VPS Jarvis deployment, which belongs to a
   different Anthropic account.
3. OpenAI Codex OAuth using model `gpt-5.6-sol`.

The local Claude Code OAuth credential is not part of the chain. Its
`claude_code` source must be suppressed in the local Hermes auth store so that
automatic credential discovery cannot silently restore it on a later gateway
restart. The plugin also guards Hermes's direct Anthropic token resolver:
while the source is suppressed, the resolver must not read Claude Code from
the macOS Keychain or `~/.claude/.credentials.json`. Claude Code's own stored
credentials remain untouched and continue working outside Hermes.

## Components and Responsibilities

### Anthropic credential pool

Both setup tokens are stored as explicit credentials in the local Hermes auth
store. They receive non-secret labels such as `local-setup-primary` and
`vps-setup-backup`. No token value may appear in configuration documentation,
Git history, logs, test output, or command arguments visible in process lists.

The Anthropic pool uses `fill_first`. Hermes continues using the first healthy
credential until it becomes exhausted, then rotates to the next healthy
credential. The local token must remain priority zero and the VPS token
priority one after a fresh `load_pool("anthropic")`, not merely in the raw JSON
file.

### Anthropic subscription compatibility plugin

The existing `anthropic-subscription-provider` plugin remains enabled. It
continues to provide subscription-compatible Anthropic request shaping and
bidirectional tool-name mapping.

The plugin adds one narrowly scoped credential-source guard. When Hermes has
persisted `suppressed_sources.anthropic` containing `claude_code`, calls to the
direct Anthropic token resolver skip both Claude Code credential stores and
resolve only explicitly configured environment/pool credentials. Removing the
suppression restores upstream resolver behavior.

Credential selection, cooldowns, and failover remain owned by Hermes core.
The guard does not classify errors, rotate credentials, select providers, or
change retry timing. No proxy behavior is added.

### OpenAI Codex fallback

OpenAI Codex is configured through Hermes's native `openai-codex` provider and
added to the top-level `fallback_providers` chain:

```yaml
fallback_providers:
  - provider: openai-codex
    model: gpt-5.6-sol
```

Hermes receives its own OpenAI device-code OAuth session. The existing Codex
CLI credentials under `~/.codex/auth.json` are not imported because OpenAI
OAuth refresh tokens are single-use and sharing them between Hermes and Codex
CLI creates a token-rotation race.

## Data Flow and Failure Handling

For every new local-agent turn, Hermes starts with Anthropic and selects the
first healthy setup token.

- A plan or usage-limit response rotates credentials immediately when Hermes
  classifies it as a quota wall.
- A generic HTTP 429 retries the same credential once, then rotates after a
  second consecutive 429, following Hermes's native policy.
- HTTP 401/403 attempts the provider's normal auth recovery and rotates if the
  credential remains unusable.
- When both Anthropic credentials are exhausted, Hermes activates
  `openai-codex/gpt-5.6-sol` for the current turn.
- A later turn tries the primary route again according to Hermes's normal
  per-turn fallback and credential cooldown behavior.

The observed Anthropic response can include `Retry-After: 600`. Native Hermes
may therefore wait up to ten minutes before the second generic-429 attempt.
This design deliberately preserves the upstream policy for the initial
deployment. If live verification shows that the delay makes the bot unusable,
fast rotation will be specified as a separate, evidence-driven change.

## Secret Handling and Backups

Before any mutation, create timestamped backups of the local Hermes
`auth.json`, `config.yaml`, and `.env`. Preserve their existing permissions.

The VPS is read-only for this task. Retrieve only the existing setup-token
value over the configured SSH connection. Do not modify the Jarvis profile,
restart its services, or copy any local credential to the VPS.

The provided local setup token and retrieved VPS token must be passed through
masked or non-echoing input and written only to the local Hermes credential
store. Verification reports use labels, status, provider, and non-reversible
fingerprints only.

## Implementation Boundaries

The change combines local runtime configuration with a small release of the
existing standalone plugin. It is not a fork of Hermes and introduces no new
service.

Plugin source changes are limited to a focused Claude Code suppression guard,
registration wiring, compatibility tests, a patch-version bump, and operator
documentation. The updated private plugin repository is the durable source;
the local Hermes plugin installation is updated through Hermes's normal plugin
manager.

Required state changes are limited to:

- the installed `anthropic-subscription-provider` plugin version;
- local Hermes credential-pool entries and suppression metadata;
- local `credential_pool_strategies.anthropic: fill_first`;
- local OpenAI Codex OAuth state;
- local top-level `fallback_providers`;
- a controlled restart of the local Hermes gateway.

## Verification

Verification must use the installed local Hermes runtime, not the development
checkout.

1. Backups exist and contain the pre-change state.
2. `hermes auth list anthropic` shows exactly the two intended setup-token
   credentials in the correct order and no active `claude_code` entry.
3. Reloading the pool preserves that order and suppression state.
4. A resolver test proves that a suppressed `claude_code` source cannot read
   the Keychain or credentials file, while removing suppression restores the
   original resolver behavior.
5. A direct OpenAI Codex request using `gpt-5.6-sol` returns a valid response.
6. A normal Anthropic request returns a valid response through the subscription
   plugin.
7. A controlled credential-failure test demonstrates Anthropic credential
   rotation without exposing either token.
8. A controlled all-Anthropic-unavailable test demonstrates activation of the
   OpenAI Codex fallback.
9. The fallback model can perform a normal text response and a terminal/Bash
   tool call without corrupting tool history.
10. After restarting the local gateway, Telegram polling is healthy and the
   configured pools and fallback chain remain intact.
11. VPS Jarvis configuration and service state are unchanged.

Temporary failure injection must operate on backed-up local state or an
isolated local test profile and must be reverted before the gateway is handed
back. A successful final check must run against the restored production
configuration.

## Rollback

Stop the local gateway, restore the timestamped local Hermes backups, restart
the gateway, and verify Telegram polling. Rollback never requires a VPS change
or plugin removal.
