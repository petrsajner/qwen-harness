"""Persistentní research ledger a vícefázová syntéza bez filtrování zdrojů."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


class ResearchLedger:
    def __init__(self, session):
        self.session = session
        self.path = session.dir / "research.json"
        self.data = self._load()
        self.run_id: str | None = None

    def begin(self, question: str) -> str:
        self.run_id = f"research-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.data.setdefault("runs", []).append({
            "id": self.run_id,
            "question": question,
            "created": time.time(),
            "queries": [],
            "plan": None,
            "candidates": [],
            "sources": [],
            "status": "collecting",
            "synthesis": None,
        })
        self._save()
        return self.run_id

    def set_plan(self, plan: dict) -> None:
        run = self.current()
        if run is None:
            return
        run["plan"] = plan
        self._save()

    def current(self) -> dict | None:
        if self.run_id:
            return next((run for run in self.data.get("runs", [])
                         if run.get("id") == self.run_id), None)
        runs = self.data.get("runs", [])
        return runs[-1] if runs else None

    def record_query(self, query: str, results: list[tuple[str, str, str]]) -> None:
        run = self.current()
        if run is None:
            self.begin(query)
            run = self.current()
        run["queries"].append({"query": query, "timestamp": time.time()})
        known = {item.get("url") for item in run["candidates"]}
        for title, url, snippet in results:
            if not url or url in known:
                continue
            run["candidates"].append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "found_by_query": query,
            })
            known.add(url)
        self._save()

    def record_source(self, url: str, title: str, content: str,
                      content_type: str = "text/html", requested_url: str | None = None,
                      max_chars: int = 200_000) -> str:
        run = self.current()
        if run is None:
            self.begin(url)
            run = self.current()
        existing = next((source for source in run["sources"] if source.get("url") == url), None)
        source_id = existing.get("id") if existing else f"S{len(run['sources']) + 1}"
        record = {
            "id": source_id,
            "title": title or url,
            "url": url,
            "requested_url": requested_url or url,
            "content_type": content_type,
            "content": content[:max_chars],
            "content_truncated": len(content) > max_chars,
            "original_char_count": len(content),
            "fetched_at": time.time(),
        }
        if existing:
            existing.update(record)
        else:
            run["sources"].append(record)
        self._save()
        return source_id

    def status(self) -> dict:
        run = self.current()
        if run is None:
            return {"active": False, "queries": 0, "candidates": 0, "sources": 0,
                    "status": "idle"}
        return {
            "active": True,
            "run_id": run["id"],
            "question": run["question"],
            "queries": len(run["queries"]),
            "candidates": len(run["candidates"]),
            "sources": len(run["sources"]),
            "status": run["status"],
        }

    def complete(self, synthesis: str) -> None:
        run = self.current()
        if run is None:
            return
        run["status"] = "complete"
        run["completed_at"] = time.time()
        run["synthesis"] = synthesis
        self._save()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"runs": []}
        except (OSError, ValueError):
            return {"runs": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def _ask(llm, prompt: str, max_tokens: int) -> str:
    result = llm.ask(
        [
            {"role": "system", "content": (
                "You are a loss-aware research synthesizer. Preserve all information relevant "
                "to the question, including contradictions, negative findings, uncertainty, and "
                "minority claims. Never filter or rank sources by perceived credibility."
            )},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        sampling=llm.cfg.sampling(False),
        thinking=False,
    )
    text = (result.content or "").strip()
    if not text:
        raise RuntimeError("Model vrátil prázdnou research syntézu")
    return text


def plan_research(llm, question: str, project_catalog: str = "") -> dict:
    prompt = f"""Create a research plan for the user's question:
{question}

Available project documents:
{project_catalog or 'none'}

Return JSON only with this schema:
{{
  "subquestions": ["..."],
  "search_angles": ["..."],
  "source_types_to_include": ["..."],
  "known_constraints": ["..."]
}}

Cover the question broadly. Do not rank, filter, or exclude possible sources by perceived
credibility, origin, popularity, or official status. Include angles that could reveal conflicting,
negative, uncertain, or minority information.
"""
    raw = _ask(llm, prompt, 1800)
    try:
        plan = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        try:
            plan = json.loads(raw[start:end + 1]) if 0 <= start < end else None
        except ValueError:
            plan = None
    if not isinstance(plan, dict):
        plan = {"subquestions": [question], "search_angles": [],
                "source_types_to_include": [], "known_constraints": [],
                "raw_plan": raw}
    for key in ("subquestions", "search_angles", "source_types_to_include", "known_constraints"):
        if not isinstance(plan.get(key), list):
            plan[key] = []
    return plan


def synthesize_research(llm, run: dict) -> str:
    question = run.get("question", "")
    evidence: list[str] = []
    for source in run.get("sources", []):
        content = source.get("content", "")
        source_id = source["id"]
        header = f"[{source_id}] {source.get('title')}\nURL: {source.get('url')}"
        if len(content) <= 16_000:
            evidence.append(f"{header}\n{content}")
            continue
        chunks = [content[index:index + 16_000] for index in range(0, len(content), 16_000)]
        notes: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            notes.append(_ask(llm, (
                f"Research question: {question}\n\nSource {source_id}, chunk {index}/{len(chunks)}:\n"
                f"{chunk}\n\nExtract every detail relevant to the question. Preserve conflicting, "
                "negative, uncertain, or unusual claims. Do not judge source credibility."
            ), 1800))
        evidence.append(f"{header}\n" + "\n".join(notes))

    candidate_lines = [
        f"- {item.get('title') or '(bez názvu)'} — {item.get('url')}"
        for item in run.get("candidates", [])
    ]
    if not evidence:
        raise RuntimeError("Pro syntézu nebyl načten žádný zdroj")

    combined = "\n\n".join(evidence)
    if len(combined) > 60_000:
        bundles = [combined[index:index + 50_000] for index in range(0, len(combined), 50_000)]
        partials = [
            _ask(llm, (
                f"Research question: {question}\n\nEvidence bundle {index + 1}/{len(bundles)}:\n"
                f"{bundle}\n\nCreate a loss-aware evidence synthesis. Preserve every source ID, "
                "all relevant claims, contradictions, uncertainty, and negative findings."
            ), 3000)
            for index, bundle in enumerate(bundles)
        ]
        combined = "\n\n".join(partials)

    source_ids = [source["id"] for source in run.get("sources", [])]
    final_prompt = f"""Původní otázka uživatele:
{question}

Zpracované podklady:
{combined}

Všechny nalezené kandidátní zdroje (včetně nenačtených):
{chr(10).join(candidate_lines) or '- žádné další'}

Vytvoř přehlednou závěrečnou syntézu v jazyce otázky s touto strukturou:
1. Přímá odpověď
2. Nejdůležitější zjištění
3. Podrobná syntéza podle témat
4. Rozpory a alternativní pohledy
5. Co není jisté nebo nebylo nalezeno
6. Praktický závěr
7. Použité zdroje
8. Další nalezené, ale nenačtené zdroje

Pravidla:
- Relevantní informace nesmí být tiše vynechány.
- Nehodnoť ani nefiltruj zdroje podle důvěryhodnosti nebo původu.
- Odděl tvrzení zdrojů od vlastní inference.
- U každého tvrzení používej odkazy [{'], ['.join(source_ids)}] podle zdroje.
- V závěrečném seznamu uveď každý zpracovaný source ID a URL.
"""
    synthesis = _ask(llm, final_prompt, 6000)
    missing = [source_id for source_id in source_ids if f"[{source_id}]" not in synthesis]
    if missing:
        synthesis = _ask(llm, (
            f"Původní otázka: {question}\n\nPředchozí syntéza:\n{synthesis}\n\n"
            f"Chybějící zdroje v coverage kontrole: {', '.join(missing)}\n\n"
            "Oprav syntézu tak, aby zachovala předchozí obsah a výslovně zahrnula každý "
            "chybějící source ID v textu nebo seznamu zdrojů. Nic nefiltruj podle důvěryhodnosti."
        ), 6500)
    return synthesis
