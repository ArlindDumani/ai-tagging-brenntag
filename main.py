import csv
import os
import pandas as pd
import json
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

load_dotenv()

# --- KONFIGURATION ---
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Fehler: GEMINI_API_KEY fehlt. Bitte in .env setzen (siehe .env.example).")
FILE_PATH_EXCEL = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Input\tw_export.xlsx"
FILE_PATH_TXT = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Tag-Description\generated_tag_instructions_v3.txt"
LOG_FILE = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Logfiles\logs_processed_results-brenntag.csv"
OUTPUT_DIR = r"V:\CURE\Operations\Clients\Brenntag SE\Talkwalker Tagging\Output"
LOG_SEP = ";"
LOG_COLUMNS = ["url", "processing_date", "generated_tags", "instruction_version", "language_model"]
GEMINI_MODEL = "gemini-3-flash-preview"
MAX_TRANSCRIPT_CHARS = 500  # YouTube-Transkript für LLM begrenzen

BATCH_SIZE = 5
MAX_RETRIES = 3       # bei 503/Überlastung: wie oft derselbe Batch wiederholt wird
RETRY_WAIT_SEC = 30   # Wartezeit vor jedem Retry (Sekunden)

ALLOWED_TAGS = ["agriculture","animal nutrition","asphalt","automotive","ceramics","cleaning","coatings & construction","composites","cosmetics","def","electronics","flavors & fragrances","food & nutrition","hi&i","industrial sales & services","lubricants","marine emissions solutions","metal surface treatment","minerals","mining","oil & gas","personal care","pharmaceuticals","polymers","polyurethanes","pulp & paper","refrigeration","rubber","solvents","surface technology","textile","water treatment","wax","acquisition","annual report","application & development center","awards","brand","ceo","certification","charity","customer experience","distribution agreement","donation","educational resources","employer branding","event","financial reporting","formulations","holidays","interview","podcast","press release","products & services","safety","service excellence","sponsoring","sustainability","trends","ukraine","webinar"]

client = genai.Client(api_key=API_KEY)

def load_instructions(path):
    with open(path, 'r', encoding='utf-8') as f: return f.read()


def get_video_id(url):
    """YouTube-Video-ID aus URL (watch?v= oder youtu.be/)."""
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


def fetch_youtube_transcript(url):
    """Holt das Transkript zu einer YouTube-URL. Return: Volltext oder None."""
    video_id = get_video_id(url)
    if not video_id:
        return None
    try:
        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id)  # FetchedTranscript (iterierbar, snippet.text)
        full = " ".join(snippet.text for snippet in fetched)
        return full.strip() or None
    except Exception as e:
        print(f"Fehler YouTube-Transkript ({url}): {e}")
        return None


def get_content_for_tagging(row):
    """
    Inhalt fürs Tagging: Normalerweise content. Bei YouTube-URL + leerem content:
    Titel + Transkript (max 500 Zeichen). Transkript wird NIE in die Excel-Spalte content geschrieben.
    """
    url = row.get("url", "") or ""
    content = row.get("content")
    content_empty = pd.isna(content) or str(content).strip() == ""
    is_youtube = "youtube" in str(url).lower()

    if is_youtube and content_empty:
        title = row.get("title", "")
        title = "" if pd.isna(title) else str(title).strip()
        transcript = fetch_youtube_transcript(url)
        transcript_part = (transcript[:MAX_TRANSCRIPT_CHARS] if transcript else "") or ""
        parts = [p for p in [title, transcript_part] if p]
        return "\n".join(parts) if parts else ""
    return str(content).strip() if not pd.isna(content) else ""


def get_system_instruction(instructions):
    return f"""DU BIST EIN EXPERTE FÜR CONTENT-ANALYSE.
AUFGABE: Du erhältst mehrere Texte mit einer ID. Weise jedem Text die passenden Tags zu.
STRENGE REGELN:
1. Nutze NUR Tags aus dieser Liste: {', '.join(ALLOWED_TAGS)}
2. Maximal 2-3 Tags pro Text.
3. FORMAT: Deine Antwort MUSS ein JSON-Objekt sein. Key = ID, Value = Liste der Tags.
BEISPIEL: {{"0": ["automotive", "lubricants"], "1": ["food & nutrition"]}}

TAG-LOGIK:
{instructions}"""

def process_batch(batch_data, system_instruction):
    # Wir bauen den Batch-Prompt
    batch_content = "\n".join([f"ID {idx}: {text}" for idx, text in batch_data])
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.1,
            ),
            contents=batch_content
        )
        text = getattr(response, "text", None) or ""
        return json.loads(text) if text else None
    except Exception as e:
        print(f"Fehler im Batch: {e}")
        return None

def main():
    instructions = load_instructions(FILE_PATH_TXT)
    sys_inst = get_system_instruction(instructions)
    df = pd.read_excel(FILE_PATH_EXCEL)
    if 'tags_ai' not in df.columns:
        df['tags_ai'] = ''
    else:
        df['tags_ai'] = df['tags_ai'].astype(object)  # damit wir Strings zuweisen können (Excel liefert sonst float64)
    
    instruction_version = Path(FILE_PATH_TXT).name  # z.B. generated_tag_instructions_v3.txt

    # Bereits verarbeitete URLs aus Log-Datei laden → Doppeltes Tagging vermeiden
    def _normalize_url(u):
        if pd.isna(u) or not u:
            return ""
        s = str(u).strip()
        if s.endswith("/"):
            s = s[:-1]
        return s

    processed_urls = set()
    if Path(LOG_FILE).exists():
        try:
            log_df = pd.read_csv(LOG_FILE, sep=LOG_SEP, encoding="utf-8")
            if "url" in log_df.columns:
                processed_urls = set(_normalize_url(u) for u in log_df["url"].dropna() if _normalize_url(u))
        except Exception as e:
            print(f"Hinweis: Log-Datei konnte nicht gelesen werden ({e}), starte mit leerer Liste.")

    if not Path(LOG_FILE).exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, delimiter=LOG_SEP).writerow(LOG_COLUMNS)

    # Nur Beiträge verarbeiten, deren URL noch NICHT in der Log-Datei steht (kein doppeltes Tagging)
    rows_to_process = []
    skipped_log = 0
    for idx, row in df.iterrows():
        url = row.get("url")
        if pd.isna(url) or str(url).strip() == "":
            continue
        url_str = _normalize_url(url)
        if not url_str:
            continue
        if url_str in processed_urls:
            skipped_log += 1
            continue
        content_for_tagging = get_content_for_tagging(row)
        rows_to_process.append((idx, content_for_tagging, url_str))
        processed_urls.add(url_str)  # gleiche URL in derselben Excel in diesem Lauf nur 1x taggen

    if skipped_log:
        print(f"{skipped_log} Zeilen übersprungen (URL bereits in Log-Datei).")
    if not rows_to_process:
        print("Keine neuen Zeilen zu verarbeiten. Keine Datei erstellt.")
        return
    print(f"Verarbeite {len(rows_to_process)} Zeilen in {BATCH_SIZE}er Batches...")

    any_tagged = False
    for i in range(0, len(rows_to_process), BATCH_SIZE):
        batch = rows_to_process[i : i + BATCH_SIZE]
        ki_input = [(str(item[0]), str(item[1])) for item in batch]
        ids_in_batch = [item[0] for item in batch]

        results = None
        for attempt in range(MAX_RETRIES):
            results = process_batch(ki_input, sys_inst)
            if results is not None:
                break
            if attempt < MAX_RETRIES - 1:
                print(f"  → Retry in {RETRY_WAIT_SEC}s für IDs {ids_in_batch} (Versuch {attempt + 2}/{MAX_RETRIES})...")
                time.sleep(RETRY_WAIT_SEC)

        if results:
            any_tagged = True
            processing_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE, "a", newline="", encoding="utf-8") as log_file:
                writer = csv.writer(log_file, delimiter=LOG_SEP)
                for idx_in_batch, content, url in batch:
                    tags_list = results.get(str(idx_in_batch), [])
                    tags_list = [t.lower().strip() for t in tags_list]
                    tags_list = [t for t in tags_list if t in ALLOWED_TAGS]
                    tags_list = sorted(list(set(tags_list)))
                    final_string = ",".join(f"Brenntag/{t}" for t in tags_list)

                    df.at[idx_in_batch, "tags_ai"] = final_string
                    writer.writerow([url, processing_date, final_string, instruction_version, GEMINI_MODEL])
                    print(f"ID {idx_in_batch} getaggt: {final_string}")
        else:
            print(f"  Übersprungen (nach {MAX_RETRIES} Versuchen): IDs {ids_in_batch} – beim nächsten Lauf erneut versuchen.")

        time.sleep(12)  # Sicherheitspause für Free-Tier

    if any_tagged:
        out_path = Path(OUTPUT_DIR) / f"Brenntag_Batch_Result_{datetime.now().strftime('%d%m_%H%M')}.xlsx"
        df.to_excel(out_path, index=False)
        print(f"Fertig! Datei erstellt: {out_path}")
    else:
        print("Nichts getaggt (z. B. alle Batches fehlgeschlagen). Keine Datei erstellt.")

if __name__ == "__main__":
    main()