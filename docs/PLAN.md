# Personal security project: expenses app on AWS + home server

## Goal

A personal project to strengthen my cloud security profile. My experience is mostly platform monitoring and defense, and I want hands-on practice deploying on AWS. I want an app I will actually use, with a real attack surface, so I can deploy it, harden it, and then attack it myself.

## Expenses app (cloud phase)

- Ideas with no real use (notes or password manager) were discarded. Chosen: **personal expenses app**.
- **Ingestion: open banking (PSD2) through Enable Banking**, read-only, restricted production mode for personal use. Validated with a local spike. **Scope: BBVA only.** Trade Republic was evaluated and dropped (it returns amount and date but no merchant text and no transaction ID); Bankinter may be added later.
- The iOS Shortcut / Apple Wallet approach was tested and **discarded**: the Wallet trigger only exposed the merchant name, not the amount.
- The web app shows aggregated expenses and lets me categorize them.
- AWS spend: as low as possible, ideally free. The app will run on AWS for a few days and then be torn down. A budget alert comes with the first Terraform apply.
- General plan: deploy on AWS first, use and test it; then migrate to the home server to avoid costs and learn integration.

### Decisions

- **Account**: a dedicated AWS member account under an Organization (not the ClickHouse test account).
- **IaC**: Terraform.
- **Backend**: Python. Core logic is plain, containerizable code behind a thin Lambda adapter, to ease the home-server migration.
- **Auth for the web app**: Amazon Cognito.
- **Ingestion**: scheduled pull (EventBridge to Lambda) from BBVA, idempotent writes to DynamoDB. See [DATA-MODEL.md](DATA-MODEL.md).
- **Bank consent**: PSD2 consent expires, so the app needs a "reconnect bank" flow and an alert before expiry.
- **Receipts**: a photo is parsed in memory with Amazon Textract (AnalyzeExpense), the user confirms, and only the resulting expense is stored. The image is never persisted. OCR sits behind an interface (`TicketReader`).
- **Frontend**: React + Vite + TypeScript, minimal, served from S3 + CloudFront.
- **Single user.**
- **Repository**: public on GitHub. No secrets, account IDs, real transactions or tokens ever committed; synthetic fixtures only.
- **Language**: English.

### Phases

1. **Feasibility** (done): local spike against Enable Banking; BBVA returns amount, date, counterparty and a stable ID.
2. Minimal backend with Terraform: scheduled pull, DynamoDB, secrets in SSM.
3. Bank consent flow (authorize, callback, renewal) and web app with login (Cognito), expense list, categories, receipt upload parsed with Textract (not stored).
4. Hardening: KMS encryption, least privilege, rate limits, CloudTrail and alerts.
5. Attack my own creation (threat model, API testing, upload abuse, key and session theft) and document findings and fixes.

See [IMPLEMENTATION-BRIEF.md](IMPLEMENTATION-BRIEF.md) for the build brief.

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
