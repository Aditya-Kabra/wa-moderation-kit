# How it was built

## The goal

Take the time and stress of moderating a 1,000-member WhatsApp group off the admins, while a person still makes every final call.

Most posts in the group are fine. Once in a while, one drifts from the norms: a forward, a hype post, a promotion or a sarcastic reply. Spotting these by hand takes time, and stepping in takes emotional energy.

## What we needed

| Need | Tool | Why this one |
|---|---|---|
| Read every message | A spare WhatsApp number + neonize | neonize uses a number the way WhatsApp Web does. The number is made an admin of the group so it can delete posts. |
| Understand meaning and tone | Jev, by TypeSafe | It picks an answer from a list and says how sure it is. It writes no text, so nothing needs parsing. Under 1 cent a day. |
| Remember everything | SQLite | One file next to the code. Nothing extra to run. |
| Tell the admins | The admin WhatsApp group | Admins already read it every day. |
| Act only when told | Three hard-coded actions | The admin picks one of three hard-coded actions, and the code does exactly that. |
| Run all day, easy to update | Plash + GitHub Actions | Plash keeps files between deploys, so the WhatsApp login survives. A push goes live in about a minute. |

### What we passed on

- **Claude Haiku instead of Jev.** Capable, but it writes free text that has to be checked, and it costs more per message. For "pick one label", Jev fits better.
- **Keyword lists.** Cheap, but they miss tone. Sarcasm and hype rarely use fixed words.
- **Letting the AI act on its own.** Not yet. Every score is saved, so that can be decided later from real results.

## One message, step by step

1. **Hear.** neonize receives the post. Photos, voice notes and the bot's own posts are skipped.
2. **Wait in line.** Posts are checked one at a time, so WhatsApp never waits on the model.
3. **Judge.** Jev gets the norms, the last 3 messages and the new one, and picks one of 7 labels: ok, spam_scam, promotion, abusive, disrespectful, hype_forward or rule_break.
4. **Remember.** The post is saved with all 7 scores and a hash of the norms version.
5. **Check the bar.** Flag only if the label isn't ok and the score is at least 0.6 (0.85 for hype).
6. **Alert.** A short message in the admin group with a case number and a suggested action.
7. **Act.** An admin replies `12 del`, `12 warn` or `12 ok`, and the bot does it and confirms.

## What we learned

- **Old attempts paid off.** Earlier unfinished versions made a build of a few hours possible.
- **Tune the words and the bar.** The bot flagged a useful shared article as hype. It took two changes to fix: rewording the label to say shared reads are fine, and raising its bar from 0.6 to 0.85.
- **Know every action your bot can take.** The first version had a "kick" command that nobody knew about. It could have removed an admin during testing. It was taken out, and removal stays manual.
- **Small things bite.** Deploying with `--force-data` logs WhatsApp out, and login codes expire in about 2 minutes.

## What's next

1. Collect a few weeks of real alerts and admin decisions.
2. Set each label's bar from that record.
3. Let the bot act alone on labels it almost always gets right.
