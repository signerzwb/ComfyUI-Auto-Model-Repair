import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rapidfuzz import fuzz

NOISE_TOKENS = {
    "fp16", "fp8", "bf16", "q2", "q3", "q4", "q5", "q6", "q8",
    "f16", "f8", "v1", "v2", "v3", "final", "pruned", "ema",
    "sft", "dev", "alpha", "beta", "diffusers"
}


@dataclass
class MatchCandidate:
    filename: str
    path: str
    score: int
    exact: bool


class ModelMatcher:
    def __init__(self, candidate_threshold: int = 78, max_candidates: int = 5):
        self.candidate_threshold = candidate_threshold
        self.max_candidates = max_candidates

    def normalize_filename(self, value: str) -> str:
        if not value:
            return ""
        base = Path(value).name
        base = Path(base).stem.lower()
        base = re.sub(r"[\[\](){}]", " ", base)
        base = re.sub(r"[_\-.]+", " ", base)
        base = re.sub(r"\s+", " ", base).strip()
        return base

    def normalize_for_compare(self, value: str) -> str:
        norm = self.normalize_filename(value)
        parts = [p for p in norm.split(" ") if p and p not in NOISE_TOKENS]
        if not parts:
            return norm.replace(" ", "")
        return " ".join(parts)

    def token_score(self, a: str, b: str) -> int:
        set_a = set(a.split())
        set_b = set(b.split())
        if not set_a or not set_b:
            return 0
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return int((inter / union) * 100)

    def score_pair(self, expected: str, actual: str) -> int:
        e1 = self.normalize_filename(expected)
        a1 = self.normalize_filename(actual)
        e2 = self.normalize_for_compare(expected)
        a2 = self.normalize_for_compare(actual)

        if e1 == a1 or e2 == a2:
            return 100

        ratio_1 = fuzz.ratio(e1, a1)
        ratio_2 = fuzz.ratio(e2, a2)
        partial = fuzz.partial_ratio(e2, a2)
        token_sort = fuzz.token_sort_ratio(e2, a2)
        token_set = fuzz.token_set_ratio(e2, a2)
        token_overlap = self.token_score(e2, a2)

        score = int(
            ratio_1 * 0.10 +
            ratio_2 * 0.20 +
            partial * 0.20 +
            token_sort * 0.15 +
            token_set * 0.20 +
            token_overlap * 0.15
        )
        return min(score, 99)

    def find_best_matches(self, expected: str, candidates: List[Path]) -> List[MatchCandidate]:
        rows: List[MatchCandidate] = []

        for path in candidates:
            filename = path.name
            score = self.score_pair(expected, filename)
            exact = score == 100
            if exact or score >= self.candidate_threshold:
                rows.append(MatchCandidate(
                    filename=filename,
                    path=str(path),
                    score=score,
                    exact=exact,
                ))

        rows.sort(key=lambda x: (-x.score, x.filename.lower()))
        return rows[: self.max_candidates]

    def choose_best(self, expected: str, candidates: List[Path], auto_apply_threshold: int) -> Optional[MatchCandidate]:
        matches = self.find_best_matches(expected, candidates)
        if not matches:
            return None
        if matches[0].score >= auto_apply_threshold:
            return matches[0]
        return None
