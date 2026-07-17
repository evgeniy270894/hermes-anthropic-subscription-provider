# Hermes Anthropic Subscription Provider

Standalone Hermes Agent plugin for using Anthropic Claude Pro/Max setup tokens
through Hermes's native `anthropic` provider.

The plugin fixes the request identity mismatch that can route tool-enabled
Hermes requests to Anthropic "extra usage". It keeps Hermes's internal tool
names unchanged, uses subscription-compatible aliases only on the Anthropic
OAuth wire request, and maps Claude's tool calls back before Hermes dispatches
them.

## Requirements

- Hermes Agent 0.18.2 or newer
- an Anthropic setup token already configured in Hermes
- Claude Pro or Max subscription access
- GitHub access to this private repository (`gh auth login` is recommended)

The plugin does not create, refresh, print, upload, or store your setup token.
Hermes remains responsible for credential discovery and storage.

## Install

```bash
hermes plugins install \
  https://github.com/evgeniy270894/hermes-anthropic-subscription-provider.git \
  --enable
hermes gateway restart
```

For a private repository, authenticate Git over HTTPS first:

```bash
gh auth login
gh auth setup-git
```

SSH also works when the active SSH key belongs to a GitHub account that has
access to this repository:

```bash
hermes plugins install \
  git@github.com:evgeniy270894/hermes-anthropic-subscription-provider.git \
  --enable
```

Keep the normal Hermes provider and model configuration:

```yaml
model:
  provider: anthropic
  name: claude-opus-4-8
```

Do not create a custom provider name for this plugin. Native setup-token
handling in Hermes is selected by the canonical `anthropic` provider id.

## Verify

```bash
hermes plugins list
hermes -z "Reply with exactly SUBSCRIPTION_OK" \
  --provider anthropic \
  --model claude-opus-4-8
```

An optional real-agent acceptance harness is included. It makes live Anthropic
requests and may create a temporary user skill, so it requires an explicit
confirmation flag:

```bash
python scripts/live_e2e.py --confirm-live
```

Run selected cases with `--case`, for example:

```bash
python scripts/live_e2e.py --confirm-live \
  --case terminal --case terminal-spaced-path --case synthetic-mcp
```

The harness prints case names, internal tool names, request counts, and pass or
fail markers. It never prints credentials or request headers.

## Maintenance and rollback

```bash
hermes plugins update anthropic-subscription-provider
hermes plugins disable anthropic-subscription-provider
hermes plugins enable anthropic-subscription-provider
hermes plugins remove anthropic-subscription-provider
hermes gateway restart
```

Disabling or removing the plugin restores Hermes's built-in Anthropic transport
after the next process or gateway restart. API-key traffic, Bedrock, Azure, and
third-party Anthropic-compatible providers are delegated unchanged even while
the plugin is enabled.

If Hermes changes its transport contract, the plugin fails closed for OAuth
requests with a local compatibility error instead of sending a request with an
unknown shape. Non-OAuth Anthropic requests continue through the upstream
transport.

Rotate a setup token through the same Hermes/Anthropic procedure you normally
use; no plugin reinstall is needed. Never paste setup tokens into bug reports,
terminal transcripts, or repository files.

## What it changes

Only native Anthropic OAuth requests are transformed:

1. enabled tools receive request-scoped `mcp__...` wire aliases;
2. explicit tool references in the system prompt receive the same aliases;
3. replayed tool-use names and specific tool choices stay aligned;
4. Claude's returned names are mapped exactly back to Hermes's internal names.

Tool arguments, Bash commands, workdirs, URLs, filenames, MCP payloads, and
ordinary prose are not rewritten. There is no local proxy and no platform
binary; the plugin is pure Python.

See [after-install.md](after-install.md) for a short operator checklist and the
design documents under `docs/superpowers/` for the full technical rationale.
