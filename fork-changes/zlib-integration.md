# 📚 Technische Blauwdruk: Z-Library Integratie (Browser-Direct Download)

Dit document beschrijft de architectuur, testbevindingen en concrete implementatiestappen om directe downloads via Z-Library (o.a. `1lib.sk`) werkend te maken in deze Shelfmark-fork.

---

## 1. Samenvatting & Doel

Z-Library biedt een enorme collectie e-books en staat op recente mirrors (zoals `https://1lib.sk`) directe downloads toe voor zowel gasten als geregistreerde gebruikers. 

In tegenstelling tot LibGen en Anna's Archive vereist Z-Library echter een **browser-direct download flow**:
- Z-Library wordt beschermd door **DiamWall** (geeft HTTP statuscode `513`).
- DiamWall blokkeert reguliere Python `requests` op basis van TLS fingerprinting (JA3/JA4).
- Alleen een echte browser (onze headless Chromium via SeleniumBase CDP) kan de downloadknop triggeren en het bestand binnenhalen.

Tijdens tests in de Shelfmark-container bleek dat headless Chrome een boek (*The Hobbit*, 2.1 MB) succesvol en foutloos kan downloaden naar de schijf via de openbare guest-flow. Dit document legt vast hoe deze flow in de Shelfmark-codebase geïntegreerd kan worden.

---

## 2. Proof-of-Concept Testresultaten

De volgende flow is succesvol geverifieerd in de draaiende `shelfmark-test` container:

```text
[1. Navigatie]
https://1lib.sk/md5/da999049e4d2eda74bdabf702a46b9cb
       │
       ▼
[2. DiamWall WAF Challenge]
Status HTTP 513 -> JavaScript 5s countdown & fingerprint
       │ (Chrome lost dit automatisch op in ~5 seconden)
       ▼
[3. Redirect naar Boekpagina]
https://1lib.sk/book/dPp3yQrevw
       │ (HTML bevat <a class="addDownloadedBook" href="/dl/rd3lMzX2pV">)
       ▼
[4. Download Triggeren]
Navigatie naar /dl/rd3lMzX2pV in Chrome
       │ (Server stuurt Content-Disposition: attachment)
       ▼
[5. Bestand Opgeslagen]
/tmp/shelfmark/seleniumbase/downloaded_files/The Hobbit (J.R.R. Tolkien)...epub (2.1 MB)
```

---

## 3. Waarom de Huidige Upstream-Code Faalt

1. **Onbekende HTTP Challenge (Status 513 & DiamWall):**
   - In `shelfmark/bypass/challenge.py` en `shelfmark/download/http.py` worden alleen HTTP `403` en `503` opgevangen voor Cloudflare en DDoS-Guard. DiamWall stuurt HTTP `513`. Shelfmark behandelt dit momenteel als een fatale serverfout.
2. **TLS Fingerprinting Blokkeert Python `requests`:**
   - Shelfmark gebruikt Chromium normaal gesproken alleen om de HTML op te halen en een directe URL te extraheren. Het daadwerkelijke downloaden gebeurt via Python `requests.get()`.
   - DiamWall herkent de Python/OpenSSL TLS-cipher suites en weigert het downloadverzoek met een 513 interstitial.
3. **Bestandsoverdracht Verwachting:**
   - Shelfmark verwacht een byte-stream in het werkgeheugen (`io.BytesIO`). Bij browser-navigatie slaat Chromium het bestand direct op de harde schijf op in de map `SELENIUMBASE_DOWNLOADS_DIR`.

---

## 4. Stappenplan voor Toekomstige Implementatie

### Stap 1: DiamWall Herkenning Toevoegen
* **Bestanden:** `shelfmark/bypass/challenge.py` & `shelfmark/download/http.py`
* **Wijzigingen:**
  1. Voeg markers toe aan `_RAW_HTML_MARKERS` in `challenge.py`:
     ```python
     "diamwall",
     "<title>verifying your browser",
     "/.well-known/diamwall/",
     ```
  2. Voeg status `513` toe aan `_HTTP_STATUS_CHALLENGE` en `_is_retryable_error` in `http.py`:
     ```python
     _HTTP_STATUS_CHALLENGE = (403, 503, 513)
     ```
  3. Breid `_detect_challenge_type()` in `internal_bypasser.py` uit om DiamWall te herkennen en ~10 seconden te wachten tot de challenge zichzelf oplost.

### Stap 2: Browser-Direct Download Flow Bouwen
* **Bestand:** `shelfmark/bypass/internal_bypasser.py`
* **Functie:** `download_via_browser(url: str, destination_dir: Path, cancel_flag=None) -> Path | None`
* **Werking:**
  1. Start de CDP-browser (of hergebruik de actieve `_BypassHelper`).
  2. Navigeer naar de `/md5/<hash>` pagina en wacht tot de DiamWall-challenge is opgelost.
  3. Lokaliseer de downloadknop `<a class="addDownloadedBook">` en haal het `href` op (bijv. `/dl/...`).
  4. Schakel Chromium download monitoring in op `SELENIUMBASE_DOWNLOADS_DIR`:
     - Luister naar nieuwe bestanden in de map.
     - Trigger de download door te navigeren naar `href` of op de knop te klikken via JS (`element.click()`).
     - Wacht tot eventuele `.crdownload` tijdelijke bestanden zijn verdwenen en het definitieve bestand (`.epub`, `.pdf`, etc.) compleet op schijf staat.
  5. Retourneer het absolute pad naar het gedownloade bestand.

### Stap 3: Koppeling in Direct Download Orchestrator
* **Bestand:** `shelfmark/release_sources/direct_download.py`
* **Locatie:** In `_try_download_url()`:
  - Controleer of de bron `zlib` is.
  - In plaats van `downloader.download_url()` aan te roepen, roep je de nieuwe `download_via_browser()` functie aan.
  - Verplaats het voltooide bestand direct naar de ingest- of boekenmap.

### Stap 4: Account / Session Cookies (Optioneel)
* **Bestanden:** `shelfmark/config/settings.py` & `shelfmark/bypass/cookie_store.py`
* **Functionaliteit:**
  - Niet-ingelogde gasten hebben een limiet van ~5 downloads per dag per IP.
  - Voeg optionele instellingsvelden toe in de Web UI onder *Mirrors* of *Download Sources*:
    - `ZLIB_REMIX_USERID`
    - `ZLIB_REMIX_USERKEY`
  - Bij het openen van de Z-Library mirror in Chromium worden deze twee cookies automatisch geïnjecteerd, waardoor downloads meetellen voor het geregistreerde/VIP account van de gebruiker.

---

## 5. Overwegingen & Aanbevelingen

| Aspect | Situatie | Advies |
| :--- | :--- | :--- |
| **Huidige Status** | LibGen (5 mirrors) + Anna's Archive (3 mirrors) werken stabiel en snel. | Z-Library integratie is momenteel niet strikt noodzakelijk. |
| **Wanneer implementeren?** | Als LibGen én Anna's Archive gelijktijdig zware storingen ondervinden, of bij behoefte aan zeer recente Z-Lib uploads. | Volg het stappenplan in sectie 4. |
| **Mirror Beheer** | Z-Library domeinen wisselen vaak. | `scripts/update_mirrors.py` haalt via Open-SLUM automatisch actuele domeinen (zoals `1lib.sk`) op. |
