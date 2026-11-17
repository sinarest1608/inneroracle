# Local testing

Workflow: **build locally, check it, then commit, push and redeploy**, then announce to
testers, check on your own devices, and announce to everyone.

## Run it on the Mac

```sh
scripts/run_local.sh        # http://localhost:8000/ui/
```

The first run creates `.env.local` (git-ignored) with its own VAPID keys and admin token.
A local run never touches production:

| | Production | Local |
| --- | --- | --- |
| Push database | `state/push.db` (server only) | `state/push.local.db` |
| VAPID keys, admin token | server `.env` | `.env.local` |
| Cloudflare tunnel | yes | none |
| Model calls | real | canned (`MOCK_MODEL=1`); set `MOCK_MODEL=0` to use the real key in `.env` |
| Festival jobs | scheduled | off; run by hand (below) |
| `/dev` test helpers | not mounted | on (`LOCAL_DEV=1`) |

The app refuses to start with `LOCAL_DEV=1` if the push database is named `push.db` or a
`TUNNEL_TOKEN` is in the environment.

## What to try

Chrome and Safari on macOS allow web push on `http://localhost`.

1. Open `http://localhost:8000/ui/`, open the sidebar, enable Daily verse.
2. The sidebar shows a **Local testing** block:
   - **Send test push now**: a random verse as a real daily push (Unread, badge, History).
   - **Pretend today is a festival**: dates Diwali today, picks its verse, sends "Happy Diwali".
   - **Preview what's new**: marks the latest release announced on this local server (no
     push) and shows the pop-up. Without this (or an announce), a release is a draft and
     nothing appears. The hints for Unread / History / Favourites need Daily verse on.
   - **Reset what's new + hints**: forget what this browser has seen.
3. Announce: `http://localhost:8000/ui/admin/announce.html` (token is printed by the script).
   Enter your device number as a tester, send to "Testers only", reload the app: the
   What's new pop-up and hints appear. A second browser profile that isn't a tester shows nothing.

Festival jobs by hand: `curl -X POST localhost:8000/dev/festival-sync` (fetches the real
calendar) and `curl -X POST localhost:8000/dev/festival-pick`.

## On the iPhone

iOS only allows push for an installed app over HTTPS. With the local server running:

```sh
cloudflared tunnel --url http://localhost:8000
```

Open the `https://….trycloudflare.com/ui/` address it prints in Safari, add it to the Home
Screen as a separate test app, and remove it afterwards. It is a different origin from the
real app, so the production install is untouched. The `/dev` helpers are reachable by anyone
who has that address while the tunnel is up; they only act on the local database.

## Then deploy

```sh
python -m pytest -q
git push            # then on the server: git pull && docker compose up -d --build app
```

Nothing is announced by deploying. Open `/ui/admin/announce.html` on the real site, send to
"Testers only", check the push, pop-up and hints on your devices, then send to "All devices".
