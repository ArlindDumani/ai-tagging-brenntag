# Brenntag Talkwalker Tagging

> **Wichtig – vor Nutzung in Talkwalker beachten:**
> - Der Code schreibt die generierten Tags aktuell in die Spalte **`tags_ai`**, nicht in **`tags_customer`**. Das muss vor einem Upload nach Talkwalker unbedingt angepasst werden (Spalte in `main.py` auf `tags_customer` umstellen).
> - Der **Upload der getaggten Daten nach Talkwalker** ist noch nicht implementiert. Die Ausgabe erfolgt derzeit nur als Excel-Datei im Output-Ordner.

---

KI-gestütztes Tagging von Talkwalker-Exporten (Excel) für Brenntag. Nutzt Google Gemini im Batch-Modus; bei YouTube-URLs mit leerem Content wird das Video-Transkript zum Taggen verwendet.

## Voraussetzungen

- Python 3.10+
- Abhängigkeiten: `pip install -r requirements.txt`

## Konfiguration

- **API-Key:** Wird aus **`.env`** gelesen (nicht im Code). Datei `.env` im Projektordner anlegen mit:
  ```
  GEMINI_API_KEY=dein-api-key
  ```
  Key z. B. unter https://aistudio.google.com/apikey erzeugen. Die Datei `.env` bleibt lokal und wird nicht ins Repo committed.
- **Pfade** (Excel, Log, Output, Instruction-Datei) in `main.py` anpassen.

## Ablauf

1. Excel aus Input-Ordner einlesen
2. Bereits verarbeitete URLs aus Log-CSV laden (kein doppeltes Tagging)
3. Pro Zeile: Content oder (bei YouTube + leerem Content) Titel + Transkript
4. Batches an Gemini senden, Tags in `tags_ai` schreiben
5. Log-CSV und getaggte Excel im Output-Ordner speichern

## GitHub

Repository erstellen, dann lokal (im Projektordner):

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/DEIN-USERNAME/ai-tagging-brenntag.git
git push -u origin main
```

Ersetze **DEIN-USERNAME** durch deinen GitHub-Benutzernamen.

**Hinweis:** API-Key und interne Pfade vor dem ersten Push prüfen (z. B. in `.env` auslagern und `.env` in `.gitignore`).
