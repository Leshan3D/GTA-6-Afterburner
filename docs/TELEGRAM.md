# Private Telegram operator bot (v0.2)

The bot is **read-only**. It never requests brokerage keys, submits orders, or controls systemd. The Telegram API token belongs to AFTERBURNER alone; **never reuse another project's token**. Commands are restricted to explicitly authorized chat IDs. Pushed research alerts go **only to the primary operator**. Friends' allowlisted chats can read status, not subscribe to operator alerts by default.

## Set up securely

1. In Telegram, open the official **@BotFather**, run `/newbot`, record its token privately, and start a conversation with the resulting bot. Never paste the token into a GitHub issue, chat message, or public repository.
2. On your host, stop AFTERBURNER briefly while finding your chat ID so only one process polls Telegram:

   ```bash
   systemctl --user stop afterburner.service
   cd <YOUR_PROJECT_DIR>
   nano ~/.config/afterburner/.env
   ```

   Set `AFTERBURNER_TELEGRAM_BOT_TOKEN=...` (no quotation marks necessary). Keep the chat ID empty for this step and send `/start` to the bot **after the service is stopped**.

3. Find the chat ID locally, without printing the token:

   ```bash
   python -m afterburner bot-chats
   ```

   If no chat ID appears, send `/start` again and retry. If an existing Telegram webhook is registered, `getUpdates` will not work until that webhook is removed. Do not change webhooks for other bots.
4. In `~/.config/afterburner/.env`, set:

   ```dotenv
   AFTERBURNER_TELEGRAM_BOT_TOKEN=YOUR_PRIVATE_BOT_TOKEN
   AFTERBURNER_TELEGRAM_CHAT_ID=YOUR_NUMERIC_CHAT_ID
   AFTERBURNER_TELEGRAM_BOT_USERNAME=YourAfterburnerBot
   AFTERBURNER_TELEGRAM_ALLOWED_CHAT_IDS=
   AFTERBURNER_PUBLIC_URL=
   AFTERBURNER_NO_ALERTS=0
   ```

   The `BOT_USERNAME` is optional but recommended for group commands such as `/meme@YourAfterburnerBot GATO`. The optional `PUBLIC_URL` is a URL you have **actually activated** via Funnel or GitHub Pages; `/link` will show it. `AFTERBURNER_NO_ALERTS=1` disables spontaneous alerts while keeping authorized command replies available.
5. Protect configuration and start AFTERBURNER:

   ```bash
   chmod 600 ~/.config/afterburner/.env
   systemctl --user start afterburner.service
   journalctl --user -u afterburner.service -n 35 --no-pager
   ```

## Commands

| Command | Behavior |
| --- | --- |
| `/progress` | Number of fresh markets, recorded markets, healthy/degraded data sources and last polling times |
| `/status` | Process heartbeat and timestamps; not a market prediction |
| `/meme` | All four market states with price, directional research scores and staleness |
| `/meme GATO` | Detailed market snapshot, funding, estimated OI, DEX liquidity and risk flags |
| `/health` | Degraded data-source list without raw error descriptions |
| `/news` | Five latest discovered (unverified) headlines |
| `/alerts` | Most recently sent research alerts |
| `/link` | Operator-configured public-facing URL |
| `/start` / `/help` | Command menu |

The bot polls in a separate daemon thread so heavy BingX/DEX market collection does not indefinitely defer command replies. It relies on polling, not a public webhook; you do **not** need to expose an inbound Telegram endpoint.

## Let a trusted friend use your status bot

Only allow friends you trust: ask them to message the bot, retrieve their numeric chat IDs with the **service temporarily stopped**, and explicitly add them to:

```dotenv
AFTERBURNER_TELEGRAM_ALLOWED_CHAT_IDS=12345678,98765432
```

Restart the service. Unknown chats receive no response; all commands are read-only. Telegram group chat IDs are often negative; retain the sign if whitelisting a group. Avoid public groups because they may disclose your monitoring habits to their participants. Never set the allowlist to `*` or disable recipient validation.
