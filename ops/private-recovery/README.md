# Private recovery operator runbook

Phase 30 recovery is supported only by the native macOS process. It is intentionally absent from
Docker. Never place the repository password in `.env`, argv, a file, logs, or an agent task.

## Human-gated setup

1. Enable FileVault and complete its restart/recovery-key flow. Do not begin a restore drill until
   `fdesetup status` reports FileVault on.
2. Attach a directly connected physical volume. A second APFS volume, disk image, network share, or
   path on the laptop's physical disk does not qualify.
3. Put the automation password in macOS Keychain under service
   `com.espn-edge.private-recovery` and the current macOS account. Store an independent break-glass
   copy on neither the laptop nor backup volume. Never send either value to an agent.
4. Point `RECOVERY_CREDENTIAL_COMMAND` at the absolute, operator-owned installed copy of
   `keychain-password`; set its mode to `0700`. Point `RECOVERY_RESTIC_PATH` at the verified pinned
   binary and `RECOVERY_RESTIC_SHA256` at that binary's approved digest.
5. Run `python -m api.recovery doctor --json`. Output is deliberately path-, identifier-, count-,
   and secret-free. Resolve every failed check before continuing.
6. Separately approve and run repository initialization, launchd load, one manual backup, retention
   dry-run, exact repository-scoped retention apply, and the post-retention restore drill. These are
   distinct mutations; contract acceptance is not permission for them.

`runner install --load` installs and loads the hourly backup agent only; it does not install or load
recurring retention. Run `retention --dry-run --drill-snapshot latest` (or substitute one exact full
64-hex snapshot identifier) separately. The exact `retention --enable --apply --drill-snapshot
latest` transition, again allowing an operator-selected exact full identifier, is the separately
approved destructive boundary: it performs
the retention application, then atomically installs and loads the recurring scheduled runner and
arms persisted state. If runner load or state arming fails, the scheduled runner is rolled back and
recurrence remains unarmed and fail-closed. Each retention run independently dry-runs and rebinds
the repository ID, physical/object identity, inventory, selected drill point and survivor-plan
digest immediately before its destructive call. The selected drill point and current point must
both survive; scheduled retention selects the current point internally. The identifier is accepted
only as an operator command argument and is never printed or persisted. Do not claim retention is
enforced until the post-retention restore drill passes. Never place a repository secret in the
command, output, evidence, or agent context.

## Failure behavior

The hourly launchd job may miss runs while the laptop sleeps, the volume is absent, or Keychain is
locked. Status shows actual coverage age. With `RECOVERY_REQUIRED=true`, a missing, corrupt,
retention-unverified, or 24-hour-old recovery point blocks league sync, uncached AI generation,
opportunity refresh, and FFC ADP refresh. Reads and account/league administration remain available.

If the automation path fails, keep it unavailable and run `make recovery-restore` from a controlling
terminal. The application displays exactly `ESPN Edge break-glass repository password: ` once,
accepts one hidden entry for at most five minutes, and supplies it only to the three non-interactive
restic verification/read operations. Enter the independent break-glass secret there only; never put
it in the shell command, chat, an agent task, `.env`, Keychain, a file, logs, or evidence. Do not copy
the source database as a shortcut. A failed drill does not repair or mutate the source database.
