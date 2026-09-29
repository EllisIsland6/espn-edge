# ADR-007 — no public raw cache; conditional S3 or bounded local files

**Status:** accepted. **Date:** 2026-08-09.

## Context

The current raw cache is 709,222,400 bytes, 97.44% of the SQLite file; all 576 rows were stale when
measured. Average payload is 1.23 MB, above DynamoDB's 400-KiB item limit. Raw responses are sensitive,
replaceable and excluded from RPO. The public deployment has only generated fixtures.

## Options

| Option | Monthly cost / effect | Trade-off |
| --- | ---: | --- |
| Keep payloads in PostgreSQL | At least 0.71 GB now; storage dollar delta fits 20 GiB but backup/restore/IO surface grows **48.9×** | Transactional metadata, but contaminates the relational recovery domain with replaceable JSON. |
| S3 Standard + 24h lifecycle | Measured conservative volume **≤$0.11/month** | Cheap object fit, lifecycle and bucket policy; replay disappears after one day. |
| DynamoDB/TTL cache | Payload must be chunked due 400-KiB item limit; request/storage charge variable | Native TTL, but chunk assembly/partial expiry is complexity with no benefit. |
| Compressed local filesystem, bounded TTL | **$0 incremental** inside existing disk | Best local mode; shares host/disk fate and is not cross-worker cache. |
| Do not persist | **$0** | Smallest risk/storage; loses short replay/debug window and may increase provider calls. |

## Decision

Public synthetic mode has no raw cache table, bucket or read path. Fixture artifacts are versioned
build inputs, not cache.

Private AWS **if applied** uses compressed S3 Standard objects with opaque keys, block-public-access,
TLS/bucket-policy enforcement, AWS-managed encryption, no versioning/replication and expiry within
24 hours. Metadata in PostgreSQL is bounded and contains no raw URL/SWID. Normal API/export roles
cannot list or read objects. Existing 709 MB is not migrated.

Private local mode writes compressed objects under a configured cache root outside SQLite, expires
at 24 hours, caps total allocation at 1 GiB using oldest-first deletion, and cleans at start/end.
File permissions are owner-only and the directory is excluded from DB backup, Git and fixture tools.
FileVault/full-disk encryption is the at-rest boundary. Cache failure becomes a miss.

## Consequences and cost

Deployed public cost is **$0/month**. Conditional private AWS is **≤$0.11/month**, using 0.71 GB at
$0.023/GB-month plus about 576 PUT/day at $0.005/1,000; compression only reduces it
([S3 pricing](https://aws.amazon.com/s3/pricing/)). Local cost is incremental $0 within the selected
disk.

Accepted failure: replay material vanishes within one day or on local disk loss; a miss performs a
provider call under the global limiter. The design intentionally does not restore cache during DR.

## Revisit when

Eliminate persistence if measured hit/replay value is negligible. Extend retention only with a
documented incident/debug need and updated privacy/backup model. Reconsider a shared TTL store when
multiple private workers need cross-host cache and S3 latency is measured as material.

