---
name: provider-data-boundary
description: Audit ESPN Edge changes and artifacts for provider, credential, private-data, fixture, AI-grounding, backup, export, logging, and hosted-mode boundary violations. Use for sync/cache/ESPN code, fixtures, imports, exports, backups, AI reports, deployment configuration, CI artifacts, or any public-synthetic/private-real change.
---

# Provider Data Boundary

## Workflow

1. Read `SPEC.md` Section 11, `SYSTEM_ARCHITECTURE.md`,
   `docs/sprint-9/01-credential-custody.md`, the active phase contract, and the candidate diff.
2. Classify every touched path and data flow as public synthetic, private operator, shared code, or
   prohibited crossing.
3. Trace sources, transformations, persistence, logs, telemetry, prompts, exports, backups, build
   artifacts, CI uploads, and deployment principals. Do not trust a mode flag without structural
   denial.
4. Verify:
   - public mode cannot instantiate the ESPN client or access cookies, real captures, real reports,
     owner/member identifiers, private raw cache, or a custody decrypt grant;
   - hosted fixtures are schema-synthesized from allowlisted fields and fail adversarial identifier,
     name, cookie, league-name, and provenance tests;
   - private ESPN access is backend-only, read-only, no login/HTML, and globally limited to one
     request start per second;
   - real inputs never reach hosted AI, telemetry, evidence bundles, or public recovery payloads;
   - secrets and sensitive contents are redacted rather than merely omitted from happy-path logs.
5. Run only offline searches/tests unless the human separately authorizes a bounded live check.

## Output

Return a table of path/data flow, classification, evidence, verdict, and remediation. Rank boundary
violations by blast radius. List unexamined artifacts explicitly. Passing grep is supporting evidence,
not proof of absence; include construction tests and IAM/key-policy checks where applicable.

