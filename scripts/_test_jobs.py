import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.coverage import list_pending_jobs

niches = json.load(open("config/niches.json", encoding="utf-8"))["nichos"]
cities = json.load(open("config/cities.json", encoding="utf-8"))["cidades"]
jobs = list_pending_jobs(cities, niches)
print("jobs_pendentes", len(jobs))
print("primeiros_12:")
for j in jobs[:12]:
    print(
        f"  {j['priority']:5} | {j['city']:22} | {j['area']:28} | {j['niche']}"
    )
sp = [j for j in jobs if j["city"] == "São Paulo"]
rj = [j for j in jobs if j["city"] == "Rio de Janeiro"]
print("SP_jobs", len(sp), "areas", [j["area"] for j in sp[:8]])
print("RJ_jobs", len(rj), "areas", [j["area"] for j in rj[:8]])
bad_sp = [j["area"] for j in sp if j["area"] in ("_cidade", "centro", "zona sul")]
print("SP_areas_ja_varridas_na_fila", bad_sp)
