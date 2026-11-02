# Operating the AWS deployment

How the live deployment at `https://voiceops.priyabrata.com` is laid out, and the commands to run, restart, reset, update and tear it down. It is ordered as a flow: understand the layout, get in, run it, change it, look after the data, then the edges (Cloudflare, AWS) and the lessons.

No secrets are written here. Anything in `<angle brackets>` is a value you look up yourself.

## 1. The layout

```
Phone ──► Telnyx ──┐                         ┌─ Cloudflare Access (login) ─┐
                   │ wss /ws/media/*  (bypass)│                             │
Browser ───────────┴──► Cloudflare edge ──► Tunnel ──► cloudflared container
                                                          │
                                  EC2 t4g.small (ap-south-1), docker compose
                                    ├─ dashboard  (nginx, serves UI, proxies /api /ws /health /docs)
                                    ├─ api        (FastAPI + the embedded worker, one process)
                                    └─ postgres   (volume on the instance's disk)
```

| Thing | Value |
| --- | --- |
| AWS profile / region | `personal3` / `ap-south-1` |
| Instance | `i-00fea267b498ccc34`, name `voiceops`, `t4g.small`, Amazon Linux 2023 (arm64) |
| Public IP | `43.205.129.142` (auto-assigned, **changes when the instance is stopped and started**) |
| Security group | `sg-06a09f60e35978c9f` — only `22/tcp` from your home IP; nothing else is open |
| SSH key | `~/.ssh/voiceops-aws.pem`, user `ec2-user` |
| App directory on the server | `~/voiceops` |
| Compose files | `docker-compose.yml` + `docker-compose.aws.yml` + `docker-compose.cf.yml` |
| Config | `~/voiceops/.env` (mode 600, never committed) |

Why it looks like this:

- **One process for API and worker.** A live call needs the websocket that Telnyx opens and the worker that placed the call to be in the same process, because the hand-over happens through in-memory state (`MediaStreamRegistry`, see VOI-13). So `REDIS_URL` is empty, `redis` and `worker` are switched off (`profiles: ["disabled"]`), and the API starts its own worker. Queued calls live in memory and are rebuilt from Postgres when the API starts.
- **Cloudflare Tunnel, no open web ports.** `cloudflared` dials out to Cloudflare, so ports 80/443 stay closed and the instance's IP can change without touching DNS.
- **No password in the app.** `API_KEYS` is empty, so anyone who gets past Cloudflare Access has full access. Access is the only lock.

## 2. Get in

Save this once so every later command is short (`~/.zshrc`, or paste per session):

```bash
export VOICEOPS_HOST=43.205.129.142      # update if the IP changes, see section 9
alias vo-ssh='ssh -i ~/.ssh/voiceops-aws.pem ec2-user@$VOICEOPS_HOST'
```

```bash
vo-ssh                                   # interactive shell
vo-ssh 'docker ps'                       # one-off command
```

If SSH hangs or times out, it is almost always one of two things:

1. Your public IP changed and the security group still allows the old one → section 9.
2. The instance is stopped, or has a new IP → section 9.

On the server, tell compose which files make up the stack so you can type `docker compose …` without three `-f` flags:

```bash
cd ~/voiceops
export COMPOSE_FILE=docker-compose.yml:docker-compose.aws.yml:docker-compose.cf.yml
```

Every `docker compose` command below assumes you are in `~/voiceops` with that variable set. In a non-interactive one-liner, prefix it: `vo-ssh 'cd ~/voiceops && COMPOSE_FILE=… docker compose ps'`.

## 3. Is it healthy?

```bash
docker compose ps                        # api, dashboard, postgres: "healthy"; cloudflared: "Up"
curl -s localhost:8000/health            # run on the server; the API's own health check
docker compose logs --tail 50 api
docker compose logs --tail 20 cloudflared   # look for "Registered tunnel connection"
```

From your Mac:

```bash
curl -sI https://voiceops.priyabrata.com | head -3    # 302 to the Cloudflare login page = Access is working
```

What each piece failing looks like:

| Symptom | Where to look |
| --- | --- |
| Site gives a Cloudflare error (1033, 502) | `cloudflared` is down or cannot reach `api` / `dashboard` → section 7 |
| Login page loops or says not authorised | Access policy → section 7 |
| Dashboard loads, data fails | `docker compose logs api` and the `postgres` health |
| Call rings, nothing is said | Section 10 |

## 4. Start, stop, restart

```bash
docker compose up -d                     # start everything (or fix what is down)
docker compose stop                      # stop everything, keep containers and data
docker compose down                      # remove containers, KEEP the database volume
docker compose down -v                   # remove containers AND the database volume. Destroys data.
```

Restart one service, from least to most disruptive:

```bash
docker compose restart dashboard         # frontend (nginx). Harmless.
docker compose restart api               # API + embedded worker. Drops any live call; queued calls come back from the DB.
docker compose restart postgres          # the API loses its connections for a few seconds; restart api after
docker compose restart cloudflared       # the site is unreachable for a few seconds
```

`restart` does **not** re-read `.env`. To pick up a changed value, recreate the container:

```bash
docker compose up -d api                 # recreates only if its config changed
docker compose up -d --force-recreate api
```

The containers use `restart: unless-stopped`, so they come back by themselves after an instance reboot. There is no systemd unit to manage: Docker starts on boot (enabled in the instance's user-data) and brings the containers up.

Check what the API actually received, instead of trusting the file:

```bash
docker compose exec api printenv | grep -E 'CALLING|TELEPHONY|TELNYX_PUBLIC|REDIS|PROVIDER'
```

## 5. Logs

```bash
docker compose logs -f api               # follow
docker compose logs --since 10m api
docker compose logs api | grep -E 'sent .* bytes|mark echoed|heard .* of caller audio'
docker compose logs api | grep -i -E 'error|traceback|dead.letter'
docker stats --no-stream                 # memory per container (the box has 2 GB plus a 2 GB swap)
```

## 6. Put new code on the server

There is no CI deploy. You copy the repo and rebuild. From the repo root on your Mac:

```bash
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude 'node_modules' --exclude '.venv' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.db' \
  -e "ssh -i ~/.ssh/voiceops-aws.pem" \
  ./ ec2-user@$VOICEOPS_HOST:~/voiceops/

vo-ssh 'cd ~/voiceops && COMPOSE_FILE=docker-compose.yml:docker-compose.aws.yml:docker-compose.cf.yml docker compose up -d --build'
```

- The `--exclude '.env'` matters. With `--delete`, rsync would otherwise remove the server's `.env`, which holds the keys and the tunnel token and exists nowhere else.
- Build only what changed: `docker compose up -d --build api` or `… dashboard`.
- On start the API runs `create_all`, which creates missing tables but never alters existing ones. A change to an existing table needs the migration run by hand: `docker compose exec api alembic upgrade head`.
- The instance is arm64 (Graviton). Build happens on the server, so there is no architecture mismatch. Do not push an image built on an Intel machine.

## 7. Change configuration

All settings live in `~/voiceops/.env`. Edit, then recreate the service that reads it:

```bash
vo-ssh
cd ~/voiceops && nano .env               # or: sed -i 's/^KEY=.*/KEY=value/' .env
export COMPOSE_FILE=docker-compose.yml:docker-compose.aws.yml:docker-compose.cf.yml
docker compose up -d api
docker compose exec api printenv KEY     # confirm
```

Values you will touch:

| Setting | Meaning |
| --- | --- |
| `CALLING_HOURS_START` / `CALLING_HOURS_END` / `CALLING_HOURS_TIMEZONE` | Calls outside the window are *deferred* (given a `scheduled_at`, no attempt used). **Equal values disable the gate** (`0`/`0` while testing). Normal: `9` / `20` / `Asia/Kolkata`. The default timezone is UTC, so forgetting the timezone shifts your window by 5.5 hours. |
| `TELEPHONY_ALLOWED_NUMBERS` | Comma-separated numbers the app may dial. Anything else is refused and not retried. Set it. It is the main protection against a wrong number or an abused endpoint. |
| `TELEPHONY_MAX_CALL_SECONDS` | Hard cap per call (60 while testing). |
| `TELNYX_PUBLIC_BASE_URL` | `https://voiceops.priyabrata.com`. Sent to Telnyx with every call as the websocket address. |
| `LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER` | `mock` or the real provider; each has its own key. |
| `GEMINI_MODEL` | Use a fast model; a slow one makes the agent pause for seconds. |
| `POSTGRES_PASSWORD` | Used by compose in `docker-compose.aws.yml`. Changing it after the first start does **not** change the existing database's password; see section 8. |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token for the tunnel. See section 9. |

Do not set `REDIS_URL` here. A non-empty value switches the embedded worker off and live calls stop working.

## 8. The database

Postgres 16 in the `postgres` container. Data is in a Docker volume on the instance's disk; there are no automatic backups.

Tables: `agents`, `calls`, `call_turns`, `call_events`.

### Look inside

```bash
docker compose exec postgres psql -U voiceops -d voiceops
```
```sql
\dt
select status, count(*) from calls group by status;
select id, status, created_at, scheduled_at, last_error from calls order by created_at desc limit 10;
```

### Back up (do this before anything destructive)

On the server:

```bash
docker compose exec -T postgres pg_dump -U voiceops -d voiceops --no-owner > ~/voiceops-$(date +%F).sql
```

Copy it to your Mac:

```bash
scp -i ~/.ssh/voiceops-aws.pem ec2-user@$VOICEOPS_HOST:~/voiceops-$(date +%F).sql ~/Desktop/
```

### Clean the data but keep the schema

Delete calls and keep the agents you configured:

```bash
docker compose exec postgres psql -U voiceops -d voiceops \
  -c "truncate call_events, call_turns, calls restart identity cascade;"
docker compose restart api               # clears the in-memory queue as well
```

Delete everything including agents:

```bash
docker compose exec postgres psql -U voiceops -d voiceops \
  -c "truncate call_events, call_turns, calls, agents restart identity cascade;"
docker compose restart api
```

Restart the API after either one. The queue is held in memory; without a restart it can still hold calls whose rows you just removed.

### Start from nothing (new empty database)

```bash
docker compose down
docker volume ls | grep postgres         # find the volume name
docker volume rm <that volume>
docker compose up -d                     # Postgres initialises from scratch, and the API creates the tables on start
```

### Copy your local database to the server

On your Mac, with the local compose Postgres running. The `grep -v` is required: a newer local `pg_dump` writes `SET transaction_timeout`, which Postgres 16 rejects and the restore stops on it.

```bash
/opt/homebrew/bin/pg_dump "postgresql://voiceops:voiceops@localhost:5432/voiceops" --no-owner \
  | grep -v -E '^(SET transaction_timeout|SET default_table_access_method)' > voiceops-local.sql

# then restore into an empty (truncated) database:
cat voiceops-local.sql | ssh -i ~/.ssh/voiceops-aws.pem ec2-user@$VOICEOPS_HOST \
  'cd ~/voiceops && COMPOSE_FILE=docker-compose.yml:docker-compose.aws.yml:docker-compose.cf.yml \
   docker compose exec -T postgres psql -U voiceops -d voiceops -v ON_ERROR_STOP=1'
```

`ON_ERROR_STOP=1` makes a failure loud instead of leaving half a restore.

### Change the Postgres password

The password is only applied when the volume is first created. To change it on a live database:

```bash
docker compose exec postgres psql -U voiceops -d voiceops -c "alter user voiceops password '<new>';"
# then set POSTGRES_PASSWORD=<new> in .env, and:
docker compose up -d api
```

### Retention

Finished calls are personal data. `backend/scripts/retention.py --dry-run` shows what would be deleted; `RETENTION_DAYS` controls the age.

## 9. Cloudflare

Dashboard: Zero Trust → **Networks → Tunnels** (the tunnel and its routes) and **Access → Applications** (who may log in).

### Tunnel

- Health: the tunnel shows **Healthy** when `cloudflared` is connected. Locally, `docker compose logs cloudflared`.
- Published routes: the hostname `voiceops.priyabrata.com` → the service URL. Inside the compose network the services are reached by name: `http://api:8000` and `http://dashboard:80`. A service URL must include `http://`; `HTTP, api:8000` is rejected.
- The service names only resolve because `cloudflared` is in the same compose project. Do not run it with `docker run` on its own.
- **Rotate the token** (if it leaked, for example pasted in a chat): Tunnels → your tunnel → refresh token, put the new value in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, then `docker compose up -d cloudflared`. The old token stops working.

### Access (the lock)

Two applications on the hostname:

1. The whole site: **Allow** policy for the people you list, logging in with a **one-time PIN** emailed to them.
2. The Telnyx media path `/ws/media/*`: a **Bypass** policy. Telnyx is not a person and cannot log in, so without this the audio websocket is turned away and every call rings and goes silent. The call id in the URL is the only thing protecting it.

To give another person access: Access → Applications → the app → Policies → add their email to the Allow rule. They do not need a Cloudflare account; they enter their email and the PIN that arrives. Free up to 50 users. There is no username/password option; the PIN is the intended method.

Everyone allowed in has **full** access (create agents, place calls, delete). There are no roles until `API_KEYS` and roles are used in the app.

### DNS

`priyabrata.com` must stay on Cloudflare's nameservers. `voiceops` is a CNAME to the tunnel, created for you by the route. If a freshly created record "does not exist" on your Mac, that is your resolver caching the earlier "no such name" answer. Verify without the cache:

```bash
dig @1.1.1.1 voiceops.priyabrata.com +short
curl --resolve voiceops.priyabrata.com:443:104.21.56.243 -sI https://voiceops.priyabrata.com | head -3
```

### Telnyx

The VoiceOps code does not use Telnyx's webhook URL. Calls are controlled over REST, and audio comes in through the websocket whose address the API sends with each call. The webhook URL currently points at the site root, which returns `405` and is behind Access anyway. It is harmless; clear it in the Telnyx application to remove the noise.

## 10. Test a call

Before: calling hours are off or you are inside the window, `TELEPHONY_ALLOWED_NUMBERS` contains your phone, the agent is **Active**, and the agent has a real ElevenLabs `voice_id`.

1. Open the dashboard → **Calls → New call**.
2. Watch the log while it rings:

   ```bash
   docker compose logs -f api | grep -E 'dial|stream|sent .* bytes|mark echoed|heard .* of caller audio|error'
   ```

3. Answer. A healthy call shows the greeting being sent (`sent … bytes`), `mark echoed`, and `heard … of caller audio` after you speak.

| What you see | Cause |
| --- | --- |
| Call stays `scheduled`, "outside calling hours" | Calling-hours gate → section 7 |
| Refused straight away as invalid number | Not in `TELEPHONY_ALLOWED_NUMBERS` |
| Telnyx `403` / code `10010` | The Telnyx outbound profile does not allow that country |
| Rings, no audio, `media stream arrived with nothing waiting` | A separate worker is running, or `REDIS_URL` is set → section 4 |
| Rings, no audio, nothing in the log about a stream | Cloudflare Access is blocking `/ws/media/*`; check the Bypass policy |
| Hangs up when the greeting starts | ElevenLabs `404`/`402`: bad or library `voice_id` |
| Long silences | Slow LLM model, see `GEMINI_MODEL` |

A plain `GET` to `/ws/media/…` returns `404`. That is normal for a websocket route and does not mean it is broken.

After testing, turn the gate back on:

```bash
sed -i 's/^CALLING_HOURS_START=.*/CALLING_HOURS_START=9/; s/^CALLING_HOURS_END=.*/CALLING_HOURS_END=20/' .env
grep -q '^CALLING_HOURS_TIMEZONE=' .env || echo 'CALLING_HOURS_TIMEZONE=Asia/Kolkata' >> .env
docker compose up -d api
```

## 11. AWS

Always pass the profile and region (or `export AWS_PROFILE=personal3 AWS_REGION=ap-south-1`).

```bash
export AWS_PROFILE=personal3 AWS_REGION=ap-south-1
aws sts get-caller-identity                                    # confirm you are in account 749336099897
aws ec2 describe-instances --instance-ids i-00fea267b498ccc34 \
  --query 'Reservations[0].Instances[0].[State.Name,PublicIpAddress]' --output text
```

### Your home IP changed (SSH times out)

```bash
MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 describe-security-groups --group-ids sg-06a09f60e35978c9f \
  --query 'SecurityGroups[0].IpPermissions[].IpRanges[].CidrIp' --output text   # the old address
aws ec2 revoke-security-group-ingress --group-id sg-06a09f60e35978c9f --protocol tcp --port 22 --cidr <old>/32
aws ec2 authorize-security-group-ingress --group-id sg-06a09f60e35978c9f --protocol tcp --port 22 --cidr $MYIP/32
```

The site and calls do not depend on SSH, only your access to the box does.

### Stop to save money, start again

```bash
aws ec2 stop-instances  --instance-ids i-00fea267b498ccc34
aws ec2 start-instances --instance-ids i-00fea267b498ccc34
aws ec2 wait instance-running --instance-ids i-00fea267b498ccc34
aws ec2 describe-instances --instance-ids i-00fea267b498ccc34 \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text      # NEW address
```

After a start, update `VOICEOPS_HOST`. The site itself keeps working at the same hostname because the tunnel dials out; only SSH needs the new IP. The containers start by themselves. The disk and the database persist while stopped. A stopped instance still bills for the disk (about $1.6/month for 20 GB). Its auto-assigned public IPv4 is released on stop, which is why the address changes.

Reboot only: `aws ec2 reboot-instances --instance-ids i-00fea267b498ccc34`.

### Cost

About $15/month running 24/7 (`ap-south-1`): instance about $8.2, public IPv4 about $3.7, 20 GB gp3 about $1.6, plus a little data transfer. Stopping the instance removes the compute part only. Pricing is queried with `--region us-east-1`, whichever region you use.

### Remove everything

1. Back up the database (section 8) if you want the data.
2. `aws ec2 terminate-instances --instance-ids i-00fea267b498ccc34`
3. After it terminates: delete the security group (`aws ec2 delete-security-group --group-id sg-06a09f60e35978c9f`) and the key pair (`aws ec2 delete-key-pair --key-name voiceops`, then remove `~/.ssh/voiceops-aws.pem`).
4. Cloudflare: delete the tunnel, the route and the Access applications.
5. Check nothing is left that bills: `aws ec2 describe-volumes`, `aws ec2 describe-addresses` (an unattached Elastic IP costs about $3.7/month for nothing).

## 12. Things worth learning from this setup

**Where state lives decides how you can run it.** Live calls need the websocket and the worker in one process because the hand-over is in memory. That single fact is why there is one API container with an embedded worker, why `REDIS_URL` must stay empty, and why you cannot scale to two API containers yet. Open item VOI-13 is the fix.

**The database is written before the queue.** When the queue is in memory and the API restarts, nothing is lost: `_restore_queue` re-reads pending calls from Postgres. That is why restarting `api` is safe, and why truncating tables without restarting `api` leaves ghosts in the queue.

**`restart` is not `up`.** `docker compose restart` reuses the container's old environment. A changed `.env` or compose file needs `docker compose up -d <service>`. Verify with `printenv` in the container rather than assuming.

**A call has two stages to get past.** Telnyx opens the audio socket when the phone starts *ringing* and sends `start` only when the person *answers*. "Socket connected" therefore does not mean "answered".

**The calling-hours gate defers; it does not fail.** A deferred call keeps its place and gets a `scheduled_at`. The default timezone is UTC. Equal start and end switches the gate off.

**The spend guard is yours to set.** `TELEPHONY_ALLOWED_NUMBERS` and `TELEPHONY_MAX_CALL_SECONDS` are what stops a mistake or an abuser from costing money. With `API_KEYS` empty and Cloudflare Access as the only lock, the allowlist is the second lock.

**A tunnel changes who connects to whom.** The server makes an outbound connection to Cloudflare, so inbound ports can be closed, the public IP stops mattering, and the only exposure is whatever Access lets through, plus the one Bypass path for Telnyx.

**Bypass is a deliberate hole.** The media path is open to the internet and guarded only by an unguessable call id. Keep it limited to `/ws/media/*`.

**Security group rules are tied to your IP.** If SSH fails after a network change, it is the rule, not the server.

**Secrets live in one file.** `~/voiceops/.env` (mode 600) is the only copy of the provider keys and tunnel token on the server. Back it up somewhere safe; `rsync --delete` without `--exclude '.env'` would remove it. If a token appears in a chat, a log or a screenshot, rotate it.

**Tool quirks that cost time**
- zsh does not split an unquoted `$VAR` into words, so a variable holding `ssh -i key host` fails. Use an alias, a function or a script.
- A `pg_dump` from a newer client writes `SET transaction_timeout`; Postgres 16 rejects it. Strip the line.
- A new DNS name can look missing on your own machine because the "no such name" answer was cached. Test against `1.1.1.1`.
- `~/.aws/credentials` has to be readable by you (mode 600). With mode 000 the CLI reports "Unable to locate credentials".
