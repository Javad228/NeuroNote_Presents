from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import AppConfig


logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_EXPLANATION_PRIORITY_GUIDANCE = (
    "Across all slides, treat explanation_text as the primary evidence signal; "
    "use other fields only for grounding and disambiguation."
)


class OpenAIQAError(RuntimeError):
    pass


class OpenAIQATimeoutError(OpenAIQAError):
    pass


class OpenAIJSONError(OpenAIQAError):
    pass


def _truncate(text: str, limit: int = 400) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _extract_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw

    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                if item.get("type") in {"output_text", "text"} and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        raw = "\n".join(parts)

    if not isinstance(raw, str):
        raise OpenAIJSONError("LLM response did not contain text.")

    text = raw.strip()
    if not text:
        raise OpenAIJSONError("LLM response was empty.")

    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()

    try:
        payload = json.loads(text)
    except Exception as exc:
        raise OpenAIJSONError(f"Failed to parse JSON response: {_truncate(text)}") from exc

    if not isinstance(payload, dict):
        raise OpenAIJSONError("LLM JSON response was not an object.")
    return payload


class OpenAIQAService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.api_key = config.openai_api_key.strip()
        self.base_url = (config.openai_base_url or _DEFAULT_OPENAI_BASE_URL).rstrip("/")
        timeout = httpx.Timeout(config.qa_openai_timeout_seconds, connect=10.0)
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "OpenAIQAService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    def _ensure_configured(self) -> None:
        if not self.api_key:
            raise OpenAIQAError("OpenAI API key is not configured for QA.")

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_configured()
        url = f"{self.base_url}{path}"
        try:
            response = await self.client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise OpenAIQATimeoutError("OpenAI request timed out.") from exc
        except httpx.HTTPError as exc:
            raise OpenAIQAError(f"OpenAI request failed: {exc}") from exc

        if response.status_code >= 400:
            text = _truncate(response.text)
            raise OpenAIQAError(
                f"OpenAI API returned {response.status_code} for {path}: {text}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise OpenAIQAError(f"OpenAI API returned invalid JSON for {path}.") from exc

        if not isinstance(data, dict):
            raise OpenAIQAError(f"OpenAI API returned a non-object payload for {path}.")
        return data

    @staticmethod
    def _normalize_message_content(message: Any) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts: list[str] = []
            for item in message:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            return "\n".join(parts)
        return ""

    async def _chat_json_object(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        strict_json_hint: bool = True,
    ) -> tuple[dict[str, Any], Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
        }
        if strict_json_hint:
            body["response_format"] = {"type": "json_object"}

        try:
            payload = await self._post_json("/chat/completions", body)
        except OpenAIQAError:
            if strict_json_hint:
                # Some deployments may reject response_format on certain models.
                payload = await self._post_json("/chat/completions", {k: v for k, v in body.items() if k != "response_format"})
            else:
                raise

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIJSONError("LLM response did not contain choices.")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise OpenAIJSONError("LLM response choice is invalid.")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise OpenAIJSONError("LLM response message is invalid.")

        raw_content = message.get("content")
        normalized = self._normalize_message_content(raw_content)
        parsed = _extract_json_object(normalized)
        return parsed, parsed

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        batch_size = 128
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            payload = await self._post_json(
                "/embeddings",
                {
                    "model": self.config.qa_embed_model,
                    "input": batch,
                },
            )
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise OpenAIQAError("Unexpected embeddings payload shape.")

            ordered: list[list[float] | None] = [None] * len(batch)
            for item in data:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                embedding = item.get("embedding")
                if not isinstance(index, int) or index < 0 or index >= len(batch):
                    continue
                if not isinstance(embedding, list):
                    continue
                try:
                    ordered[index] = [float(v) for v in embedding]
                except Exception as exc:
                    raise OpenAIQAError("Invalid embedding vector returned by OpenAI.") from exc

            if any(v is None for v in ordered):
                raise OpenAIQAError("OpenAI embeddings response missing vectors.")

            vectors.extend([v for v in ordered if v is not None])

        return vectors

    async def select_relevant_units(
        self,
        *,
        question: str,
        candidate_cards: list[dict[str, Any]],
        max_selected_units: int,
    ) -> tuple[list[str], Any]:
        system_prompt = (
            "Select the most relevant candidate units for answering the user question.\n"
            "Return ONLY a JSON object with key selected_unit_ids.\n"
            "Constraints:\n"
            f"- {_EXPLANATION_PRIORITY_GUIDANCE}\n"
            "- selected_unit_ids must contain only unit_id values from the provided candidates.\n"
            f"- Return at most {max_selected_units} unit IDs.\n"
            "- Keep IDs ordered from most relevant to least relevant.\n"
            "- Do not include commentary or extra keys."
        )
        user_payload = {
            "question": question,
            "max_selected_units": max_selected_units,
            "candidate_units": candidate_cards,
            "output_schema": {"selected_unit_ids": ["unit_id_1"]},
        }

        parsed, raw = await self._chat_json_object(
            model=self.config.qa_select_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )

        raw_ids = parsed.get("selected_unit_ids")
        if not isinstance(raw_ids, list):
            return [], raw
        selected: list[str] = []
        seen: set[str] = set()
        for item in raw_ids:
            if not isinstance(item, str):
                continue
            unit_id = item.strip()
            if not unit_id or unit_id in seen:
                continue
            seen.add(unit_id)
            selected.append(unit_id)
        return selected, raw

    async def rewrite_queries(
        self,
        *,
        question: str,
        max_rewrites: int,
    ) -> tuple[list[str], Any]:
        limit = max(0, int(max_rewrites))
        if limit <= 0:
            return [], {"rewrites": []}
        system_prompt = (
            "Generate alternate phrasings of a user question for retrieval.\n"
            "Return ONLY a JSON object with key rewrites.\n"
            "Constraints:\n"
            f"- Return at most {limit} rewrites.\n"
            "- Keep each rewrite semantically equivalent to the original question.\n"
            "- Vary wording and terminology where useful.\n"
            "- Do not answer the question.\n"
            "- Do not include commentary or extra keys."
        )
        user_payload = {
            "question": question,
            "max_rewrites": limit,
            "output_schema": {"rewrites": ["rewrite query"]},
        }
        parsed, raw = await self._chat_json_object(
            model=self.config.qa_rewrite_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )
        raw_rewrites = parsed.get("rewrites")
        if not isinstance(raw_rewrites, list):
            return [], raw
        out: list[str] = []
        seen: set[str] = {question.strip().lower()}
        for item in raw_rewrites:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(value)
            if len(out) >= limit:
                break
        return out, raw

    async def rerank_candidate_units(
        self,
        *,
        question: str,
        candidate_cards: list[dict[str, Any]],
        top_n: int,
    ) -> tuple[list[str], Any]:
        limit = max(1, int(top_n))
        system_prompt = (
            "Rerank candidate units by relevance to the user question.\n"
            "Return ONLY a JSON object with key ranked_unit_ids.\n"
            "Constraints:\n"
            f"- {_EXPLANATION_PRIORITY_GUIDANCE}\n"
            "- ranked_unit_ids must be a subset of provided candidate unit_id values.\n"
            f"- Return at most {limit} unit IDs, best first.\n"
            "- Do not include commentary or extra keys."
        )
        user_payload = {
            "question": question,
            "top_n": limit,
            "candidate_units": candidate_cards,
            "output_schema": {"ranked_unit_ids": ["unit_id_1"]},
        }
        parsed, raw = await self._chat_json_object(
            model=self.config.qa_rerank_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )
        raw_ids = parsed.get("ranked_unit_ids")
        if not isinstance(raw_ids, list):
            return [], raw
        allowed = {
            str(item.get("unit_id"))
            for item in candidate_cards
            if isinstance(item, dict) and isinstance(item.get("unit_id"), str)
        }
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_ids:
            if not isinstance(item, str):
                continue
            unit_id = item.strip()
            if not unit_id or unit_id in seen or unit_id not in allowed:
                continue
            seen.add(unit_id)
            out.append(unit_id)
            if len(out) >= limit:
                break
        return out, raw

    async def assess_answerability(
        self,
        *,
        question: str,
        context_units: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], Any]:
        allowed_reason_codes = {
            "sufficient_evidence",
            "insufficient_retrieval_evidence",
            "question_out_of_scope",
            "ambiguous_question",
        }
        system_prompt = (
            "Assess whether the provided context is sufficient to answer the question.\n"
            "Return ONLY a JSON object with keys answerable and reason_code.\n"
            f"- {_EXPLANATION_PRIORITY_GUIDANCE}\n"
            "reason_code must be one of: sufficient_evidence, insufficient_retrieval_evidence, question_out_of_scope, ambiguous_question.\n"
            "Do not answer the question and do not include extra keys."
        )
        user_payload = {
            "question": question,
            "context_units": context_units,
            "output_schema": {"answerable": True, "reason_code": "sufficient_evidence"},
        }
        parsed, raw = await self._chat_json_object(
            model=self.config.qa_gate_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )
        raw_answerable = parsed.get("answerable")
        answerable = raw_answerable if isinstance(raw_answerable, bool) else False
        reason_code = parsed.get("reason_code")
        if not isinstance(reason_code, str) or reason_code not in allowed_reason_codes:
            reason_code = "sufficient_evidence" if answerable else "insufficient_retrieval_evidence"
        return {"answerable": answerable, "reason_code": reason_code}, raw

    async def verify_answer_lines(
        self,
        *,
        question: str,
        answer_lines: list[dict[str, Any]],
        context_units: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], Any]:
        if not answer_lines:
            return [], {"line_verdicts": []}
        system_prompt = (
            "You verify whether each answer line is supported by the cited context units.\n"
            "Return ONLY a JSON object with key line_verdicts.\n"
            f"- {_EXPLANATION_PRIORITY_GUIDANCE}\n"
            "For each line, output line_index, verdict, corrected_text (or null), and reason_code.\n"
            "verdict must be one of: supported, partially_supported, unsupported.\n"
            "Do not include commentary or extra keys."
        )
        user_payload = {
            "question": question,
            "answer_lines": answer_lines,
            "context_units": context_units,
            "output_schema": {
                "line_verdicts": [
                    {
                        "line_index": 0,
                        "verdict": "supported",
                        "corrected_text": None,
                        "reason_code": "supported",
                    }
                ]
            },
        }
        parsed, raw = await self._chat_json_object(
            model=self.config.qa_verify_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )
        raw_verdicts = parsed.get("line_verdicts")
        if not isinstance(raw_verdicts, list):
            return [], raw
        valid_verdicts = {"supported", "partially_supported", "unsupported"}
        out: list[dict[str, Any]] = []
        seen_indices: set[int] = set()
        max_idx = len(answer_lines) - 1
        for item in raw_verdicts:
            if not isinstance(item, dict):
                continue
            try:
                line_index = int(item.get("line_index"))
            except Exception:
                continue
            if line_index < 0 or line_index > max_idx or line_index in seen_indices:
                continue
            verdict = item.get("verdict")
            if not isinstance(verdict, str) or verdict not in valid_verdicts:
                continue
            corrected_text = item.get("corrected_text")
            if corrected_text is not None and not isinstance(corrected_text, str):
                corrected_text = None
            reason_code = item.get("reason_code")
            if not isinstance(reason_code, str) or not reason_code.strip():
                reason_code = verdict
            out.append(
                {
                    "line_index": line_index,
                    "verdict": verdict,
                    "corrected_text": corrected_text.strip() if isinstance(corrected_text, str) else None,
                    "reason_code": reason_code.strip(),
                }
            )
            seen_indices.add(line_index)
        return out, raw

    async def answer_with_highlights(
        self,
        *,
        question: str,
        context_units: list[dict[str, Any]],
        region_catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            "You answer questions using only the provided slide explanation units and region catalog.\n"
            "Return ONLY JSON matching this schema:\n"
            '{"answer_lines":[{"text":"...","highlights":[{"slide_id":"page_001","region_id":"r:1"}],"unit_ids":["chunk_001:page_001:s1"]}]}\n'
            "Constraints:\n"
            f"- {_EXPLANATION_PRIORITY_GUIDANCE}\n"
            "- answer_lines must be a list.\n"
            "- Each line text must be concise and factual.\n"
            "- highlights must use exact slide_id and region_id values from the region catalog.\n"
            "- unit_ids must use exact unit_id values from the context units.\n"
            "- Keep highlights and unit_ids relevant to the same claim.\n"
            "- Do not include markdown, commentary, or extra keys."
        )

        user_payload = {
            "question": question,
            "context_units": context_units,
            "region_catalog": region_catalog,
            "output_schema": {
                "answer_lines": [
                    {
                        "text": "answer line",
                        "highlights": [{"slide_id": "page_001", "region_id": "r:1"}],
                        "unit_ids": ["chunk_001:page_001:s1"],
                    }
                ]
            },
        }

        parsed, _ = await self._chat_json_object(
            model=self.config.qa_answer_model,
            system_prompt=system_prompt,
            user_payload=user_payload,
            strict_json_hint=True,
        )
        return parsed
