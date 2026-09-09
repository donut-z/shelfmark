# Alternatieve Strategieën voor Downloadlinks Verzamelen

> **Status:** Geïmplementeerd (Optie C: Configureerbare Strategie met `title_author` als standaard)  
> **Auteur:** Antigravity & User  
> **Datum:** 8 september 2026 (bijgewerkt 9 september 2026)  
> **Betreft:** Optimalisatie van `ReleaseSearchPlan` en `direct_download.py` in Shelfmark  

---

## 1. Probleemanalyse: De Huidige ISBN-First Strategie

In de huidige versie van Shelfmark (`shelfmark/release_sources/direct_download.py`) worden downloadlinks voor een gekozen boek als volgt opgehaald:

```
Gebruiker kiest boek in UI
       │
       ▼
1. ISBN-zoekopdracht naar AA/Mirrors (bijv. "9783423026185" + filters: en,nl)
       ├── Resultaten gevonden? ──► Toon releases in UI
       └── Geen resultaten? (0 hits)
               │
               ▼
2. Fallback: Zoekopdracht op Titel + Auteur (bijv. "Dune Frank Herbert")
               │
               ▼
       Toon releases in UI
```

### Waarom dit in de praktijk suboptimaal werkt:
1. **Ondoorzichtige metadata uit Hardcover:**  
   Wanneer een gebruiker zoekt naar *"Dune"*, toont Hardcover een lijst van covers en titels. Een gebruiker ziet **niet** dat het geselecteerde resultaat toevallig gekoppeld is aan een specifieke regionale pocketuitgave (zoals de Duitse DTV-pocket met ISBN `9783423026185`).
2. **Onnodige wachttijd (Dubbele Roundtrip):**  
   Omdat het ISBN-nummer van die specifieke pocketuitgave op Anna's Archive / LibGen niet bestaat met een Engels/Nederlands EPUB-bestand, levert de eerste zoekopdracht **altijd 0 resultaten** op.  
   - Dit kost een complete HTTP-roundtrip (of erger: een volledige headless Chrome-solve als er een challenge optreedt).
   - Pas na deze mislukte poging start de fallback op Titel + Auteur, die vervolgens direct **50 resultaten** oplevert.
3. **E-books hebben zelden het ISBN van een specifieke paperback:**  
   In schaduwbibliotheken (Anna's Archive, Z-Library, LibGen) zijn e-books vaak geüpload zonder ISBN, met het ISBN van de oorspronkelijke hardback, of met een algemeen ASIN/digitale identifier. Een strikte ISBN-check heeft hierdoor een hoge foutmarge (*false negatives*).

---

## 2. Voorgestelde Alternatieve Strategieën

### Strategie A: Direct "Title + Author First" (Aanbevolen standaard)
* **Werking:**  
  In plaats van eerst het ISBN te proberen, zoekt Shelfmark direct op `"{title} {author}"` (gecombineerd met de ingestelde taalfilters, bijv. `en,nl`).
* **Voordelen:**
  - **Direct resultaat in 1 zoekactie:** Geen dubbele wachttijd meer. Bij *"Dune Frank Herbert"* heb je direct 50 resultaten binnen 4 seconden.
  - **Veel hogere recall:** Vrijwel alle EPUBs, MOBIs en AZW3-bestanden in Z-Lib en Anna's Archive worden gevonden, ongeacht of de uploader het juiste ISBN heeft ingevuld.
* **Aandachtspunten:**
  - Bij hele korte titels (zoals *"It"* van Stephen King of *"1984"*) zorgt de toevoeging van de auteur ervoor dat de precisie hoog blijft (`It Stephen King`).

---

### Strategie B: Parallelle Zoekopdracht (ISBN + Title/Author tegelijk)
* **Werking:**  
  Shelfmark vuurt beide verzoeken gelijktijdig asynchroon af (`asyncio.gather` of `ThreadPoolExecutor`).
* **Voordelen:**
  - Als het ISBN bestaat, heb je de 100% exacte match.
  - Tegelijkertijd heb je direct alle varianten op titel en auteur paraat.
  - De totale zoektijd is gelijk aan de traagste van de twee (in plaats van optelling van beide).
* **Nadelen:**
  - Verdubbelt het aantal requests naar de mirror per zoekopdracht (iets meer risico op rate limits bij intensief gebruik).

---

### Strategie C: Configureerbare Strategie (`RELEASE_SEARCH_STRATEGY`)
* **Werking:**  
  Een instelling toevoegen aan `.env` en de WebUI Settings onder *Direct Download*:
  - `title_author` (Standaard: snelste, meeste resultaten)
  - `isbn_first` (Huidige gedrag: maximale precisie voor een specifieke druk)
  - `smart_hybrid` (Titel+Auteur, maar filteren/sorteren op ISBN indien aanwezig)
* **Voordelen:**
  - Gebruiker kan zelf kiezen tussen maximale snelheid of strikte editie-precisie.

---

### Strategie D: Editie-weergave & Primaire Editie in Hardcover Provider
* **Werking:**  
  In `shelfmark/metadata_providers/hardcover.py`:
  - Altijd de `canonical_edition` of de Engelstalige hoofduitgave selecteren in plaats van een willekeurige vertaling/reprint.
  - In de zoekresultaten van Shelfmark een subtiele badge tonen (bijv. `EN - Paperback` of `NL - E-book`) zodat de gebruiker weet welke editie aangeklikt wordt.

---

## 3. Concreet Implementatieplan voor Strategie A / C

In `shelfmark/release_sources/direct_download.py`:

```python
# Huidig:
if not expand_search:
    isbn = plan.isbn_candidates[0] if plan.isbn_candidates else None
    if isbn:
        # Zoekt eerst op ISBN...

# Nieuw (Title + Author eerst):
search_strategy = app_config.get("RELEASE_SEARCH_STRATEGY", "title_author")

if search_strategy == "title_author":
    # 1. Direct zoeken op Title + Author (snelst, 95% hits)
    results = search_by_title_author(plan)
    if not results and plan.isbn_candidates:
        # Fallback naar ISBN als titel niets opleverde
        results = search_by_isbn(plan)
else:
    # Klassieke fallback volgorde behouden
    ...
```

---

## 4. Volgende Stappen
1. Testen met specifieke boeken met korte titels om te verifiëren dat `Title + Author` geen ruis introduceert.
2. Keuze maken of we Strategie A direct als standaard instellen of via een `.env` toggle configureren.
