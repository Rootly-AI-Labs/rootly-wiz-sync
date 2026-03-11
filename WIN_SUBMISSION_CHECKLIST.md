# WIN Submission Checklist

This checklist maps the current repo to Wiz Integration (WIN) certification expectations and tracks what still needs to be prepared before submission.

## Product Readiness

- [x] Pull integration model is clear: the bridge stores Wiz service-account credentials and pulls issues into Rootly.
- [x] Integration uses Wiz GraphQL issue queries and documents the required `read:issues` scope.
- [x] Integration now follows the WIN-recommended delta update model using `issuesV2` plus a stored `last_successful_run_at` cursor.
- [x] Runtime model is aligned with WIN pull best practice: scheduled `sync` execution no more frequently than daily.
- [x] Runtime logs include retry, auth, and GraphQL error details.
- [x] Lifecycle support now covers new/opened issues and resolved/closed updates with a stable dedupe key.
- [x] Local env file is auto-loaded to reduce manual shell setup.
- [x] Local `validate` command gives users a low-friction readiness check before the first dry-run.
- [ ] Rootly onboarding is still partially manual.
  - The primary flow now uses the bootstrap command for the Generic Webhook source, dedupe, auto-resolution, and env-file updates.
  - The Rootly UI remains as a documented fallback path.
  - Users still need to set up scheduling outside the script.
- [ ] Package the integration for simpler install and operations.
  - Consider a Docker image, hosted worker, or packaged CLI release.

## API / Query Inventory

- [x] Default polling frequency is documented: `POLL_INTERVAL_SECS=86400` by default.
- [x] Delta cursor behavior is documented: after the first successful run, the bridge queries Wiz with `statusChangedAt.after=<last_successful_run_at>`.
- [x] Variables used by default are documented in the README and `.env.wiz-rootly.example`.
- [x] Certification-ready query inventory document added.

## Documentation

- [x] Repo README documents setup, dry-run, live-run, scheduling, and Rootly mapping guidance.
- [ ] Publish customer-facing documentation at a stable URL.
- [ ] Prepare a PDF copy if the final docs live behind authentication.
- [ ] Add a short troubleshooting section with common Wiz scope / region / Rootly webhook failures.

## Demo / Certification Materials

- [ ] Two to three sentences about Rootly.
- [ ] Two to three sentences about the Wiz + Rootly better-together story.
- [ ] One color icon in SVG format.
- [ ] One logo with company name in SVG format.
- [ ] Full-screen screenshots showing the integration in the Rootly product.
- [ ] A 30-minute end-to-end demo script and proposed time slots.
- [ ] Named support contact(s) for customers and certification follow-up.

## Suggested Next Build Steps

1. Add packaging and deployment guidance for the preferred once-daily runtime model.
2. Capture screenshots of the bootstrap-first onboarding and the resulting Rootly alerts.
3. Prepare the certification packet with query inventory, screenshots, SVG assets, and product copy.
