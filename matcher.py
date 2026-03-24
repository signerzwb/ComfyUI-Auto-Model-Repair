import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rapidfuzz import fuzz

NOISE_TOKENS = {
    "fp16", "fp8", "bf16", "q2", "q3", "q4", "q5", "q6", "q8",
    "f16", "f8", "v1", "v2", "v3", "final", "pruned", "ema",
    "sft", "dev", "alpha", "beta", "diffusers", "scaled", "light",
    "distill", "model", "weights"
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

        parts = [p for p in base.split() if p and p not in NOISE_TOKENS]
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
        e = self.normalize_filename(expected)
        a = self.normalize_filename(actual)

        if e == a:
            return 100

        score = int(
            fuzz.ratio(e, a) * 0.20
            + fuzz.partial_ratio(e, a) * 0.20
            + fuzz.token_sort_ratio(e, a) * 0.20
            + fuzz.token_set_ratio(e, a) * 0.25
            + self.token_score(e, a) * 0.15
        )
        return min(score, 99)

    def find_best_matches(self, expected: str, candidates: List[str]) -> List[MatchCandidate]:
        rows: List[MatchCandidate] = []

        for candidate in candidates:
            raw_candidate = str(candidate).strip()
            normalized_candidate = raw_candidate.replace("\\", "/")

            score = self.score_pair(expected, Path(normalized_candidate).name)
            exact = score == 100

            if exact or score >= self.candidate_threshold:
                rows.append(
                    MatchCandidate(
                        filename=raw_candidate,
                        path=raw_candidate,
                        score=score,
                        exact=exact,
                    )
                )

        rows.sort(key=lambda x: (-x.score, x.filename.lower()))
        return rows[: self.max_candidates]

    def choose_best(self, expected: str, candidates: List[str], auto_apply_threshold: int) -> Optional[MatchCandidate]:
        matches = self.find_best_matches(expected, candidates)
        if not matches:
            return None
        if matches[0].score >= auto_apply_threshold:
            return matches[0]
        return None
