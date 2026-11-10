# MARI bridge — `GET /status` on 127.0.0.1:55553

MARI is **not** part of this repository, and it has no public upstream: it is a .NET 8
service (Orleans) built from its own source tree. Pretending to download it would be the
one lie this repository refuses to tell.

## What UHB does with it

Exactly one call, read-only:

```
GET http://127.0.0.1:55553/status  ->  {"status": "ok", "signalsEmitted": 2}
```

`POST /fire` exists upstream; UHB never calls it — sending a signal is an action, not a
status read, and UHB only reads.

## If you have the source

```bash
# from the repository root, with the .NET SDK installed:
python -m uhb.services.install --with mari --mari-src <path to the bridge project>
# -> builds into uhb/bin/mari/ and checks that the executable is there
```

Then `uhb brain start --only mari` launches it, and `uhb brain status` should show a green
dot for `mari`.

## If you do not

Nothing breaks. `uhb brain status` prints `mari  ○  ...` with the reason, and the rest of
UHB — modes, router, wiki, panel — works exactly the same. The second brain is optional by
construction; that is why the adapter treats a down service as a *state*.
