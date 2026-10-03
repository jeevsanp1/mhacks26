# Creating the two Cloudflare tunnel URLs

Local dev needs **two separate public `https://` URLs**, one per local server.
Each is a Cloudflare quick tunnel (`cloudflared`), which gives a random
`https://<words>.trydevcloudflare.com`-style URL with no account needed.

| # | Env var            | Tunnels to                      | Used by                                                                 |
|---|--------------------|---------------------------------|-------------------------------------------------------------------------|
| 1 | `CLAW_PUBLIC_URL`  | claw gateway, `localhost:18789` | Vapi calls `<url>/vapi/<VAPI_LLM_SECRET>/chat/completions` (phone brain) |
| 2 | `WEBHOOK_BASE_URL` | Node/Express server, `localhost:3000` | ElevenLabs server-tool calls (`/tools/*`) and post-call webhook (`/webhooks/post-call`) |

The two ports differ (`GATEWAY_PORT=18789`, `PORT=3000`), so you need two
`cloudflared` processes, each in its own terminal, both left running.

## Prerequisites

- `cloudflared` installed (`brew install cloudflared`).
- `.env` created from `.env.example`.
- Secrets generated: `openssl rand -hex 32` for `VAPI_LLM_SECRET` and
  `TOOL_WEBHOOK_SECRET`.

## Step 1: Start the local servers

```bash
# Terminal A: claw gateway (port 18789)
uv run claw gateway

# Terminal B: Node server (port 3000)
npm run dev
```

## Step 2: Create tunnel #1, claw gateway -> `CLAW_PUBLIC_URL`

```bash
# Terminal C
cloudflared tunnel --url http://localhost:18789
```

Copy the `https://....trycloudflare.com` URL it prints, then in `.env`:

```env
CLAW_PUBLIC_URL=https://<tunnel-1>.trycloudflare.com
```

No trailing slash and no `/vapi/...` suffix. The scripts append
`/vapi/<VAPI_LLM_SECRET>` themselves.

## Step 3: Create tunnel #2, Node server -> `WEBHOOK_BASE_URL`

```bash
# Terminal D
cloudflared tunnel --url http://localhost:3000
```

Copy that (different) URL into `.env`:

```env
WEBHOOK_BASE_URL=https://<tunnel-2>.trycloudflare.com
```

Then register `<WEBHOOK_BASE_URL>/webhooks/post-call` in the ElevenLabs
dashboard (Conversational AI -> Settings -> Webhooks) and copy its signing
secret into `POST_CALL_WEBHOOK_SECRET`.

## Step 4: Register the URLs with the providers

Restart the Node server (`npm run dev`) so it picks up the new `.env`, then:

```bash
npm run create-agent            # ElevenLabs agent; tool URLs use WEBHOOK_BASE_URL
npm run create-vapi-assistant   # Vapi assistant; LLM URL uses CLAW_PUBLIC_URL
npm run import-phone            # provisions/assigns the Vapi phone number
```

## Verify

```bash
curl -i https://<tunnel-1>.trycloudflare.com/vapi/wrong/chat/completions \
  -H 'content-type: application/json' -d '{}'    # expect 401/403 from claw, not a tunnel error
curl -i https://<tunnel-2>.trycloudflare.com/    # expect a response from Express, not 502
```

## Gotchas

- **URLs change every time `cloudflared` restarts.** After a restart, update
  `.env` and re-run `create-agent` / `create-vapi-assistant` so the providers
  point at the new URLs.
- Don't swap them: gateway (18789) -> `CLAW_PUBLIC_URL`, Node server (3000) ->
  `WEBHOOK_BASE_URL`.
- Keep `VAPI_LLM_SECRET` private, since it is the only auth on the public
  `/vapi/<secret>/...` path.
- For stable URLs, use a named Cloudflare tunnel with your own domain instead
  of quick tunnels (`cloudflared tunnel login`, `tunnel create`,
  `tunnel route dns`).
