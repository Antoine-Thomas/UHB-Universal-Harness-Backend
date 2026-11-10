"""Optional local services — downloaded, built and launched by UHB.

Nothing here ships a binary. The repository carries the code and the installer;
the heavy artefacts (SiYuan ~240 MB, the embedding model ~450 MB, the .NET bridge)
land in `uhb/bin/` on the machine that installs them, and `uhb/bin/` is not versioned.

    uhb brain start --dry-run      # what would be launched, and from where
    install_services.ps1           # Windows: SiYuan
    ./install_services.sh          # Linux:  SiYuan

Each sub-directory explains its own limits:

- `rag/`   the RAG service code; the *index* is yours to build, it is not shipped
- `mari/`  an external .NET service; it has no upstream, so there is nothing to fetch
"""
