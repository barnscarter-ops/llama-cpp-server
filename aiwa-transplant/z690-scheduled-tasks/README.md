# Z690 scheduled tasks — exported 2026-08-17

39 task definitions exported from `CARTERSPC` before the teardown, because
**scheduled tasks live in the Windows task store, not on `C:`** — they do not
travel with the SN7100 and would otherwise be lost with the old install.

Microsoft, GoogleUpdater, SoftLanding, NVIDIA-updater and vendor-noise tasks
were excluded; what remains is the real automation surface.

## Re-registering on the X870E

```powershell
Get-ChildItem *.xml | ForEach-Object {
  Register-ScheduledTask -Xml (Get-Content $_.FullName -Raw) -TaskName $_.BaseName
}
```

Register them **one at a time, deliberately** rather than in a blind loop —
several will fail or misbehave until their prerequisites exist:

- Every action referencing `C:\Workspace\...` breaks if the SN7100 does not
  come back as `C:`. Check the paths against the drive letter you actually get.
- `S4U` and `InteractiveToken` logon types re-prompt for the account. The
  exports carry no passwords (Windows does not export them).
- `UserId` values reference `CARTERSPC\carte` and the old machine SID
  (`S-1-5-21-3692995547-1880394738-1435933407-1001`). These need re-pointing to
  the new machine's account.
- Anything depending on PM2, Python, Node, or `aos.exe` needs those installed
  first, or the task registers fine and then fails silently every run.

## Verified free of secrets

Scanned for password/token/key/bearer/`sk-`/`ghp_`/`AKIA` patterns before being
written here. The only matches are task *names* and a reminder *description* —
no credential values. Safe to commit.

## Surfaced while exporting: a token expiry that outlives this PC

`Grizzly FB Page Token Renewal` is a one-shot reminder that
**`FB_PAGE_ACCESS_TOKEN` data access expires 2026-09-09** — about three weeks
out. It must be replaced with a never-expiring System User page token, or
Grizzly boosting and posting break.

The reminder itself is a scheduled task on the machine being torn down. If it
is not re-registered on the X870E, the only thing that was going to warn about
this expiry disappears tonight and the failure surfaces in September as a
silent posting outage. Re-register it early, or handle the token now.
