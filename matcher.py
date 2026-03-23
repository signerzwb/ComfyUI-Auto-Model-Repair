import re
from dataclasses import dataclass
from pathlib import Path
from rapidfuzz import fuzz
NOISE_TOKENS = {"fp16","fp8","bf16","q2","q3","q4","q5","q6","q8","f16","f8","v1","v2","v3","final","pruned","ema","sft","dev","alpha","beta","diffusers","scaled","light","distill","model","weights"}
@dataclass
class MatchCandidate:
    filename: str
    path: str
    score: int
    exact: bool
class ModelMatcher:
    def __init__(self, candidate_threshold=78, max_candidates=5):
        self.candidate_threshold = candidate_threshold; self.max_candidates = max_candidates
    def normalize_filename(self, value):
        if not value: return ""
        base = Path(value).name; base = Path(base).stem.lower(); base = re.sub(r"[\[\](){}]", " ", base); base = re.sub(r"[_\-.]+", " ", base); parts = [p for p in base.split() if p and p not in NOISE_TOKENS]; return " ".join(parts)
    def token_score(self, a, b):
        set_a = set(a.split()); set_b = set(b.split())
        if not set_a or not set_b: return 0
        return int((len(set_a & set_b) / len(set_a | set_b)) * 100)
    def score_pair(self, expected, actual):
        e = self.normalize_filename(expected); a = self.normalize_filename(actual)
        if e == a: return 100
        return min(int(fuzz.ratio(e,a)*0.20 + fuzz.partial_ratio(e,a)*0.20 + fuzz.token_sort_ratio(e,a)*0.20 + fuzz.token_set_ratio(e,a)*0.25 + self.token_score(e,a)*0.15), 99)
    def find_best_matches(self, expected, candidates):
        rows = []
        for path in candidates:
            filename = path.name; score = self.score_pair(expected, filename); exact = score == 100
            if exact or score >= self.candidate_threshold: rows.append(MatchCandidate(filename=filename, path=str(path), score=score, exact=exact))
        rows.sort(key=lambda x: (-x.score, x.filename.lower())); return rows[: self.max_candidates]
