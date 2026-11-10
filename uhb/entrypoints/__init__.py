"""Entrypoints: the shapes UHB takes when it is launched by a harness.

* ``cli``          — ``uhb`` command, for humans and scripts
* ``fake_claude``  — a ``claude``-compatible binary, for Harness Maxxing
* ``server``       — HTTP, Anthropic and OpenAI compatible, for OpenClaw and others

Nothing is imported here at package import time: each entrypoint pulls its own
dependencies so that ``uhb.entrypoints.cli`` does not require a server stack.
"""
