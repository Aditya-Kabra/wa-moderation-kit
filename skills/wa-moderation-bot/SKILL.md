---
name: wa-moderation-bot
description: Build, adapt and deploy a WhatsApp group moderation bot (neonize, Jev from TypeSafe, Plash) where a person approves every delete or warning. Use when someone wants to moderate a WhatsApp group, build a community moderation bot, or adapt this template to their own group and norms.
---

# WhatsApp moderation bot

A working template (about 230 lines of Python) lives in `template/` next to this file. Build the user's bot by copying and adapting it, not by writing a new one.

## What the bot does

1. Hears every text message in the watched groups (neonize, on a spare WhatsApp number).
2. Sends the group norms, the last 3 messages and the new message to Jev, which picks one of 7 labels with a probability for each.
3. Saves every message with all label scores and a hash of the norms version (SQLite in `data/`).
4. If the label is not `ok` and its score clears the bar (0.6, or 0.85 for `hype_forward`), posts an alert to the admin group with a case number and a suggested action.
5. A member of the admin group replies `12 del`, `12 warn` or `12 ok`. These are three hard-coded actions: the admin picks one, and the code does exactly that, then confirms.

## Hard rules

- A person approves every action. Add automatic deletes or warnings only if the user asks and has weeks of saved scores showing the label is reliable.
- Removing members stays manual: the bot has no member-removal command. An earlier version had a hidden "kick" that could have removed an admin during testing.
- Secrets stay out of git. `plash.env` and `data/` are gitignored; keep them there. Deploys use GitHub secrets.
- Deploy only with plain `plash-deploy`. `--force-data` replaces the live `data/` folder, which logs WhatsApp out and loses the history.
- Before going live, list every action the bot can take (alert, `del`, `warn`, `ok`, nothing else) and get the user's yes.

## Gather from the user first

Ask for these in one message, then wait:

1. A spare phone number with WhatsApp installed, never their personal number.
2. A TypeSafe API key (https://typesafe.ai).
3. A Plash account (https://pla.sh) with `plash-cli` installed.
4. The names of the group(s) to watch and of the admin group. The bot number joins both and is made admin of every watched group, because deleting needs it.
5. The group's norms in plain words. With none, start from `DEFAULT_RULES` in `template/main.py` and ask what to change.

## Build steps

1. **Copy the template** into the user's project folder, including the hidden `.gitignore`, `.plash` and `.github/`.
2. **Set the app name** in `.plash` (`PLASH_APP_NAME`); the app is served at `<name>.pla.sh`.
3. **Adapt norms and labels.** Put their norms in `DEFAULT_RULES`. Keep `CATEGORIES` unless their norms need another label, describe each label in plain words, and update `SUGGEST` to match.
4. **Test the judging before deploying.** With the user, write 8 to 10 sample messages (normal chat, a shared article, a promotion, a sarcastic reply, a scam) and run `judge()` on each with their key. Fix label wording until every result looks right. Wording fixes more than cutoffs: the `hype_forward` label must say that sharing something you read is fine, or it flags good posts.
5. **Create `plash.env`** from `plash.env.example`: their key, `BOT_NAME`, `DISPLAY_TZ` (Plash runs on US time) and a long random `ADMIN_PASSWORD`. Leave the group IDs as placeholders.
6. **Deploy** with `plash-deploy` from the project folder, then open `https://<name>.pla.sh/admin` (user `admin`, their password).
7. **Link WhatsApp:** on the bot phone, Linked devices, scan the QR on `/admin`. Codes expire after about 2 minutes; if scanning fails, restart the app and scan right away.
8. **Set group IDs:** `/admin` lists every group the bot is in with its ID (`...@g.us`). Put them in `WATCH_GROUPS` (comma separated) and `ADMIN_GROUP` in `plash.env`, then redeploy. The login survives because the session is in `data/`.
9. **Run the Before going live checklist.**
10. **Test end to end:** post a test promotion in the watched group, confirm the alert arrives, reply `N ok`, then post a second test message and reply `N del`.
11. **Optional push-to-deploy:** create a private GitHub repo, set secrets `TYPESAFE_API_KEY`, `ADMIN_PASSWORD`, `WATCH_GROUPS`, `ADMIN_GROUP` and `PLASH_TOKEN` (`gh secret set NAME`), and push to `main`. `.github/workflows/deploy.yml` rebuilds `plash.env` from those secrets. Set `BOT_NAME`, `DISPLAY_TZ` and `FLAG_THRESHOLD` as repository variables (`gh variable set NAME`); unset ones fall back to the defaults in `main.py`. When rotating a secret, update both `plash.env` and the GitHub secret.

## Before going live

- [ ] **Admin group is invite-only.** The code checks only the group, so any member of it can issue `del` and `warn`. Confirm its member list with the user.
- [ ] **Admin password is long and random.** `/admin` shows the WhatsApp login QR; whoever scans it controls the bot number.
- [ ] **Members are told** that a bot reads the group and that messages are stored.
- [ ] **Bot number is admin of every watched group.** Otherwise `del` fails and the admin group gets an error message.
- [ ] **Every action listed to the user** and approved (see Hard rules).

## Things that bite

- neonize needs `libmagic1`; `setup.sh` installs it on Plash. An ffmpeg warning in the logs is harmless, since only text is read.
- `main.py` starts uvicorn directly, because FastHTML `serve()` re-imports the module and starts the bot twice.
- Logs: `plash-logs --mode app` from the project folder.
- The public page at `/` shows the norms and labels; every other page needs the admin password.

## After it runs

Every message keeps all 7 scores. After a few weeks, compare alerts admins marked `ok` (false alarms) with the real ones, and tune `FLAG_THRESHOLD` or `LABEL_THRESHOLDS` per label from that data. Only then consider letting the bot act alone on a label it almost always gets right.
