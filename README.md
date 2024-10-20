# music-presence/discord-bot

This is the Discord bot for the Music Presence
[Discord server](https://discord.com/invite/7rc8dWD4ug)!

- Automatically assigns server members a configured listener role
  when they are listening to music
  and sharing it in their Discord status via
  [Music Presence](https://github.com/ungive/discord-music-presence)
- Other ideas? [Let me know!](https://github.com/music-presence/discord-bot/issues)

## Setup

```sh
$ python3 -m venv venv
$ source venv/bin/activate
(venv) $ pip install -r requirements.txt
(venv) $ cp .env.example .env
# Add your bot token to .env
(venv) $ python bot.py
```

## Deploy

```sh
$ docker compose up -d --build
```
