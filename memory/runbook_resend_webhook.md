# Resend Webhook — Configuration

## Purpose
Receive real-time email delivery events (delivered, opened, clicked, bounced,
complained, delivery_delayed) from Resend and update `email_campaigns` and
`email_logs` counters automatically. Without this, the campaign dashboard
shows `delivered_count=0` even if emails were actually delivered.

## Endpoint
```
POST {REACT_APP_BACKEND_URL}/api/webhooks/resend
```

No authentication — endpoint is public. Security is enforced via **Svix HMAC
signature verification** (see below).

## Environment Variable

Add to `backend/.env` (or inject via Emergent's secret manager):

```
RESEND_WEBHOOK_SECRET=whsec_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

**Never commit this value to git.** If left empty / unset, the endpoint will
accept all payloads without verification (development mode). In production
this variable **must** be set — otherwise any attacker knowing the URL could
forge fake delivery events.

## How to obtain the secret

1. Go to https://resend.com/webhooks
2. Click **Add Webhook**
3. Fill in:
   - URL: `https://japap-refactor.preview.emergentagent.com/api/webhooks/resend`
     (or the production equivalent)
   - Events: select **all 6**:
     - `email.delivered`
     - `email.opened`
     - `email.clicked`
     - `email.bounced`
     - `email.complained`
     - `email.delivery_delayed`
4. Click **Create**. Resend displays the signing secret (`whsec_…`).
5. Copy the secret into `RESEND_WEBHOOK_SECRET` and restart the backend.

## Verification

Once configured, you can test by sending a small campaign (e.g. the sandbox
`seg_pytest_safe`) and observing:

1. Backend logs show `POST /api/webhooks/resend 200 OK` for each event.
2. `email_campaigns.delivered_count` / `opened_count` / `clicked_count`
   increment in near-real-time.
3. The admin "Campaigns" analytics panel shows rich event history per
   recipient via `email_logs`.

## Event mapping

| Resend event type       | DB event column    | Counter incremented            |
|-------------------------|--------------------|---------------------------------|
| `email.delivered`       | `delivered`        | `email_campaigns.delivered_count` |
| `email.opened`          | `opened`           | `email_campaigns.opened_count`   |
| `email.clicked`         | `clicked`          | `email_campaigns.clicked_count`  |
| `email.bounced`         | `bounced`          | `email_campaigns.bounced_count`  |
| `email.complained`      | `unsubscribed`     | `email_campaigns.unsub_count`    |
| `email.delivery_delayed`| `deferred`         | — (logged only)                  |

## Signature verification algorithm

Resend uses the Svix standard. For each incoming request we:

1. Reject if required headers missing (`svix-id`, `svix-timestamp`, `svix-signature`) → HTTP 401
2. Reject if timestamp older than 5 min (replay protection) → HTTP 401
3. Compute `HMAC-SHA256(secret_bytes, "{svix-id}.{svix-timestamp}.{body}")` and compare against any of the `v1,…` parts in `svix-signature` → HTTP 401 if none match

See `/app/backend/routes/email_tracking.py::resend_webhook`.
