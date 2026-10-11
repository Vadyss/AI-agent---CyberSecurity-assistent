# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Translate only known sandbox diagnostics at the human display boundary."""
from __future__ import annotations

import re


_MESSAGES = {
    "Úloha musí být JSON objekt.": "The job must be a JSON object.",
    "Neplatný identifikátor úlohy.": "Invalid job identifier.",
    "Neplatný režim nebo vstupní bod úlohy.": "Invalid job mode or entrypoint.",
    "Neplatné názvy souborů úlohy.": "Invalid job file names.",
    "Chybí soubor dovednosti nebo testů.": "The skill or test file is missing.",
    "Obsah souborů musí být text.": "File contents must be text.",
    "Neplatný vstup nebo parametry.": "Invalid inputs or parameters.",
    "Import není v bezpečném seznamu modulů.": "The import is not in the allowed module list.",
    "Časový limit musí být větší než 0 a nejvýš 30 sekund.": "Timeout must be greater than 0 and at most 30 seconds.",
    "Úlohu nelze serializovat do JSON.": "The job cannot be serialized as JSON.",
    "Úloha překračuje limit velikosti vstupu.": "The job exceeds the input size limit.",
    "Překročen časový limit.": "Time limit exceeded.",
    "Výstup překračuje limit velikosti.": "The output exceeds the size limit.",
    "Neplatný výstup runneru.": "Invalid runner output.",
    "Runner selhal nebo překročil limit zdrojů.": "The runner failed or exceeded a resource limit.",
    "Tělo úlohy překračuje 8 MB.": "The job body exceeds 8 MB.",
    "Neplatný JSON úlohy.": "Invalid job JSON.",
    "Sandbox nepovoluje soubory, síť ani podprocesy.": "Sandbox does not allow files, network access or subprocesses.",
    "Sandbox nepovoluje introspekci atributů.": "Sandbox does not allow attribute introspection.",
    "Sandbox nepovoluje soukromé atributy modulů.": "Sandbox does not allow private module attributes.",
    "Sandbox nepovoluje přístup k interním modulům.": "Sandbox does not allow access to internal modules.",
    "Sandbox nepovoluje soukromé atributy.": "Sandbox does not allow private attributes.",
    "Sandbox nepovoluje přístup k souborům.": "Sandbox does not allow file access.",
    "Výstup dovednosti musí být seznam.": "The skill output must be a list.",
    "Výstup překračuje limit 10 MB.": "The output exceeds the 10 MB limit.",
}
_EXCEPTIONS = ("ImportError: ", "PermissionError: ", "TypeError: ", "ValueError: ")
_FORBIDDEN_IMPORT = re.compile(r"Zakázaný import: ([A-Za-z_][A-Za-z0-9_.]*)\.")


def present_sandbox_error(message: str) -> str:
    """Keep unknown/user strings intact, including exception names and imports."""
    prefix = next((name for name in _EXCEPTIONS if message.startswith(name)), "")
    detail = message[len(prefix):]
    translated = _MESSAGES.get(detail)
    if translated is not None:
        return prefix + translated
    forbidden = _FORBIDDEN_IMPORT.fullmatch(detail)
    if forbidden:
        return prefix + f"Forbidden import: {forbidden.group(1)}."
    return message
