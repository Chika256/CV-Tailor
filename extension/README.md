# CV Tailor Capture

A Chrome/Edge Manifest V3 extension that captures the complete job listing in the active tab and sends it to a local CV Tailor companion on `127.0.0.1` (port `8765` by default).

## Features

- Extracts `JobPosting` JSON-LD when available.
- Includes selectors for LinkedIn, Indeed, Workday, Greenhouse, Lever, Ashby, SmartRecruiters, and generic careers pages.
- Uses only `activeTab` access rather than persistent access to every website.
- Automatically pairs with the loopback companion using a generated token.
- Retries pairing automatically if the local token changes.
- Provides a manual job-description fallback.
- Displays recent tailoring jobs and clarification questions.
- Shows completion or review status on the extension badge.

## Requirements

The extension is a capture client. It requires the separate local CV Tailor companion to be running on `127.0.0.1`. If the companion uses a port other than `8765`, open **Companion connection** in the popup and enter the same port as `port` in the workspace's `cv-tailor.json`. Changing the port discards the old pairing token and re-pairs with the new companion. The extension does not contain model credentials, modify CV files, or call OpenAI or Anthropic directly.

## Install For Development

1. Open `chrome://extensions` in Chrome or Edge.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Select this repository directory.
5. Pin **CV Tailor Capture** to the browser toolbar.

## Use

1. Open a job listing and expand any collapsed description.
2. Open the extension.
3. Select **Send current job**.
4. Reopen the popup to monitor status or answer clarification questions.

If automatic extraction is incomplete, use **Paste description manually**.

## Security

- Requests are limited to the loopback address, `http://127.0.0.1/*`. The port is configurable, but the host is not: the companion URL is always built from `127.0.0.1` and a validated port, so the pairing token cannot be sent elsewhere.
- The Geist typeface (SIL Open Font License, `fonts/OFL.txt`) is bundled in `fonts/`, so the popup makes no requests other than to the local companion.
- Job endpoints require a locally generated bearer token.
- Pairing is accepted only from a `chrome-extension://` origin.
- Authenticated job requests use the token even when Chrome omits the `Origin` header; explicit ordinary website origins are rejected by the companion.
- No API keys or CV data are stored in this repository.

## Troubleshooting

If the popup reports **Companion authentication failed**, the service is reachable but rejected the request. Update and restart the companion, then reload the extension at `chrome://extensions`. Version 1.0.2 distinguishes authentication errors from a service that is not running.
