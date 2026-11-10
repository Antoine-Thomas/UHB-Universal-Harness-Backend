# RAG service — `POST /search` on 127.0.0.1:8200

Two files, no rewriting: `serveur_rag.py` (FastAPI surface) and `chercher.py` (the
search engine: faiss index + lexical rerank + flashrank).

## What is shipped, and what is not

| | |
| --- | --- |
| **Shipped** | the service code and its `requirements.txt` |
| **Not shipped** | `index.faiss`, `chunks.jsonl`, `manifeste.json` — they are built from **your** notes, and they are the second brain, not the software |

That distinction is the point: a fresh clone starts with an **empty** brain. `GET /sante`
then answers `fragments: 0`, and `/search` returns nothing. UHB reports that honestly
(`uhb brain status` → `0 fragments`), it does not invent results.

## Install

```bash
python -m pip install -r uhb/services/rag/requirements.txt
```

Roughly 1.5 GB with the CPU torch wheel; the embedding model (`intfloat/multilingual-e5-base`,
in `manifeste.json`) is downloaded from Hugging Face on first use.

## Build an index

The indexer that produced the reference index is **not** included: it walks
Hermes-specific paths (skills directory, wiki, a SiYuan workspace, a WordPress repo).
Write your own, or adapt one — the contract is small:

- `chunks.jsonl` — one JSON object per chunk: `{"source", "titre", "partie", "texte", "chemin"}`
- `index.faiss` — a faiss index whose vectors are the e5 embeddings of those chunks
- `manifeste.json` — `{"modele": "intfloat/multilingual-e5-base", "fragments": N, ...}`

Then restart the service (or `POST /recharger`) so it reloads from disk.

## Run

```bash
python uhb/services/rag/serveur_rag.py --port 8200
# or, if installed:  uhb brain start --only rag
```

| Endpoint | Answer |
| --- | --- |
| `GET /sante` | `fragments`, `modele`, `par_source`, `index_mo`, `mis_a_jour` |
| `POST /search` | `{"question": "...", "k": 5}` → `{"resultats": [{score, cos, source, titre, partie, extrait, chemin}]}` |
| `POST /v1/embeddings` | OpenAI-compatible embeddings |
| `POST /recharger` | reload the index from disk |

## Limits, stated plainly

- No index → no results. The service is a search engine, not a knowledge base.
- Paths in `chemin` are the raw paths recorded at index time (absolute on the machine
  that built the index). UHB shortens them to `~` for display only.
- The first call after a restart pays the model load (several seconds), by design.
