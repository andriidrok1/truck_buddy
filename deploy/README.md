# TruckBuddy API deploy (AWS Lightsail)

Modelled on VeteranHQ's playbook: one Ubuntu box, Docker, SSH-driven deploys,
SHA-tagged images, manual rollback. Railway (`backend/railway.json`) is unused here.

| What | Where |
|---|---|
| Instance | Lightsail `truckbuddy-api`, us-east-1, static IP `truckbuddy-api-ip` |
| SSH key | `~/.ssh/truckbuddy-aws.pem` (Mac only) |
| Generated secrets | `~/.truckbuddy/generated.env` (JWT_SECRET, TESTER_CODE, POSTGRES_PASSWORD; mode 600, Mac only) |
| Third-party keys | `~/truckbuddy-secrets.env` (OPENAI/HERE/ORS/GOOGLE; owner supplies) |
| On box | `/opt/truckbuddy/{docker-compose.yml,Caddyfile,.env,repo/,.current-sha,.previous-sha}` |

## First time
1. `AWS_PROFILE=<lightsail-capable profile> bash provision.sh` (instance, static IP, ports 22/80/443).
2. Add DNS: `A api.truckbuddy.io -> <static IP>`. Caddy issues the cert on first request after DNS resolves.
3. `TB_HOST=<static IP> bash deploy.sh <branch>`.

## Deploy
Normally GitHub Actions deploys (see `docs/deploy.md`). By hand: `TB_HOST=<ip> bash deploy.sh [branch]`
streams `backend/` into `/usr/local/bin/truckbuddy-deploy` (copy of `truckbuddy-deploy` here), which builds
`truckbuddy-api:<sha7>`, records the previous sha, restarts, gates on `/api/health` and rolls back to the
previous image if the gate fails.

`deploy.sh` rewrites `/opt/truckbuddy/.env` from scratch on every run (third-party keys + the generated
secrets + the fixed list below them). Anything added on the box by hand, such as
`CATEGORIES_EXTRA=cat_scale,walmart,truck_wash`, is lost unless it is in `~/truckbuddy-secrets.env` too.

## GitHub Actions deploy user (not done by provision.sh)
On the box, as root:
```
adduser --disabled-password --gecos "" tbdeploy
install -m 0755 truckbuddy-deploy /usr/local/bin/truckbuddy-deploy
printf 'Defaults:tbdeploy env_keep += "SSH_ORIGINAL_COMMAND"\ntbdeploy ALL=(root) NOPASSWD: /usr/local/bin/truckbuddy-deploy\n' > /etc/sudoers.d/tbdeploy
install -d -m 700 -o tbdeploy -g tbdeploy /home/tbdeploy/.ssh
echo 'restrict,command="sudo /usr/local/bin/truckbuddy-deploy" ssh-ed25519 <deploy public key>' > /home/tbdeploy/.ssh/authorized_keys
chown tbdeploy:tbdeploy /home/tbdeploy/.ssh/authorized_keys; chmod 600 /home/tbdeploy/.ssh/authorized_keys
```
GitHub secrets: `TRUCKBUDDY_DEPLOY_KEY` (the private half), `TRUCKBUDDY_HOST` (static IP),
`TRUCKBUDDY_KNOWN_HOSTS` (`ssh-keyscan -t ed25519 <ip>`).

## Rollback (manual, like VeteranHQ)
```
ssh -i ~/.ssh/truckbuddy-aws.pem ubuntu@<ip> "bash /opt/truckbuddy/rollback.sh <sha7>"   # preferred
ssh -i ~/.ssh/truckbuddy-aws.pem ubuntu@<ip> "bash /opt/truckbuddy/rollback.sh"          # .previous-sha
ssh -i ~/.ssh/truckbuddy-aws.pem ubuntu@<ip> "DRY_RUN=1 bash /opt/truckbuddy/rollback.sh <sha7>"
```
Rollback swaps the API image only (the box keeps an image per deployed sha and the five newest release dirs); Postgres data and `/data` (parking cache) persist.
Database backup: `docker compose exec -T db pg_dump -U truckbuddy truckbuddy | gzip > tb-$(date +%F).sql.gz`;
also enable Lightsail automatic snapshots on the instance.
