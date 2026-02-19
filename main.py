"""
Brenntag Talkwalker Tagging – KI-gestütztes Batch-Tagging von Exporten via Google Gemini.
"""

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()


# -----------------------------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------------------------

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("GEMINI_API_KEY fehlt. .env prüfen.")

FILE_PATH_EXCEL = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Input\tw_export.xlsx"
FILE_PATH_TXT = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Tag-Description\generated_tag_instructions_v3.txt"
LOG_FILE = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Logfiles\logs_processed_results-brenntag.csv"
OUTPUT_DIR = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Output"

LOG_SEP = ";"
LOG_COLUMNS = ["url", "processing_date", "generated_tags", "instruction_version", "language_model"]

GEMINI_MODEL = "gemini-3-flash-preview"
BATCH_SIZE = 5
MAX_RETRIES = 3
RETRY_WAIT_SEC = 30
MAX_TRANSCRIPT_CHARS = 500

ALLOWED_TAGS = [
    "agriculture", "animal nutrition", "asphalt", "automotive", "ceramics", "cleaning",
    "coatings & construction", "composites", "cosmetics", "def", "electronics",
    "flavors & fragrances", "food & nutrition", "hi&i", "industrial sales & services",
    "lubricants", "marine emissions solutions", "metal surface treatment", "minerals",
    "mining", "oil & gas", "personal care", "pharmaceuticals", "polymers", "polyurethanes",
    "pulp & paper", "refrigeration", "rubber", "solvents", "surface technology", "textile",
    "water treatment", "wax", "acquisition", "annual report", "application & development center",
    "awards", "brand", "ceo", "certification", "charity", "customer experience",
    "distribution agreement", "donation", "educational resources", "employer branding",
    "event", "financial reporting", "formulations", "holidays", "interview", "podcast",
    "press release", "products & services", "safety", "service excellence", "sponsoring",
    "sustainability", "trends", "ukraine", "webinar",
]

_client = genai.Client(api_key=API_KEY)


# -----------------------------------------------------------------------------
# Hilfsfunktionen (URL, YouTube, Content)
# -----------------------------------------------------------------------------

def _normalize_url(url) -> str:
    if pd.isna(url) or not url:
        return ""
    s = str(url).strip()
    if s.endswith("/"):
        s = s[:-1]
    return s


def _get_video_id(url) -> str | None:
    if pd.isna(url) or not url:
        return None
    s = str(url).strip()
    if s and not s.startswith(("http://", "https://")):
        s = "https://" + s
    parsed = urlparse(s)
    if "youtu.be" in parsed.netloc:
        return (parsed.path or "").strip("/") or None
    if "youtube.com" in parsed.netloc:
        q = parse_qs(parsed.query)
        return q.get("v", [None])[0]
    return None


def _fetch_youtube_transcript(url) -> str | None:
    video_id = _get_video_id(url)
    if not video_id:
        return None
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        return " ".join(s.text for s in fetched).strip() or None
    except Exception as e:
        print(f"YouTube-Transkript fehlgeschlagen ({url}): {e}")
        return None


def get_content_for_tagging(row: pd.Series) -> str:
    """Ermittelt den für das Tagging zu verwendenden Inhalt (Content oder YouTube Titel+Transkript)."""
    url = row.get("url", "") or ""
    content = row.get("content")
    content_empty = pd.isna(content) or str(content).strip() == ""
    is_youtube = "youtube" in str(url).lower()

    if is_youtube and content_empty:
        title = "" if pd.isna(row.get("title")) else str(row.get("title", "")).strip()
        transcript = _fetch_youtube_transcript(url)
        transcript_part = (transcript[:MAX_TRANSCRIPT_CHARS] if transcript else "") or ""
        parts = [p for p in [title, transcript_part] if p]
        return "\n".join(parts) if parts else ""
    return str(content).strip() if not pd.isna(content) else ""


# -----------------------------------------------------------------------------
# Tagging (LLM)
# -----------------------------------------------------------------------------

def _load_instructions(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _build_system_instruction(instructions: str) -> str:
    return f"""DU BIST EIN EXPERTE FÜR CONTENT-ANALYSE.
AUFGABE: Du erhältst mehrere Texte mit einer ID. Weise jedem Text die passenden Tags zu.
STRENGE REGELN:
1. Nutze NUR Tags aus dieser Liste: {", ".join(ALLOWED_TAGS)}
2. Maximal 2-3 Tags pro Text.
3. FORMAT: Deine Antwort MUSS ein JSON-Objekt sein. Key = ID, Value = Liste der Tags.
BEISPIEL: {{"0": ["automotive", "lubricants"], "1": ["food & nutrition"]}}

TAG-LOGIK:
{instructions}"""


def _process_batch(batch_data: list[tuple], system_instruction: str) -> dict | None:
    batch_content = "\n".join(f"ID {idx}: {text}" for idx, text in batch_data)
    try:
        response = _client.models.generate_content(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.1,
            ),
            contents=batch_content,
        )
        text = getattr(response, "text", None) or ""
        return json.loads(text) if text else None
    except Exception as e:
        print(f"Batch-Fehler: {e}")
        return None


def _validate_and_format_tags(tags_list: list) -> str:
    tags_list = [t.lower().strip() for t in tags_list if isinstance(t, str)]
    tags_list = [t for t in tags_list if t in ALLOWED_TAGS]
    tags_list = sorted(set(tags_list))
    return ",".join(f"Brenntag/{t}" for t in tags_list)


# -----------------------------------------------------------------------------
# Log & Ausgabe
# -----------------------------------------------------------------------------

def _load_processed_urls(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    try:
        log_df = pd.read_csv(log_path, sep=LOG_SEP, encoding="utf-8")
        if "url" not in log_df.columns:
            return set()
        return set(_normalize_url(u) for u in log_df["url"].dropna() if _normalize_url(u))
    except Exception as e:
        print(f"Log-Datei nicht lesbar ({e}), starte mit leerer URL-Liste.")
        return set()


def _ensure_log_file_exists(log_path: Path) -> None:
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, delimiter=LOG_SEP).writerow(LOG_COLUMNS)


def _append_log_row(log_path: Path, url: str, generated_tags: str, instruction_version: str) -> None:
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=LOG_SEP).writerow([
            url,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            generated_tags,
            instruction_version,
            GEMINI_MODEL,
        ])


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    instructions = _load_instructions(FILE_PATH_TXT)
    system_instruction = _build_system_instruction(instructions)

    df = pd.read_excel(FILE_PATH_EXCEL)
    if "tags_ai" not in df.columns:
        df["tags_ai"] = ""
    else:
        df["tags_ai"] = df["tags_ai"].astype(object)

    instruction_version = Path(FILE_PATH_TXT).name
    log_path = Path(LOG_FILE)
    _ensure_log_file_exists(log_path)

    processed_urls = _load_processed_urls(log_path)

    rows_to_process: list[tuple] = []
    skipped = 0
    for idx, row in df.iterrows():
        url = row.get("url")
        if pd.isna(url) or str(url).strip() == "":
            continue
        url_str = _normalize_url(url)
        if not url_str or url_str in processed_urls:
            if url_str and url_str in processed_urls:
                skipped += 1
            continue
        content = get_content_for_tagging(row)
        rows_to_process.append((idx, content, url_str))
        processed_urls.add(url_str)

    if skipped:
        print(f"{skipped} Zeilen übersprungen (bereits in Log).")
    if not rows_to_process:
        print("Keine neuen Zeilen. Keine Datei erstellt.")
        return

    print(f"Verarbeite {len(rows_to_process)} Zeilen in Batches à {BATCH_SIZE} …")

    any_tagged = False
    for i in range(0, len(rows_to_process), BATCH_SIZE):
        batch = rows_to_process[i : i + BATCH_SIZE]
        ki_input = [(item[0], str(item[1])) for item in batch]
        ids = [item[0] for item in batch]

        results = None
        for attempt in range(MAX_RETRIES):
            results = _process_batch(ki_input, system_instruction)
            if results is not None:
                break
            if attempt < MAX_RETRIES - 1:
                print(f"  Retry in {RETRY_WAIT_SEC}s (IDs {ids}) …")
                time.sleep(RETRY_WAIT_SEC)

        if results:
            any_tagged = True
            for idx_in_batch, _content, url in batch:
                raw_tags = results.get(str(idx_in_batch), [])
                final_string = _validate_and_format_tags(raw_tags)
                df.at[idx_in_batch, "tags_ai"] = final_string
                _append_log_row(log_path, url, final_string, instruction_version)
                print(f"  ID {idx_in_batch}: {final_string}")
        else:
            print(f"  Übersprungen (nach {MAX_RETRIES} Versuchen): IDs {ids}")

        time.sleep(12)

    if any_tagged:
        out_path = Path(OUTPUT_DIR) / f"Brenntag_Batch_Result_{datetime.now().strftime('%d%m_%H%M')}.xlsx"
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        df.to_excel(out_path, index=False)
        print(f"Fertig: {out_path}")
    else:
        print("Nichts getaggt. Keine Datei erstellt.")


if __name__ == "__main__":
    main()
