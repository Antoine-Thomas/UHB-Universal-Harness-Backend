# Docker Compose — end-to-end proof

Captured on 2026-11-16T00:25:33+01:00 from a clean `docker compose build` on Windows 11,
Docker 29.9.3 / compose Docker Compose version v5.7.0.

Everything below is verbatim command output. Section 9 keeps the failures this
run produced before their fix — six defects, each pinned by a test.

## 1. The stack starts

```console
$ docker compose ps
NAME           IMAGE                  COMMAND                  SERVICE   CREATED          STATUS          PORTS
uhb-rag-1      uhb-rag                "python serveur_rag.…"   rag       7 minutes ago    Up 6 minutes    127.0.0.1:8200->8200/tcp
uhb-siyuan-1   b3log/siyuan:v3.8.10   "/opt/siyuan/entrypo…"   siyuan    6 minutes ago    Up 6 minutes    127.0.0.1:6806->6806/tcp
uhb-uhb-1      uhb-uhb                "uhb run --host 0.0.…"   uhb       29 seconds ago   Up 24 seconds   127.0.0.1:8101->8101/tcp
```

## 2. `GET /status`

```console
$ curl -s http://127.0.0.1:8101/status
{
    "connected": true,
    "version": "0.1.2",
    "mode": "chat",
    "harness": "hermes",
    "providers": [
        {
            "name": "anthropic",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "models": [],
            "key_env": "ANTHROPIC_API_KEY",
            "key_loaded": false,
            "local": false
        },
        {
            "name": "deepseek",
            "protocol": "openai",
            "base_url": "https://api.deepseek.com/v1",
            "models": [
                "deepseek-flash"
            ],
            "key_env": "DEEPSEEK_API_KEY",
            "key_loaded": false,
            "local": false
        },
        {
            "name": "kimi",
            "protocol": "openai",
            "base_url": "https://api.moonshot.cn/v1",
            "models": [],
            "key_env": "KIMI_API_KEY",
            "key_loaded": false,
            "local": false
        },
        {
            "name": "nous",
            "protocol": "openai",
            "base_url": "https://inference-api.nousresearch.com/v1",
            "models": [],
            "key_env": "NOUS_API_KEY",
            "key_loaded": false,
            "local": false
        },
        {
            "name": "ollama",
            "protocol": "openai",
            "base_url": "http://host.docker.internal:11434/v1",
            "models": [
                "qwen3:8b"
            ],
            "key_env": "OLLAMA_API_KEY",
            "key_loaded": false,
            "local": true
        },
        {
            "name": "openai",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "models": [],
            "key_env": "OPENAI_API_KEY",
            "key_loaded": false,
            "local": false
        },
        {
            "name": "openrouter",
            "protocol": "openai",
            "base_url": "https://openrouter.ai/api/v1",
            "models": [],
            "key_env": "OPENROUTER_API_KEY",
            "key_loaded": false,
            "local": false
        }
    ],
    "modes": [
        "chat",
        "dev",
        "observe",
        "review"
    ],
    "docs": {
        "manual": "/docs/manuel.pdf",
        "manual_en": "/docs/manual.pdf",
        "status": "/status",
        "panel": "/"
    },
    "endpoints": {
        "openai": "/v1/chat/completions",
        "anthropic": "/v1/messages",
        "models": "/v1/models",
        "health": "/health"
    }
}
```

## 3. `POST /route` — the plan, without spending a token

```console
$ curl -s -X POST http://127.0.0.1:8101/route -H "content-type: application/json" \
      -d '{"prompt":"corrige ce bug de parsing dans le routeur et explique la cause racine"}'
{
  "mode": "chat",
  "complexity": "trivial",
  "score": 0,
  "signals": {
    "length": 0,
    "code": 0,
    "multi_step": 0,
    "paths": 0,
    "multi_question": 0
  },
  "allow_paid": false,
  "reason": "chat/trivial -> free/nous/meituan/longcat-2.5:free (9 candidate(s): nous/meituan/longcat-2.5:free > openrouter/nvidia/nemotron-3-ultra-550b-a55b:free > openrouter/recursive/flint-v1.0:free > nous/upstage/solar-mini4:free > openrouter/google/gemma-4-31b-it:free > nous/stepfun/step-3.7-flash:free > ollama/qwen3:8b > deepseek/deepseek-flash > deepseek/deepseek-v4-pro)"
}
chain, in order:
  free nous/meituan/longcat-2.5:free
  free openrouter/nvidia/nemotron-3-ultra-550b-a55b:free
  free openrouter/recursive/flint-v1.0:free
  free nous/upstage/solar-mini4:free
  free openrouter/google/gemma-4-31b-it:free
  free nous/stepfun/step-3.7-flash:free
  free ollama/qwen3:8b
  eco deepseek/deepseek-flash
  eco deepseek/deepseek-v4-pro
```

## 4. `GET /rag/status`

```console
$ curl -s http://127.0.0.1:8101/rag/status
{
    "built": false,
    "source": "service",
    "last_indexed": null,
    "documents": null,
    "chunks": 0,
    "index_bytes": 0,
    "service": true,
    "service_url": "http://rag:8200",
    "path": "/usr/local/lib/python3.11/site-packages/uhb/services/rag"
}
```

## 5. `GET /siyuan/blocks`

```console
$ curl -s "http://127.0.0.1:8101/siyuan/blocks?limit=3"
{
    "count": 3,
    "blocks": [
        {
            "id": "20261116072329-zrj9yf0",
            "parent_id": "",
            "type": "d",
            "content": "uhb-compose-proof",
            "hpath": "/uhb-compose-proof",
            "updated": "20261116072329"
        },
        {
            "id": "20261116072329-66aqakq",
            "parent_id": "20261116072329-zrj9yf0",
            "type": "h",
            "content": "UHB compose proof",
            "hpath": "/uhb-compose-proof",
            "updated": "20261116072329"
        },
        {
            "id": "20261116072329-9tbc6l7",
            "parent_id": "20261116072329-66aqakq",
            "type": "p",
            "content": "Three services started by docker-compose.yml: uhb, rag, siyuan.",
            "hpath": "/uhb-compose-proof",
            "updated": "20261116072329"
        }
    ],
    "url": "http://siyuan:6806",
    "path": null
}
```

## 6. `POST /rag/reindex` — honest about a missing index

```console
$ curl -s -X POST http://127.0.0.1:8101/rag/reindex -H "content-type: application/json" -d "{}"
{
    "error": {
        "message": "the RAG service refused the reload (HTTP 503): manifeste illisible : [Errno 2] No such file or directory: '/app/manifeste.json'",
        "type": "memory"
    }
}
```

## 7. Logs — no traceback, three services up

```console
$ docker compose logs --tail=20
rag-1     | RAG local sur http://0.0.0.0:8200 — aucun index (fragments illisibles) : /sante repond 0, /search repond 503
uhb-1     |   UHB 0.1.2 · Universal Harness Backend
uhb-1     |   mode      chat
rag-1     | INFO:     Started server process [1]
rag-1     | INFO:     Waiting for application startup.
rag-1     | INFO:     Application startup complete.
rag-1     | INFO:     Uvicorn running on http://0.0.0.0:8200 (Press CTRL+C to quit)
rag-1     | INFO:     172.19.0.1:51832 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.1:51608 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.1:52542 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.4:46480 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.1:55194 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.1:46708 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.1:45370 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.4:52308 - "POST /recharger HTTP/1.1" 503 Service Unavailable
rag-1     | INFO:     172.19.0.1:49170 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.4:54310 - "POST /recharger HTTP/1.1" 503 Service Unavailable
rag-1     | INFO:     172.19.0.4:33638 - "GET /sante HTTP/1.1" 200 OK
rag-1     | INFO:     172.19.0.4:33644 - "POST /recharger HTTP/1.1" 503 Service Unavailable
uhb-1     |   providers 1/7 utilisables
uhb-1     |   panel     http://0.0.0.0:8101/
uhb-1     |   manuel    http://0.0.0.0:8101/docs/manuel.pdf
uhb-1     | 
uhb-1     | uhb server on http://0.0.0.0:8101 (loopback by default)
siyuan-1  | I 2026/11/16 07:19:24 cert.go:75: generating TLS server certificates signed by local CA...
siyuan-1  | I 2026/11/16 07:19:24 blocktree.go:86: reinitialized database [/siyuan/workspace/temp/blocktree.db]
siyuan-1  | I 2026/11/16 07:19:24 cert.go:81: generated TLS certificates at [/siyuan/workspace/conf]
siyuan-1  | I 2026/11/16 07:19:24 serve.go:253: kernel [pid=1] http server [0.0.0.0:6806] is booting (local HTTPS + HTTP/2 enabled)
siyuan-1  | I 2026/11/16 07:19:24 database.go:146: the database structure is changed, rebuilding database...
siyuan-1  | I 2026/11/16 07:19:24 blocktree.go:86: reinitialized database [/siyuan/workspace/temp/blocktree.db]
siyuan-1  | I 2026/11/16 07:19:24 database.go:161: reinitialized database [/siyuan/workspace/temp/siyuan.db]
siyuan-1  | I 2026/11/16 07:19:24 conf.go:1163: tree/block count [0/0]
siyuan-1  | I 2026/11/16 07:19:24 working.go:222: kernel booted
siyuan-1  | I 2026/11/16 07:19:24 manager.go:156: kernel plugins are disabled by configuration, skipping start
siyuan-1  | I 2026/11/16 07:19:25 box.go:72: auto stat [trees=0, blocks=0, dataSize=45.06 kB, assetsSize=0 B]
siyuan-1  | I 2026/11/16 07:19:26 cron.go:57: cron job [[RefreshRhyResultJob]] executed
siyuan-1  | W 2026/11/16 07:22:06 session.go:467: invalid api token [ip=172.19.0.4]
siyuan-1  | W 2026/11/16 07:22:12 session.go:467: invalid api token [ip=172.19.0.1]
siyuan-1  | I 2026/11/16 07:22:24 session.go:136: auth success [ip=172.19.0.1:59610, maxAge=0]
siyuan-1  | W 2026/11/16 07:22:30 session.go:467: invalid api token [ip=172.19.0.1]
siyuan-1  | W 2026/11/16 07:22:30 session.go:467: invalid api token [ip=172.19.0.1]
siyuan-1  | I 2026/11/16 07:22:58 mount.go:112: created box [20261116072258-ksmil2o]
siyuan-1  | I 2026/11/16 07:22:59 index.go:337: rebuilt database for notebook [20261116072258-ksmil2o] in [0.02s], tree [count=0, size=0 B]
siyuan-1  | I 2026/11/16 07:22:59 index.go:432: resolved refs [0] in [4ms]
```

## 8. `docker compose down` — clean teardown

```console
$ docker compose down
 Container uhb-uhb-1 Stopping 
 Container uhb-uhb-1 Stopped 
 Container uhb-uhb-1 Removing 
 Container uhb-uhb-1 Removed 
 Container uhb-siyuan-1 Stopping 
 Container uhb-rag-1 Stopping 
 Container uhb-rag-1 Stopped 
 Container uhb-rag-1 Removing 
 Container uhb-rag-1 Removed 
 Container uhb-siyuan-1 Stopped 
 Container uhb-siyuan-1 Removing 
 Container uhb-siyuan-1 Removed 
 Network uhb_default Removing 
 Network uhb_default Removed 

$ docker compose ps -a
NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS
```

## 9. Defects this run found — and what fixed them

Every block below is a real failure output, printed before the fix.

### 9.1 `rag` could not start without an index

```
Traceback (most recent call last):
  File ".../uhb/services/rag/serveur_rag.py", line 187, in <module>
    m = manifeste()
  File ".../uhb/services/rag/serveur_rag.py", line 99, in manifeste
    raise HTTPException(status_code=503, detail="manifeste illisible : %s" % e)
fastapi.exceptions.HTTPException: 503: manifeste illisible : [Errno 2] No such file or directory: '.../manifeste.json'
```

The service refused to boot without the index it does not ship — while its own
README promised "a fresh clone starts with an empty brain, `/sante` answers
`fragments: 0`". Fixed: it starts, `/sante` returns `200 {"status":
"sans_index", "fragments": 0}`, `/search` and `/recharger` return 503 (never an
empty result set). Tests: `uhb/tests/test_rag_service.py`.

### 9.2 The `uhb` image died at import

```
ModuleNotFoundError: No module named 'yaml'
  File "/usr/local/lib/python3.11/site-packages/uhb/core/router.py", line 249, in _load_yaml
```

`pip install .` does not install the `yaml` extra, and the container mounts
`config.docker.yaml` at `/app/config.yaml`. Fixed: `pip install ".[yaml]"`.
Test: `test_the_image_installs_the_yaml_extra`.

### 9.3 SiYuan 3.8.10 refused to boot with an empty access code

```
the access authorization code or a valid OIDC configuration must be set when deploying via Docker
E ... Docker access authentication configuration is invalid: remote access requires at least one authentication method
```

The compose default was `${SIYUAN_AUTH_CODE:-}` — empty. Fixed: the variable is
now required (`${SIYUAN_AUTH_CODE:?...}`), so `docker compose config` fails with
an instruction instead of a crash loop; a `.env` file next to the compose file
carries it.

### 9.4 The access code is not the API token

```
W session.go:467: invalid api token [ip=172.19.0.4]
{"code":-1,"msg":"Auth failed [header: Authorization]"}
```

`/siyuan/blocks` answered 401 in the container. SiYuan authenticates its HTTP API
with the token it generates in `<workspace>/conf/conf.json` (`api.token`), not with
`--accessAuthCode`. Fixed: the compose file reads `SIYUAN_API_TOKEN` into
`SIYUAN_TOKEN`, with the workspace path in the comment. With it set, section 5
returns 3 real blocks.

### 9.5 `/status` called the container's Ollama remote

The first capture said `"name": "ollama", "local": false`, while the router's
`is_paid` called the same provider free. `providers_facts` read the URL
(`host.docker.internal` is not loopback) and ignored the declared `local: true`.
Fixed: the flag wins. It matters because the widget's dot turns green on
`key_loaded || local`. Test: `test_the_container_reports_its_local_ollama_as_local`.

### 9.6 `/rag/reindex` and `/rag/status` ignored the container address

```
{"error": {"message": "the RAG service is not reachable at http://127.0.0.1:8200 (ConnectError); ..."}}
```

`rag_status` had no override at all, and the POST route defaulted to the
module constant. Both now read `UHB_RAG_URL` at call time, and a refused reload
carries the service's own reason:

```
the RAG service refused the reload (HTTP 503): manifeste illisible : [Errno 2] No such file or directory: '/app/manifeste.json'
```

