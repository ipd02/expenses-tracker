# Personal security project: expenses app on AWS + home server

## Goal

A personal project to strengthen my cloud security profile. My experience is mostly platform monitoring and defense, and I want hands-on practice deploying on AWS. I want an app I will actually use, with a real attack surface, so I can deploy it, harden it, and then attack it myself.

## Expenses app (cloud phase)

- Ideas with no real use (notes or password manager) were discarded. Chosen: **personal expenses app**.
- Card payments are captured automatically; importing CSVs by hand was discarded.
- Banks to cover: **Trade Republic** (the one I use most), **BBVA** and **Bankinter**.
- Chosen method: **iOS Shortcuts** (iPhone). One automation per bank card sends the transaction data with a POST to an API on AWS. Open banking was discarded because Trade Republic is not covered.
- Shortcut authentication: a **long, secret, revocable token** is enough.
- The web app will show aggregated expenses and allow uploading documents.
- AWS spend: as low as possible, ideally free. The app will run on AWS for a few days only and then be torn down. A budget alert comes later.
- General plan: deploy on AWS first, use and test it; then migrate to the home server to avoid costs and learn integration.

### Decisions

- **Account**: a dedicated AWS member account under the existing root/Organization (not the ClickHouse test account).
- **IaC**: Terraform.
- **Backend**: Python. Core logic is plain, containerizable code behind a thin Lambda adapter, to ease the home-server migration.
- **Auth for the web app**: Amazon Cognito.
- **Parsing of bank text**: deferred.
- **Receipts**: the user uploads a photo, Amazon Textract (AnalyzeExpense) extracts the text in memory, the user confirms, and only the resulting expense is stored. The image is never persisted. OCR sits behind an interface (`TicketReader`) so it can be swapped for Tesseract on the home server.
- **Frontend**: React + Vite + TypeScript, minimal, served from S3 + CloudFront.
- **Single user.**
- **Trigger**: the Wallet "Transaction" automation did not fire in a first test; the backend accepts a generic JSON payload so Wallet, Message or Email triggers can all feed it.
- **Repository**: public on GitHub under `ipd02`. No secrets, account IDs, real transactions or tokens ever committed; synthetic fixtures only.
- **Language**: English from now on.

### Phases

1. **Feasibility**: Wallet "Transaction" automations on the iPhone POSTing to a throwaway endpoint; verify over a few days that they fire reliably for all three cards.
2. Minimal backend with Terraform: API Gateway, Lambda, DynamoDB, token stored securely.
3. Web app with login (Cognito), expense list, categories, receipt upload parsed with Textract (not stored).
4. Hardening: KMS encryption, least privilege, rate limits, CloudTrail and alerts.
5. Attack my own creation (threat model, API testing, file-upload abuse, token theft) and document findings and fixes.

Design the backend so it is not tightly bound to AWS (containers, IaC) to ease the migration.

## Home server (second phase)

- Uses: remote storage, running processes, home automation, and remote access at any time. A Minecraft server for friends is one more feature to account for, not the center.
- Requirements: free or as cheap as possible, without changing operator services.
- Hardware: old Windows PC to be moved to Linux; believed to be an i5 with 8 GB RAM and a ~500 GB HDD (unverified). A cheap SSD would be the best upgrade.
- OS: Debian Server or Ubuntu Server, no desktop environment.
- Network: Jazztel (Orange), Livebox Fibra F@st 5656 router. Verified **public IPv4** (router WAN IP matches an "what is my IP" site) and not behind CG-NAT, so ports can be opened. No IPv6 assigned.
- Personal access: **Tailscale** (free, works with or without CG-NAT).
- Minecraft with friends: open only the game port via NAT/PAT on the router, with **DuckDNS** (free) for dynamic DNS, an isolated container on a separate network, and a player whitelist. Always test from outside the network, using mobile data.
- Planned Docker services: Nextcloud or Syncthing (storage), Home Assistant (home automation), Minecraft server.

### Order of steps

1. Install Debian or Ubuntu Server on the old PC (and an SSD if possible).
2. Reserve a fixed IP in the router's DHCP tab.
3. Install Tailscale and Docker.
4. Run Minecraft in a container with a whitelist.
5. Create a NAT/PAT rule for that port only and configure DuckDNS.
6. Test from outside the network.

## Current status

- Next step for the app: phase 1 (feasibility test).
- Next step for the server: choose Debian or Ubuntu Server and install it.
