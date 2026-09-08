# Wijzigingen in Shelfmark Fork t.o.v. Upstream (origin/main)

Dit document beschrijft alle technische aanpassingen en optimalisaties die zijn doorgevoerd in deze Shelfmark-fork om betrouwbaar boeken te kunnen zoeken en downloaden via Anna's Archive, LibGen en alternatieve bronnen op een ARM64 / Docker-omgeving.

---

## Inhoudsopgave

1. [Probleemstelling & Doelstellingen](#1-probleemstelling--doelstellingen)
2. [Overzicht van Gewijzigde Bestanden](#2-overzicht-van-gewijzigde-bestanden)
3. [Gedetailleerde Technische Wijzigingen](#3-gedetailleerde-technische-wijzigingen)
   - [3.1. Cookie Persistentie & Thread-Safety (`cookie_store.py`)](#31-cookie-persistentie--thread-safety-cookie_storepy)
   - [3.2. CDP Browser Cold-Start & Mirror Failover (`internal_bypasser.py`)](#32-cdp-browser-cold-start--mirror-failover-internal_bypasserpy)
   - [3.3. DDoS-Guard Challenge Detectie (`challenge.py`)](#33-ddos-guard-challenge-detectie-challengepy)
   - [3.4. HTTP Rate-Limit Handoff & Failover (`http.py`)](#34-http-rate-limit-handoff--failover-httppy)
   - [3.5. Release & Detail Mirror Rotatie (`direct_download.py`)](#35-release--detail-mirror-rotatie-direct_downloadpy)
   - [3.6. Z-Library Login, DiamWall & Browser-Direct Download](#36-z-library-login-diamwall--browser-direct-download)
4. [Mirrors Update & Cookie Pre-Warming Script (`scripts/update_mirrors.py`)](#4-mirrors-update--cookie-pre-warming-script-scriptsupdate_mirrorspy)
5. [Aanbevolen Implementatie: Nachtelijke Cronjob](#5-aanbevolen-implementatie-nachtelijke-cronjob)
6. [Migratie naar Productie](#6-migratie-naar-productie)

---

## 1. Probleemstelling & Doelstellingen

In de standaard upstream-versie van Shelfmark liepen gebruikers tegen verschillende blokkades aan:
1. **DDoS-Guard Challenges & Koude Starts**: Anna's Archive maakt gebruik van DDoS-Guard. Elke zoekopdracht startte Chromium in een Xvfb-display. Op ARM64 Docker-omgevingen leidde dit geregeld tot `RuntimeError: Pure CDP browser startup failed: Failed to connect to the browser`.
2. **Geen Cookie Persistentie**: Opgeloste clearance-cookies werden enkel in het werkgeheugen gehouden. Na herstart of sessie-refresh moest Chrome opnieuw worden opgestart, wat elke zoekopdracht met 15-25 seconden vertraagde.
3. **Rate-Limiting (HTTP 429) & Ontbrekende Failover**: Anna's Archive mirrors (zoals `.gl`) zetten bij herhaalde aanroepen een IP-cooldown van 120s. Shelfmark brak de zoekopdracht direct af in plaats van door te schakelen naar actieve alternatieve mirrors (`.gd`, `.pk`).
4. **Lock Deadlocks**: Bij gelijktijdige aanroepen kon een niet-reentrante `threading.Lock` in `cookie_store.py` leiden tot een deadlock.
5. **Z-Library DiamWall (HTTP 513) & TLS Fingerprinting**: Upstream Shelfmark liep vast op Z-Library downloads omdat DiamWall (HTTP 513) standaard Python `requests` blokkeert en een browser-directe downloadsessie met ingelogde sessiecookies vereist.

---

## 2. Overzicht van Gewijzigde Bestanden

| Bestand | Belangrijkste wijziging |
| :--- | :--- |
| `shelfmark/config/env.py` | Inlezen van Z-Library inloggegevens (`ZLIB_EMAIL`, `ZLIB_PASSWORD`) en optionele cookies (`ZLIB_REMIX_*`). |
| `shelfmark/bypass/cookie_store.py` | Schijf-persistentie voor cookies/UA (`clearance_cookies.json`), universele Z-Library sessie-synchronisatie over alle mirrors, en disk-isolatie bij unit tests. |
| `shelfmark/bypass/internal_bypasser.py` | CDP browser start patch (ARM), geautomatiseerde Z-Library modal login (`#loginForm`), en directe bestandsdownload via CDP (`download_via_browser()`). |
| `shelfmark/bypass/challenge.py` | DDoS-Guard fingerprint markers én DiamWall HTML-markers (`diamwall`, `<title>verifying your browser`, `/.well-known/diamwall/`). |
| `shelfmark/download/http.py` | Automatische mirror failover bij 429, 200 OK challenge responses, en retryable afhandeling van HTTP 513 DiamWall challenges. |
| `shelfmark/release_sources/direct_download.py` | Mirror failover loops én directe koppeling naar `download_via_browser()` voor Z-Library releases. |
| `scripts/update_mirrors.py` | Open-SLUM mirror sync voor AA, LibGen en Z-Lib, status 503/513 allowlist, nachtelijke cookie pre-warming, en container execution onder `-w /tmp`. |
| `scripts/generate_env_docs.py` | Automatische documentatiegeneratie inclusief Z-Library bootstrap credentials en configuratie. |

---

## 3. Gedetailleerde Technische Wijzigingen

### 3.1. Cookie Persistentie & Thread-Safety (`cookie_store.py`)
* **Locatie**: `shelfmark/bypass/cookie_store.py`
* **Probleem**: Zodra een DDoS-Guard clearance cookie was verkregen, ging deze verloren bij container-herstart of proceswisseling. Bovendien trad er bij schijfoperaties binnen dezelfde thread een deadlock op met `threading.Lock()`.
* **Aanpassing**:
  - `_cf_cookies_lock` gewijzigd van `threading.Lock()` naar `threading.RLock()` (re-entrant lock).
  - Functies `_load_store_from_disk()` en `_save_store_to_disk()` toegevoegd. Cookies en User-Agents worden opgeslagen in `/config/clearance_cookies.json`.
  - Bij importeren van de module worden bestaande cookies automatisch ingeladen.
  - Vervaldata (`expiry`) worden gecontroleerd; verlopen cookies worden automatisch opgeruimd.
  - Zodra Chrome een challenge oplost, worden de cookies direct weggeschreven. Hierdoor duren vervolgverzoeken slechts **~1 à 2 seconden** via gewone HTTP.

### 3.2. CDP Browser Cold-Start, Xvfb Lifecycle & Mirror Failover (`internal_bypasser.py`)
* **Locatie**: `shelfmark/bypass/internal_bypasser.py`
* **Probleem**:
  1. Op ARM64 Docker doet Chromium er soms langer over om de remote debugging poort te initialiseren dan de hardcoded timeout in SeleniumBase/CDP.
  2. **Xvfb / Virtual Display Desynchronisatie**: `_BypassHelper` blijft als achtergrondproces draaien om overhead te beperken. Echter, zodra de browser sloot of een proces opschoning (`_cleanup_orphan_processes`) Xvfb afsloot, bleef `sb_config._virtual_display` in het Python-geheugen bewaard. Bij een volgende bypass ging SeleniumBase ervan uit dat Xvfb al actief was en sloeg het opstarten over. Chromium crashte vervolgens direct (`Missing X server or $DISPLAY: The platform failed to initialize. Exiting.`), waarna CDP 45s bleef wachten op een dode browser en crashte met een timeout.
  3. Als een mirror een cooldown heeft (`network.host_cooldown_remaining > 0`), gooide `get_bypassed_page()` direct een `RateLimitedError`.
* **Aanpassing**:
  - `_patch_cdp_browser_start()` toegevoegd: Onderschept `cdp_driver.Browser.start()` en voegt een robuuste retry-loop toe (tot 20 seconden) die wacht tot de debug-poort reageert.
  - `_reset_virtual_display()` en `_cleanup_dead_x11_locks()` toegevoegd: Garandeert dat elke bypass met een schone lei start. Stale virtual displays worden expliciet gestopt, `_virtual_display = None` en `_xvfb_users = 0` worden gereset, `DISPLAY` wordt opgeruimd en dode `/tmp/.X*-lock` bestanden worden gewist. Dit wordt aangeroepen bij start, afsluiten, fouten en weesproces-cleanup.
  - **Helper Herstart bij Browser Fouten**: Als `_get_via_subprocess()` een browser-, display- of CDP-fout rapporteert, wordt het helper-proces direct weggegooid (`_discard(wait_for_exit=False)`) zodat het volgende verzoek altijd met een fris proces start.
  - **FFmpeg Resolutie Clamping**: In `_start_ffmpeg_recording()` wordt de `-video_size` dynamisch begrensd tot de werkelijke Xvfb-afmetingen (`1440x1880`), waardoor FFmpeg niet langer crasht met exit code 234 bij willekeurige schermresoluties.
  - In `get_bypassed_page()`: Zodra `host_cooldown_remaining > 0` of als `RateLimitedError` optreedt, wordt direct `sel.next_mirror_or_rotate_dns()` aangeroepen. De URL wordt herschreven naar de alternatieve mirror (`.gd` of `.pk`) en direct geprobeerd.

### 3.3. DDoS-Guard Challenge Detectie (`challenge.py`)
* **Locatie**: `shelfmark/bypass/challenge.py`
* **Probleem**: DDoS-Guard serveert soms een HTTP 200 OK HTML-pagina met inline JavaScript die een fingerprint-script inlaadt (`/js/fingerprint/iife.min.js` of `fingerprintjs.load`). Shelfmark zag dit aan voor een volwaardige pagina.
* **Aanpassing**:
  - `_RAW_HTML_MARKERS` uitgebreid met `"/js/fingerprint/iife.min.js"` en `"fingerprintjs.load"`.
  - Shelfmark herkent deze pagina's nu feilloos als challenge en stuurt ze door naar de bypasser.

### 3.4. HTTP Rate-Limit Handoff & Failover (`http.py`)
* **Locatie**: `shelfmark/download/http.py`
* **Probleem**: In `html_get_page()` leidde een `RateLimitedError` in `_run_bypasser()` tot het beëindigen van de zoekactie met een foutmelding.
* **Aanpassing**:
  - In `_run_bypasser()`: Bij het vangen van `network.RateLimitedError` controleert de code of `selector and network.is_aa_auto_mode()`. Zo ja, dan roteert de selector direct naar de volgende mirror en herhaalt `html_get_page()` met de nieuwe mirror URL.
  - In de HTTP-ontvangstloop: Detectie toegevoegd voor 200 OK responses met een challenge-marker. Stale cookies worden gepurged en de bypasser wordt direct aangeroepen.

### 3.5. Release & Detail Mirror Rotatie (`direct_download.py`)
* **Locatie**: `shelfmark/release_sources/direct_download.py`
* **Probleem**: Zowel bij zoekopdrachten (`_fetch_search_table_uncached`) als bij het ophalen van detailpagina's van een release (`get_book_info`) brak de code af als de eerste mirror een leeg resultaat of foutmelding gaf.
* **Aanpassing**:
  - In `_fetch_search_table_uncached()`: Als `html` leeg is, wordt via `selector.next_mirror_or_rotate_dns()` de URL herschreven en gaat de loop door naar de volgende mirror.
  - In `get_book_info()`: Gewrapt in een `for _ in range(len(network.get_available_aa_urls()) or 1):` retry-loop met `selector.next_mirror_or_rotate_dns()`. Hierdoor faalt een download van releases niet als de primaire mirror geblokkeerd is.

### 3.6. Z-Library Login, DiamWall & Browser-Direct Download
* **Locaties**: `shelfmark/config/env.py`, `shelfmark/bypass/challenge.py`, `shelfmark/download/http.py`, `shelfmark/bypass/cookie_store.py`, `shelfmark/bypass/internal_bypasser.py`, `shelfmark/release_sources/direct_download.py`
* **Probleem**: 
  1. Z-Library gebruikt **DiamWall** WAF-bescherming (HTTP statuscode `513`), waardoor reguliere Python `requests` worden geweigerd op basis van TLS fingerprinting.
  2. Zonder geldige account-login geldt een strikte downloadlimiet (~5 boeken/dag/IP).
  3. Directe downloadlinks vereisen browseruitvoering om het daadwerkelijke bestand op te halen.
* **Aanpassing**:
  - **Inloggegevens**: `ZLIB_EMAIL` en `ZLIB_PASSWORD` worden ingelezen via `shelfmark/config/env.py`.
  - **DiamWall Detectie**: HTTP status `513` is toegevoegd aan de retryable challenge handlers (`http.py`), en HTML-markers (`diamwall`, `<title>verifying your browser`, `/.well-known/diamwall/`) zijn opgenomen in `challenge.py`.
  - **Automatische Modal Login**: In `internal_bypasser.py` opent Chromium de login-modal (`#loginForm`), vult de inloggegevens in en verifieert succesvolle login via `#navProfile`.
  - **Universele Sessie-Persistentie**: Sessiecookies (`remix_userid` en `remix_userkey`) worden opgevangen in `cookie_store.py` (`clearance_cookies.json`) en automatisch geïnjecteerd over **alle** actieve Z-Library mirrors.
  - **Browser-Direct Download**: `download_via_browser()` in `internal_bypasser.py` navigeert via CDP direct naar de `/dl/...` URL, bewaakt het wegschrijven van `.crdownload` bestanden in `/tmp/shelfmark/seleniumbase/downloaded_files/` en levert het voltooide e-book direct op aan de Shelfmark downloadmanager.
  - 📄 *Zie voor de volledige blauwdruk: [`fork-changes/zlib-integration.md`](zlib-integration.md).*

---

## 4. Mirrors Update & Cookie Pre-Warming Script (`scripts/update_mirrors.py`)

Het script `scripts/update_mirrors.py` haalt werkende mirrors op van [Open-SLUM](https://open-slum.org/) en automatiseert het beheer van de configuratie.

### Belangrijkste Eigenschappen:
1. **Niet-blokkerende Live Check**:
   - Gebruikt streaming `GET` verzoeken i.p.v. `HEAD`.
   - Accepteert statuscodes `403` (challenge), `429` (rate-limit), `503` en `513` (DiamWall / Cloudflare) als **levend**. Hierdoor blijven actieve mirrors zoals `z-lib.gl` en `z-lib.gd` behouden in plaats van ten onrechte te worden afgekeurd.
2. **Behoud van `.env` Instellingen**:
   - Overschrijft niet langer het complete `.env` bestand, maar past uitsluitend de mirror-omgevingsvariabelen aan (`AA_MIRROR_URLS`, `LIBGEN_MIRROR_URLS`, `ZLIB_MIRROR_URLS`, `AA_BASE_URL`). Z-Library credentials (`ZLIB_EMAIL`, `ZLIB_PASSWORD`) blijven intact.
3. **Slimme Mirror Rangschikking & Filtering**:
   - Sorteert Anna's Archive mirrors met de stabiele zoekextensies voorop (`.gl`, `.gd`, `.pk`) en filtert CDN data-clusters (`yqrii5.org`, `wbsg8v.xyz`) en software repo's eruit.
   - Filtert Z-Library gateway-redirectors (`go-to-library.sk`, `library-access.sk`) automatisch uit en selecteert zuivere search/download mirrors.
4. **Proactieve Cookie Pre-Warming (`prewarm_mirrors`)**:
   - Voert per geconfigureerde mirror (zowel Anna's Archive als Z-Library) een lichte testzoekopdracht uit in de draaiende Shelfmark container.
   - Lost 's nachts proactief alle DDoS-Guard en DiamWall challenges op, inclusief automatische Z-Library login.
   - Docker exec calls maken gebruik van `-w /tmp` zodat Chromium CDP browser lockfiles veilig aangemaakt kunnen worden onder de geconfigureerde non-root gebruiker (`1001:1001`). Overdag zijn alle downloads daardoor direct snel.

### Beschikbare Parameters:
```bash
python3 scripts/update_mirrors.py --help
# Opties:
#   --service SERVICE     Docker Compose service naam (standaard: shelfmark)
#   --container CONTAINER Directe container naam (bijv. shelfmark of shelfmark-test)
#   --no-restart          Herstart container niet bij wijzigingen
#   --no-prewarm          Sla de cookie pre-warming over
#   --prewarm-only        Draai uitsluitend de cookie pre-warming zonder Open-SLUM check
```

---

## 5. Aanbevolen Implementatie: Nachtelijke Cronjob

Het advies is om dit script één keer per nacht via `cron` op de host (`vm1-arm`) te laten draaien. Hierdoor worden eventuele nieuwe mirrors toegevoegd en worden alle DDoS-Guard cookies klaargezet voor de komende dag.

### Installatie via Crontab:
Open de crontab van de `ubuntu` gebruiker op `vm1-arm`:
```bash
crontab -e
```

Voeg de volgende regel toe (draait elke nacht om 04:00):
```cron
0 4 * * * /usr/bin/python3 /home/ubuntu/docker/shelfmark/scripts/update_mirrors.py >> /home/ubuntu/docker/shelfmark/scripts/update_mirrors.log 2>&1
```

---

## 6. Migratie naar Productie

Zodra je de werking in de teststack hebt gecontroleerd, kunnen de wijzigingen als volgt naar productie worden overgezet:

1. **Kopieer het update-script naar productie**:
   ```bash
   cp /home/ubuntu/projects/shelfmark-fork/scripts/update_mirrors.py /home/ubuntu/docker/shelfmark/scripts/update_mirrors.py
   chmod +x /home/ubuntu/docker/shelfmark/scripts/update_mirrors.py
   ```

2. **Commit of sync de fork code**:
   De aangepaste code in `shelfmark/` kan worden gecommit naar je Git repository of gemount in je productie `docker-compose.yml`.

3. **Voer een initiële pre-warming uit op productie**:
   ```bash
   python3 /home/ubuntu/docker/shelfmark/scripts/update_mirrors.py --prewarm-only
   ```
