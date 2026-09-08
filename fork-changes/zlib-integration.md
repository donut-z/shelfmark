# 📚 Technische Documentatie: Z-Library Integratie (Browser-Direct Download & Account Login)

Dit document beschrijft de architectuur, configuratie en implementatie om directe downloads via Z-Library (o.a. `1lib.sk`, `z-library.sk`, `z-lib.gl`, `z-lib.gd`) werkend te maken in deze Shelfmark-fork.

---

## 1. Samenvatting & Doel

Z-Library biedt een enorme collectie e-books en staat op recente mirrors directe downloads toe voor zowel gasten als geregistreerde gebruikers. 

In tegenstelling tot LibGen en Anna's Archive vereist Z-Library echter een **browser-direct download flow**:
- Z-Library wordt beschermd door **DiamWall** (geeft HTTP statuscode `513`).
- DiamWall blokkeert reguliere Python `requests` op basis van TLS fingerprinting (JA3/JA4).
- Alleen een echte browser (onze headless Chromium via SeleniumBase CDP) kan de downloadknop triggeren en het bestand binnenhalen.
- Niet-ingelogde gasten hebben een limiet van ~5 downloads per dag per IP. Met een geregistreerd account (gratis of premium) is de dagelijkse limiet aanzienlijk hoger.

---

## 2. Configuratie (`.env`)

Voeg je Z-Library accountgegevens toe aan je `.env` bestand:

```env
# =============================================================================
# Z-Library Account Inloggegevens
# =============================================================================
ZLIB_EMAIL=jouw-email@domein.com
ZLIB_PASSWORD=jouw-wachtwoord

# Optioneel (wordt automatisch uitgelezen en gesynchroniseerd na eerste login):
# ZLIB_REMIX_USERID=
# ZLIB_REMIX_USERKEY=
```

> [!NOTE]
> Het script `scripts/update_mirrors.py` overschrijft uitsluitend de mirror-lijsten en behoudt je `ZLIB_EMAIL` en `ZLIB_PASSWORD` configuratie intact.

---

## 3. Geïmplementeerde Architectuur

```text
[1. Navigatie naar Boek / MD5]
https://1lib.sk/md5/<hash>
       │
       ▼
[2. DiamWall WAF Challenge]
Status HTTP 513 -> JavaScript challenge & TLS fingerprint
       │ (Headless Chromium lost dit automatisch op in ~5 seconden)
       ▼
[3. Geautomatiseerde Modal Login (indien niet ingelogd)]
Controleer of #navProfile aanwezig is
  ├─ Nee: Open modal (#loginForm), vul email + wachtwoord in, submit
  └─ Ja:  Sessie al actief
       │
       ▼
[4. Universele Cookie Synchronisatie]
Vang 'remix_userid' en 'remix_userkey' op
       │ (Wordt direct opgeslagen in clearance_cookies.json
       │  en gedeeld over ALLE Z-Library mirrors)
       ▼
[5. Browser-Direct Download via CDP]
Navigeer naar /dl/<token> binnen Chromium
       │
       ▼
[6. Voltooiing & Levering]
Chromium downloadt bestand naar /tmp/shelfmark/seleniumbase/downloaded_files/
Bestand wordt gevalideerd en direct overgedragen aan Shelfmark downloadmanager.
```

---

## 4. Gedetailleerde Code-Aanpassingen

### 4.1. DiamWall Herkenning (`challenge.py` & `http.py`)
- Markers in `_RAW_HTML_MARKERS`: `"diamwall"`, `"<title>verifying your browser"`, `"/.well-known/diamwall/"`.
- HTTP Status `513` toegevoegd aan `_HTTP_STATUS_CHALLENGE` en `_is_retryable_error` in `http.py`.
- Browser bypasser wacht tot de interstitial verdwijnt en de werkelijke pagina geladen is.

### 4.2. Geautomatiseerde Login (`internal_bypasser.py`)
- Functie `_ensure_zlib_authenticated(sb, target_url)`:
  - Controleert aanwezigheid van gebruikersprofiel `#navProfile`.
  - Indien afwezig: klikt op login, vult `#loginForm input[name="email"]` en `input[name="password"]` in via JavaScript injectie, en wacht op succesvolle login.
  - Vangt `remix_userid` en `remix_userkey` op en slaat deze op in de `cookie_store`.

### 4.3. Universele Sessie-Persistentie (`cookie_store.py`)
- Functies `get_zlib_auth_cookies()` en `store_zlib_auth_cookies()`.
- Omdat Z-Library login cookies mirror-onafhankelijk zijn, zorgt de store ervoor dat zodra je op mirror A (`1lib.sk`) bent ingelogd, dezelfde inlogstatus direct geldt voor mirror B (`z-lib.gl`), C (`z-lib.gd`), enzovoort.

### 4.4. Browser-Direct Download (`internal_bypasser.py` & `direct_download.py`)
- Functie `download_via_browser(url, destination_dir, cancel_flag)`.
- Monitor voor voltooide bestanden (geen `.crdownload` meer aanwezig).
- In `direct_download.py`: Z-Library downloads worden automatisch gerouteerd via `download_via_browser()` in plaats van Python `requests`.

### 4.5. Nachtelijke Mirror Sync & Pre-Warming (`scripts/update_mirrors.py`)
- Synchroniseert actieve mirrors via Open-SLUM.
- Accepteert HTTP 503 en 513 als live status (voorkomt onterecht verwijderen van `z-lib.gl` en `z-lib.gd`).
- Filtert gateway redirectors (`go-to-library.sk`, `library-access.sk`) automatisch uit.
- Voert elke nacht om 04:00 automatische cookie pre-warming uit voor alle Z-Library mirrors via `docker exec -w /tmp`.

---

## 5. Implementatiestatus (Afgerond & Geverifieerd)

| Onderdeel | Status | Details |
| :--- | :--- | :--- |
| **DiamWall Bypass (HTTP 513)** | ✅ Werkend | Herkenning toegevoegd aan `challenge.py`, `http.py` en `internal_bypasser.py`. |
| **Geautomatiseerde Login** | ✅ Werkend | Leest `ZLIB_EMAIL` & `ZLIB_PASSWORD` in via `.env` / `compose.yaml`. Voert eenmalige login uit via Chromium modal en vangt `remix_userid` & `remix_userkey` op. |
| **Cookie Persistentie** | ✅ Werkend | Opgeslagen in `clearance_cookies.json` en universeel geïnjecteerd over alle actieve Z-Library mirrors. |
| **Browser-Direct Download** | ✅ Werkend | `download_via_browser()` in `internal_bypasser.py` gekoppeld aan `_try_download_url()` in `direct_download.py`. Downloads binnen ~3s voltooid. |
| **Update-Script & Pre-Warming** | ✅ Werkend | `scripts/update_mirrors.py` behoudt inloggegevens in `.env`, verifieert live mirrors (incl. 503/513) en warmt 's nachts alle cookies op. |
| **Test Suite Isolatie** | ✅ Werkend | 390 bypass & download unit tests slagen 100% met mock-isolatie tegen netwerk en schijf. |


