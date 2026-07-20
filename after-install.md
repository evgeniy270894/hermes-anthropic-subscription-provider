# After installation

1. Confirm the plugin is enabled:

   ```bash
   hermes plugins list
   ```

2. Keep Hermes configured with provider `anthropic` and model
   `claude-opus-4-8`.

3. Restart every long-running Hermes process so its cached transport is
   replaced:

   ```bash
   hermes gateway restart
   ```

4. Run a plain response and one harmless tool call:

   ```bash
   hermes -z "Reply with exactly SUBSCRIPTION_OK" \
     --provider anthropic --model claude-opus-4-8

   hermes -z "Use terminal once to run pwd, then report the path." \
     --provider anthropic --model claude-opus-4-8 --toolsets terminal
   ```

5. For the full live matrix, from the installed plugin directory run:

   ```bash
   python scripts/live_e2e.py --confirm-live
   ```

If OAuth requests are blocked by a compatibility message, update both Hermes
and this plugin, restart Hermes, and retry. To roll back immediately:

```bash
hermes plugins disable anthropic-subscription-provider
hermes gateway restart
```

The plugin never needs the setup token as a command-line argument. Do not pass
or print the token while troubleshooting.

To exclude the local Claude Code login without deleting its credentials, run:

```bash
hermes auth remove anthropic claude_code
```

While that source is suppressed, the plugin prevents the direct Anthropic
resolver from reading the macOS Keychain or `~/.claude/.credentials.json`.
Those credentials and files are not modified. Re-adding an Anthropic
credential clears the suppression and restores upstream resolver behavior.
