# Security policy

LimitBar intentionally reuses local CLI authentication and never asks users to paste OAuth tokens into configuration files.

Please do not attach `%USERPROFILE%\.claude\.credentials.json`, `%USERPROFILE%\.codex\auth.json`, raw HTTP headers, or unredacted logs to bug reports. A normal LimitBar log should contain no secrets, but review it before sharing.

The cache at `%LOCALAPPDATA%\LimitBar\snapshots.json` contains only provider names, percentages, reset timestamps, durations, source labels and fetch timestamps.

