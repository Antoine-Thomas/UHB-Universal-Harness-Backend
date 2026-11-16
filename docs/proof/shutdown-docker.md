# The Docker shutdown, proved on a real stack (P1-6)

What was missing after the audit: `POST /shutdown` existed with three targets
(`self`, `local`, `docker`) and a token, but nothing had ever exercised the
`docker` target against a live project. The unit tests (`uhb/tests/test_shutdown.py`,
25) pin the plan and the refusal paths with a `FakeRunner`; this document is the
other half — real containers, real `docker compose down`, real ports.

Run date: 2026-11-16. Host: Windows 11, Docker Desktop, project
`<checkout>` (UHB), compose file `docker-compose.yml`, three services declared
(`uhb`, `rag`, `siyuan`).

## Why the proof brings up one container and stops it from a host server

Two constraints, both measured during this work, decide the shape of the test:

1. **A containerised UHB cannot run the `docker` target.** There is no Docker
   socket inside the container, and `uhb/entrypoints/shutdown.py` says so
   explicitly: `in_container()` reduces the target list to `self`. Asking the
   container for `docker` returns a 400 with the reason, which is the correct
   behaviour, not a bug.
2. **The full stack cannot start on the reference machine**: ports 8200 and 6806
   belong to the operator's own RAG and SiYuan. `docker compose up -d` therefore
   fails on `rag` with `bind: Only one usage of each socket address`.

So the proof does what an operator does when they click Power in the interface:
the containers run in the project, and the *host* UHB — the instance that owns
`docker compose` — receives the request.

## The test

`uhb/tests/test_docker_shutdown_proof.py`, opt-in only:

```bash
UHB_DOCKER_E2E=1 python -m pytest uhb/tests/test_docker_shutdown_proof.py -s -q
```

No `sleep` anywhere: readiness is an HTTP probe on `/status`, and every wait
polls its own predicate (`compose ps` empty, port closed, `/status` 200). The
teardown runs `docker compose down` whatever happens.

Sequence, as executed:

1. snapshot `docker network ls` and `docker volume ls`, and write
   `uhb/data/siyuan/proof-marker.txt` (the bind mount that holds real data);
2. `docker compose build uhb` — the image must carry the route: a stale image
   answered `404` on `/shutdown` in the first attempt and made the route look
   broken;
3. `docker compose up -d --no-deps uhb`;
4. start a host `uhb run --mode dev --port <free>` with the same shutdown token,
   wait for `/status` 200;
5. `POST /shutdown` to the **host** server with
   `{"confirm": true, "stop": ["docker"]}` and the token in `X-UHB-Token`;
6. containers removed, port 8101 closed, network `uhb_default` gone;
7. the host server must still answer — `stop` was honoured, `self` was not sent;
8. volumes identical to the snapshot, marker file intact.

## Result

```
$ docker compose ps -a -q
AVANT: conteneurs=[] port 8101 ecoute=False
$ docker compose build uhb
   Image uhb-uhb Built
$ docker compose up -d --no-deps uhb
   Network uhb_default Created
   Container uhb-uhb-1 Started
$ docker compose ps -a -q
  7bd6d2a134bbb59a54af40c91e458ff634f00b9e54ea884634f0f279521d23e5
READY: conteneur en place, /status 200
APRES: aucun conteneur, port 8101 libre
$ docker network ls --format {{.Name}}
  bridge
  host
  none
  single-node_default
APRES: le serveur hote repond toujours (cible self exclue)
$ docker volume ls --format {{.Name}}
  n8n_data
  single-node_filebeat_etc
  ...
  wazuh-dashboard-config
  wazuh-dashboard-custom
APRES: volumes inchanges, marqueur du bind mount intact
$ docker compose down --timeout 10

1 passed in 23.31s
```

## What each line proves

| Line | What it establishes |
|---|---|
| `AVANT: conteneurs=[]` | the proof starts from a clean project, not from a leftover |
| `READY: /status 200` | readiness measured, not assumed |
| `APRES: aucun conteneur` | `down`, not `stop`: the container is gone from `ps -a` |
| `APRES: … port 8101 libre` | the port is freed, checked by connecting |
| `uhb_default` absent | the project network is removed |
| `single-node_default` still there | the operator's other project is untouched |
| `le serveur hote repond toujours` | `stop: ["docker"]` did not widen into `self` |
| volumes identical | no `-v` was passed, nothing was created or destroyed |
| marker intact | the bind mount survived: this is the data a real operator loses |

This compose file declares **no named volume** — it mounts `./uhb/data/siyuan`
and `./uhb/services/rag`. "Volumes intact" is therefore measured two ways: the
set of Docker volumes is unchanged (empty for this project, before and after),
and the bind-mounted marker file still holds its content.

## A finding this proof produced: two tokens, two worlds

First attempt, the route answered `401` although the host had a token:

```
the route refused with 401: {'error': {'message': "a shutdown token is required:
send it in the X-UHB-Token header (the operator's copy is
/root/.uhb/shutdown-token)", 'type': 'unauthorized', 'header': 'X-UHB-Token'}}
```

A containerised UHB generates its token inside the container (`/root/.uhb/…`);
the host's copy is elsewhere. The panel served by that container embeds the
container's token, so it works — but a host process (UH, a script) cannot stop
it, and falls back to terminating a process it may not have started.

`docker-compose.yml` now passes `UHB_SHUTDOWN_TOKEN: ${UHB_SHUTDOWN_TOKEN:-}` to
the service, so an operator can share one value:

```bash
export UHB_SHUTDOWN_TOKEN="$(cat ~/.uhb/shutdown-token)"   # or put it in .env
docker compose up -d
```

Left empty, the container keeps generating its own: its own panel still works,
and a host `uh web` falls back to the process tree — exactly the behaviour that
existed before the route.

## Not covered here

- the `rag` and `siyuan` containers: they cannot start on this machine (ports),
  see `docs/proof/docker-compose-e2e.md` for their boot and health checks;
- the `local` target (the operator's RAG and SiYuan processes): proving it means
  stopping services the operator is using, so it is exercised by the unit tests
  with a fake runner and stays an explicit interface action;
- the `self` target inside a container: covered by `docs/proof/shutdown-proof.md`.

## Reproducing

```bash
cd <checkout>
docker compose down --timeout 10        # a clean project
UHB_DOCKER_E2E=1 python -m pytest uhb/tests/test_docker_shutdown_proof.py -s -q
```
