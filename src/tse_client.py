"""Client and parsers for official Brazilian TSE election results, catalogs, and polling places."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import unicodedata
import urllib.request
import zipfile
from typing import Any

USER_AGENT = "Mozilla/5.0 (compatible; ApifyBrazilElections/1.0; +https://apify.com/brasil-api-lcm)"
TSE_BASE = "https://resultados.tse.jus.br/oficial"
LOCAIS_2026_ZIP_URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/"
    "eleitorado_locais_votacao/eleitorado_local_votacao_2026.zip"
)

_CACHE_TTL_SECS = 1800
_CACHE: dict[str, tuple[float, Any]] = {}


def fold_text(value: str | None) -> str:
    """Lowercase and strip diacritics so ASCII queries match Portuguese names."""
    if not value:
        return ""
    norm = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()


def parse_ptbr_float(value: Any) -> float | None:
    """Convert PT-BR formatted number string (e.g. '48,21') into float."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def build_result_url(
    cycle: str,
    election_id: int,
    scope: str,
    office_code: int,
    municipality_code: str = "",
) -> str:
    """Build official TSE consolidated static JSON endpoint URL."""
    abr = (scope or "br").strip().lower()
    mun = municipality_code.strip().zfill(5) if municipality_code.strip() else ""
    prefix = f"{abr}{mun}" if mun else abr
    return f"{TSE_BASE}/{cycle}/{int(election_id)}/dados/{abr}/{prefix}-c{int(office_code):04d}-e{int(election_id):06d}-u.json"


def _http_get_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _cached_bytes(key: str, url: str, timeout: int) -> bytes:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL_SECS:
        return hit[1]
    raw = _http_get_bytes(url, timeout)
    _CACHE[key] = (now, raw)
    return raw


def parse_election_catalog(data: dict[str, Any], query: str = "", max_results: int = 100) -> list[dict[str, Any]]:
    """Flatten TSE ele-c.json into structured election & pleito catalog rows."""
    q = fold_text(query.strip())
    rows: list[dict[str, Any]] = []
    for pleito in data.get("pl") or []:
        cd_pleito = pleito.get("cd")
        cycle = pleito.get("c") or ""
        dt = pleito.get("dt") or ""
        for elec in pleito.get("e") or []:
            ele_id = int(elec.get("cd")) if str(elec.get("cd", "")).isdigit() else elec.get("cd")
            name = elec.get("nm") or ""
            round_num = elec.get("t") or ""
            scopes = [a.get("cd") for a in (elec.get("abr") or []) if a.get("cd")]
            offices = []
            for a in elec.get("abr") or []:
                for cp in a.get("cp") or []:
                    label = f"{cp.get('cd')}:{cp.get('ds')}"
                    if label not in offices:
                        offices.append(label)
            blob = fold_text(f"{cycle} {ele_id} {name} {' '.join(scopes)} {' '.join(offices)}")
            if q and q not in blob:
                continue
            rows.append(
                {
                    "recordType": "election_catalog",
                    "cycle": cycle,
                    "pleitoCode": cd_pleito,
                    "electionId": ele_id,
                    "scope": ",".join(scopes[:6]) + ("..." if len(scopes) > 6 else ""),
                    "office": "; ".join(offices[:6]),
                    "candidateOrEntity": name,
                    "partyOrCode": f"Round {round_num}",
                    "votesOrCount": len(scopes),
                    "votePct": None,
                    "status": dt,
                    "sourceUrl": f"{TSE_BASE}/comum/config/ele-c.json",
                }
            )
            if len(rows) >= max_results:
                return rows
    return rows


def _extract_cands_from_cargo(carg_obj: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return list of (coalition_name, party_sg, cand_dict) handling both agr/par and flat layouts."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for agr in carg_obj.get("agr") or []:
        coalition = agr.get("nm") or ""
        for par in agr.get("par") or []:
            sg = par.get("sg") or ""
            for cand in par.get("cand") or []:
                out.append((coalition, sg, cand))
    for cand in carg_obj.get("cand") or []:
        out.append((cand.get("com") or "", cand.get("sgp") or "", cand))
    return out


def parse_consolidated_results(
    data: dict[str, Any],
    cycle: str,
    source_url: str,
    query: str = "",
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Parse TSE -u.json consolidated election output into flat candidate vote records."""
    q = fold_text(query.strip())
    ele_id_raw = str(data.get("ele") or "")
    ele_id = int(ele_id_raw) if ele_id_raw.isdigit() else ele_id_raw
    scope = str(data.get("cdabr") or "").upper()
    updated_at = f"{data.get('dt', '')} {data.get('ht', '')}".strip()

    s_info = data.get("s") or {}
    v_info = data.get("v") or {}
    rows: list[dict[str, Any]] = []

    for carg in data.get("carg") or []:
        office = carg.get("nmn") or carg.get("nmm") or carg.get("cd") or ""
        for coalition, party_sg, cand in _extract_cands_from_cargo(carg):
            cand_name = cand.get("nmu") or cand.get("nm") or ""
            full_name = cand.get("nm") or cand_name
            votes = int(cand.get("vap")) if str(cand.get("vap", "")).isdigit() else None
            pct = parse_ptbr_float(cand.get("pvapn") or cand.get("pvap"))
            elected = str(cand.get("e") or "").lower() == "s"
            status = cand.get("st") or ("Eleito" if elected else "Não eleito")
            vices = [f"{v.get('nmu') or v.get('nm')} ({v.get('sgp')})" for v in (cand.get("vs") or [])]

            blob = fold_text(f"{cand_name} {full_name} {party_sg} {coalition} {office} {status}")
            if q and q not in blob:
                continue

            rows.append(
                {
                    "recordType": "result",
                    "cycle": cycle,
                    "electionId": ele_id,
                    "scope": scope,
                    "office": office,
                    "candidateNumber": cand.get("n") or "",
                    "sqcand": cand.get("sqcand") or "",
                    "candidateOrEntity": cand_name,
                    "fullName": full_name,
                    "partyOrCode": party_sg,
                    "coalition": coalition,
                    "viceCandidates": vices,
                    "votesOrCount": votes,
                    "votePct": round(pct, 4) if pct is not None else None,
                    "elected": elected,
                    "status": status,
                    "sectionsTotalizedPct": parse_ptbr_float(s_info.get("pst")),
                    "turnoutTotal": int(v_info.get("tv")) if str(v_info.get("tv", "")).isdigit() else None,
                    "updatedAt": updated_at,
                    "sourceUrl": source_url,
                }
            )
            if len(rows) >= max_results:
                return rows
    rows.sort(key=lambda r: r.get("votesOrCount") or 0, reverse=True)
    return rows


def parse_municipalities_config(
    data: dict[str, Any],
    cycle: str,
    election_id: int,
    source_url: str,
    scope: str = "",
    query: str = "",
    max_results: int = 500,
) -> list[dict[str, Any]]:
    """Parse TSE mun-e*-cm.json into municipality + IBGE code + electoral zone rows."""
    uf_filter = "" if not scope or scope.lower() == "br" else scope.strip().lower()
    q = fold_text(query.strip())
    rows: list[dict[str, Any]] = []

    for abr in data.get("abr") or []:
        uf = str(abr.get("cd") or "").lower()
        if uf_filter and uf != uf_filter:
            continue
        for mu in abr.get("mu") or []:
            nm = mu.get("nm") or ""
            cd_tse = mu.get("cd") or ""
            cd_ibge = mu.get("cdi") or ""
            zones = mu.get("z") or []
            if q and q not in fold_text(f"{nm} {cd_tse} {cd_ibge} {uf}"):
                continue
            rows.append(
                {
                    "recordType": "municipality",
                    "cycle": cycle,
                    "electionId": election_id,
                    "scope": uf.upper(),
                    "office": "Municipality",
                    "candidateOrEntity": nm,
                    "partyOrCode": cd_tse,
                    "ibgeCode": cd_ibge,
                    "zones": zones,
                    "votesOrCount": len(zones),
                    "votePct": None,
                    "status": "Active",
                    "sourceUrl": source_url,
                }
            )
            if len(rows) >= max_results:
                return rows
    return rows


def parse_polling_stations_stream(
    text_stream: io.TextIOBase,
    uf_filter: str = "",
    query: str = "",
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Stream-parse deduplicated polling stations with lat/lng from TSE ZIP CSV."""
    reader = csv.DictReader(text_stream, delimiter=";")
    uf_target = "" if not uf_filter or uf_filter.lower() == "br" else uf_filter.strip().upper()
    q = fold_text(query.strip())
    seen_locals: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for row in reader:
        uf = (row.get("SG_UF") or "").strip().upper()
        if uf_target and uf != uf_target:
            continue
        cd_mun = (row.get("CD_MUNICIPIO") or "").strip()
        nm_mun = (row.get("NM_MUNICIPIO") or "").strip()
        zona = (row.get("NR_ZONA") or "").strip()
        local_id = (row.get("NR_LOCAL_VOTACAO") or "").strip()
        nm_local = (row.get("NM_LOCAL_VOTACAO") or "").strip()
        addr = (row.get("DS_ENDERECO") or "").strip()
        bairro = (row.get("NM_BAIRRO") or "").strip()

        key = (uf, cd_mun, zona, local_id)
        if key in seen_locals:
            seen_locals[key]["sectionsCount"] += 1
            seen_locals[key]["votesOrCount"] += 1
            continue

        blob = fold_text(f"{uf} {nm_mun} {nm_local} {addr} {bairro} {cd_mun}")
        if q and q not in blob:
            continue

        lat = parse_ptbr_float(row.get("NR_LATITUDE"))
        lon = parse_ptbr_float(row.get("NR_LONGITUDE"))
        if lat == -1:
            lat = None
        if lon == -1:
            lon = None

        seen_locals[key] = {
            "recordType": "polling_station",
            "cycle": "ele2026",
            "electionId": 2026,
            "scope": f"{nm_mun}/{uf}",
            "office": f"Zone {zona}",
            "candidateOrEntity": nm_local,
            "partyOrCode": f"Local {local_id}",
            "municipalityCode": cd_mun,
            "municipality": nm_mun,
            "neighborhood": bairro,
            "address": addr,
            "latitude": lat,
            "longitude": lon,
            "sectionsCount": 1,
            "votesOrCount": 1,
            "votePct": None,
            "status": "Geocoded" if (lat is not None and lon is not None) else "No Coords",
            "sourceUrl": LOCAIS_2026_ZIP_URL,
        }
        if len(seen_locals) >= max_results:
            break

    return list(seen_locals.values())


async def run_tse_query(actor_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Run requested Brazil TSE elections query."""
    mode = str(actor_input.get("mode") or "results").strip()
    cycle = str(actor_input.get("cycle") or "ele2026").strip()
    election_id = int(actor_input.get("electionId") or 6258)
    scope = str(actor_input.get("scope") or "br").strip().lower()
    office_code = int(actor_input.get("officeCode") or 1)
    municipality_code = str(actor_input.get("municipalityCode") or "").strip()
    query = str(actor_input.get("query") or "").strip()
    max_results = max(1, min(5000, int(actor_input.get("maxResults") or 100)))
    timeout = max(5, min(120, int(actor_input.get("timeoutSecs") or 30)))

    loop = asyncio.get_running_loop()

    if mode == "elections_catalog":
        url = f"{TSE_BASE}/comum/config/ele-c.json"
        raw = await loop.run_in_executor(None, _cached_bytes, "ele_c", url, timeout)
        return parse_election_catalog(json.loads(raw.decode("utf-8")), query=query, max_results=max_results)

    if mode == "results":
        url = build_result_url(cycle, election_id, scope, office_code, municipality_code)
        raw = await loop.run_in_executor(None, _cached_bytes, f"res:{url}", url, timeout)
        return parse_consolidated_results(
            json.loads(raw.decode("utf-8")),
            cycle=cycle,
            source_url=url,
            query=query,
            max_results=max_results,
        )

    if mode == "municipalities":
        url = f"{TSE_BASE}/{cycle}/{election_id:04d}/config/mun-e{election_id:06d}-cm.json"
        raw = await loop.run_in_executor(None, _cached_bytes, f"mun:{url}", url, timeout)
        return parse_municipalities_config(
            json.loads(raw.decode("utf-8")),
            cycle=cycle,
            election_id=election_id,
            source_url=url,
            scope=scope,
            query=query,
            max_results=max_results,
        )

    if mode == "polling_stations":
        zbytes = await loop.run_in_executor(None, _cached_bytes, "zip:locais_2026", LOCAIS_2026_ZIP_URL, max(timeout, 60))

        def _scan() -> list[dict[str, Any]]:
            uf_upper = "" if scope == "br" else scope.upper()
            rows: list[dict[str, Any]] = []
            with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if uf_upper:
                    uf_matches = [n for n in csv_names if f"_{uf_upper}." in n.upper()]
                    if uf_matches:
                        csv_names = uf_matches
                for name in csv_names:
                    with zf.open(name) as bstream:
                        tstream = io.TextIOWrapper(bstream, encoding="latin-1", errors="ignore")
                        rows.extend(
                            parse_polling_stations_stream(
                                tstream,
                                uf_filter=uf_upper,
                                query=query,
                                max_results=max_results - len(rows),
                            )
                        )
                    if len(rows) >= max_results:
                        break
            return rows

        return await loop.run_in_executor(None, _scan)

    raise ValueError(f"Unsupported mode: {mode}")
