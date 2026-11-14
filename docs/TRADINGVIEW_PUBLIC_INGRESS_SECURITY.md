# TradingView Public Ingress — Security Boundary

## Current state

The receiver is a separate FastAPI application intended to bind to `127.0.0.1:8765`. It is not a public service. The local smoke test in `TRADINGVIEW_WEBHOOK_LOCAL_SMOKE_TEST.md` does not require a tunnel, router port-forward, DNS name, or public deployment.

Do not expose the Mission Control dashboard, its API, account registry, approval endpoints, or inbox read endpoints to TradingView or the public internet.

## Required boundary before enabling a TradingView webhook

A future HTTPS ingress must satisfy every item below:

1. Publicly expose only `POST /webhook/tradingview` and forward it to the isolated local receiver.
2. Do not forward `/health`, `/inbox`, FastAPI docs/OpenAPI, dashboard paths, or any `/api/*` route.
3. Terminate HTTPS with a valid certificate and reject plaintext public traffic.
4. Preserve the receiver's request-size limit and fast acknowledgement behavior.
5. Keep the dedicated webhook secret in a local environment variable; never place it in source control, screenshots, chat, or alert titles.
6. If the ingress provider supports a verified source-IP allowlist, use TradingView's currently published webhook sender addresses. Do not trust arbitrary forwarded-IP headers from an untrusted proxy.
7. Log only delivery metadata needed for troubleshooting. Never log the request body or secret.
8. Test duplicate delivery, invalid secret, stale timestamp, oversized body, and unavailable local receiver before enabling the TradingView alert.
9. Ensure ingress failures do not silently report successful durable delivery. The public endpoint should return success only after the local receiver has durably accepted the event.
10. Keep all account selection, risk review, and human approval inside the private Mission Control application.

## Explicitly out of scope for this phase

- No router port forwarding.
- No public binding of the FastAPI receiver.
- No public access to the dashboard or internal API.
- No paid webhook bridge requirement.
- No selection or installation of a tunnel provider until its free-tier limits, privacy, authentication, and operational behavior are reviewed.
- No broker credentials, order routing, or automatic execution.

## Go-live checklist

A TradingView alert must not be enabled against a public URL until the operator has:

- verified the selected strategy's actual alert conditions and JSON payload;
- configured the dedicated secret locally and in the alert payload without sharing it;
- confirmed the public ingress forwards only the one POST path;
- observed a synthetic event arrive once in the Signals inbox;
- confirmed a duplicate is safely recognized;
- confirmed an invalid secret is rejected;
- confirmed no order is created and manual review remains required.

The local receiver is intentionally useful before public ingress exists. Complete local testing first; choose and configure ingress as a separate reviewed step.
