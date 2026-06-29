#!/usr/bin/env python3
"""Utilities for deterministic answer-position randomization.

Generated drafts often put the correct option first unless the pipeline
normalizes the answer position after generation. This module reorders only the
five options and the answer index. It does not change the stem, explanation,
evidence, or metadata.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from typing import Any


OPTION_REFERENCE_PATTERN = re.compile(r"(?<!\d)([1-5])\s*번|[①②③④⑤]")


def stable_int(parts: list[Any]) -> int:
    text = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def parse_answer(value: Any) -> int | None:
    if value in {1, 2, 3, 4, 5}:
        return int(value)
    text = str(value or "").strip()
    if text in {"1", "2", "3", "4", "5"}:
        return int(text)
    return None


def normalize_options(options: Any) -> list[str] | None:
    if isinstance(options, list):
        normalized = [str(option) for option in options]
    elif isinstance(options, dict):
        normalized = [str(options.get(str(index)) or options.get(index) or "") for index in range(1, 6)]
    else:
        return None
    if len(normalized) != 5:
        return None
    if any(not option.strip() for option in normalized):
        return None
    return normalized


def deterministic_target_answer(seed_parts: list[Any]) -> int:
    return stable_int(seed_parts) % 5 + 1


def reorder_item_answer_position(
    item: dict[str, Any],
    *,
    target_answer: int | None = None,
    seed_parts: list[Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copy with options reordered so the answer is at target_answer.

    The distractor order is also deterministically shuffled from the seed. If
    the input is not a valid five-option item, the original copy is returned
    with ``changed`` set to false.
    """

    updated = copy.deepcopy(item)
    options = normalize_options(updated.get("options"))
    original_answer = parse_answer(updated.get("answer"))
    if options is None or original_answer is None:
        return updated, {
            "changed": False,
            "reason": "invalid_options_or_answer",
            "original_answer": updated.get("answer"),
            "target_answer": target_answer,
        }
    if OPTION_REFERENCE_PATTERN.search(str(updated.get("explanation") or "")):
        return updated, {
            "changed": False,
            "reason": "explanation_contains_option_number_reference",
            "original_answer": original_answer,
            "target_answer": target_answer,
        }

    seed = seed_parts or [
        updated.get("period"),
        updated.get("subject"),
        updated.get("field"),
        updated.get("area"),
        updated.get("detail"),
        updated.get("stem"),
    ]
    if target_answer is None:
        target_answer = deterministic_target_answer(seed)
    if target_answer not in {1, 2, 3, 4, 5}:
        raise ValueError(f"target_answer must be 1..5: {target_answer}")

    correct_option = options[original_answer - 1]
    distractors = [option for index, option in enumerate(options, start=1) if index != original_answer]
    rng = random.Random(stable_int([*seed, "distractor_order"]))
    rng.shuffle(distractors)

    reordered: list[str] = []
    distractor_iter = iter(distractors)
    for index in range(1, 6):
        if index == target_answer:
            reordered.append(correct_option)
        else:
            reordered.append(next(distractor_iter))

    updated["options"] = reordered
    updated["answer"] = target_answer
    return updated, {
        "changed": reordered != options or original_answer != target_answer,
        "reason": "ok",
        "original_answer": original_answer,
        "target_answer": target_answer,
    }
