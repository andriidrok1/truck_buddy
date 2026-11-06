# Handover: Abdul → Andrii (2026-11-05)

Abdul is leaving TruckBuddy. Everything below moves to Andrii (`andriidrok1`), who already owns this repo.
The production server runs in **Abdul's** AWS account today, so it has to be rebuilt in Andrii's.

## What Abdul hands over privately (not in git)

| Item | Notes |
| --- | --- |
| `truckbuddy-db-2026-11-05.dump` | `pg_dump -Fc` of the prod database (48 conversation rows, 2 drivers, 2 orgs). Driver data: keep private. |
| `truckbuddy-data-volume-2026-11-05.tgz` | The `/data` volume (parking cache). Small, can be skipped. |
| Current `.env` values | Only needed until the keys are rotated. Production also sets `CATEGORIES_EXTRA=cat_scale,walmart,truck_wash` (added 2026-10-30). |

A Lightsail snapshot (`truckbuddy-api-handover-2026-11-05`) stays in Abdul's account as a fallback until the
new server is live. Lightsail snapshots can't be shared with another AWS account.

## Andrii: rebuild the server (about an hour)

1. In your own AWS account: `AWS_PROFILE=<yours> bash deploy/provision.sh` (instance, static IP, firewall;
   writes the SSH key to `~/.ssh/truckbuddy-aws.pem`).
2. Create `~/truckbuddy-secrets.env` with the **new** third-party keys (step "Rotate" below) and
   `CATEGORIES_EXTRA=cat_scale,walmart,truck_wash`. `deploy.sh` generates `JWT_SECRET`, `TESTER_CODE` and
   `POSTGRES_PASSWORD` in `~/.truckbuddy/generated.env` on the first run. A new `JWT_SECRET` signs every
   driver out once; keep the old one in `generated.env` if you want to avoid that.
3. Install the deploy command and the `tbdeploy` user: `deploy/README.md`, "GitHub Actions deploy user".
4. `TB_HOST=<new ip> bash deploy/deploy.sh main`.
5. Restore the database:
   `ssh ubuntu@<new ip> 'cd /opt/truckbuddy && docker compose exec -T db pg_restore -U truckbuddy -d truckbuddy --clean --if-exists' < truckbuddy-db-2026-11-05.dump`
   (prefix `TB_SHA=$(cat .current-sha)` if compose asks for it), then restart `api`.
6. Check `http://<new ip>/api/health`.
7. GitHub → Settings → Secrets → Actions: replace `TRUCKBUDDY_HOST`, `TRUCKBUDDY_KNOWN_HOSTS`,
   `TRUCKBUDDY_DEPLOY_KEY` with the new box's values, then run "Deploy backend" once by hand.
8. DNS: point `A api.truckbuddy.io` at the new static IP. Caddy gets a certificate on the first HTTPS request.

## Rotate

Every key below was pasted in a chat on 2026-11-05. Make new ones on your own accounts and revoke the old ones
once the new server runs on them:
OpenAI, HERE, OpenRouteService, Google Maps (if set), the GitHub Actions deploy key, `JWT_SECRET`,
`TESTER_CODE`, `POSTGRES_PASSWORD`.

## Other accounts

- **Domain `truckbuddy.io`** (Namecheap): Abdul pushes it to Andrii's Namecheap username. Namecheap's DNS
  records move with the domain.
- **Expo / EAS** and the app stores: confirm they are on Andrii's accounts.

## Abdul, after the new server is live

- Delete the Lightsail instance `truckbuddy-api`, static IP `truckbuddy-api-ip` and the handover snapshot.
- Delete `~/.ssh/truckbuddy-aws.pem`, the `truckbuddy` AWS profile, `~/.truckbuddy/`, `~/truckbuddy-secrets.env`
  and the local clones.
- Leave the GitHub repo (or Andrii removes `zhadyz`).
