#!/usr/bin/env python3
"""
update_mirrors.py
Haalt actuele werkende mirrors op van Open-SLUM (https://open-slum.org/),
werkt Shelfmark's mirrors.json en .env veilig bij, en kan optioneel
cookies pre-warmen voor alle actieve mirrors via de Shelfmark container.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
import urllib.request
import urllib.error

# Paden relatief aan project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))


def get_config_dir(custom_path=None):
    """Bepaalt het actieve config pad (productie 'config' of test '.local-test/config')."""
    if custom_path:
        return os.path.abspath(custom_path)
    local_test = os.path.join(PROJECT_ROOT, ".local-test", "config")
    prod_config = os.path.join(PROJECT_ROOT, "config")
    if not os.path.isdir(prod_config) and os.path.isdir(local_test):
        return local_test
    return prod_config


def get_mirrors_json_path(custom_config_dir=None):
    return os.path.join(get_config_dir(custom_config_dir), "plugins", "mirrors.json")


ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

OPEN_SLUM_URL = "https://open-slum.org/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

# Voorkeursvolgorde voor stabiliteit
AA_PREFERRED_ORDER = [".gl", ".gd", ".pk", ".li", ".org", ".se"]


def fetch_open_slum_html():
    """Haalt de homepage HTML op van Open-SLUM."""
    req = urllib.request.Request(
        OPEN_SLUM_URL,
        headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as e:
        print(f"[!] Fout bij ophalen van {OPEN_SLUM_URL}: {e}")
        return None


def _sort_aa_mirrors(urls):
    """Sorteert AA mirrors op basis van voorkeur (.gl, .gd, .pk voorop)."""
    def key_fn(u):
        for idx, ext in enumerate(AA_PREFERRED_ORDER):
            if u.endswith(ext):
                return idx
        return len(AA_PREFERRED_ORDER)
    return sorted(urls, key=key_fn)


def parse_open_slum_mirrors(html):
    """
    Parset actieve mirrors uit de Open-SLUM HTML.
    Zoekt naar domeinnamen van Anna's Archive, LibGen en Z-Library.
    """
    results = {
        "aa": [],
        "libgen": [],
        "zlib": []
    }

    if not html:
        return results

    # Anna's Archive domeinen
    aa_matches = set(re.findall(r"https?://(?:www\.)?annas-archive\.[a-z0-9]+", html, re.IGNORECASE))
    aa_filtered = [url.rstrip('/') for url in aa_matches if "software." not in url]
    results["aa"] = _sort_aa_mirrors(aa_filtered)

    # LibGen domeinen
    libgen_matches = set(re.findall(r"https?://(?:www\.)?libgen\.[a-z0-9]+", html, re.IGNORECASE))
    results["libgen"] = sorted([url.rstrip('/') for url in libgen_matches])

    # Z-Library domeinen
    zlib_matches = set(re.findall(
        r"https?://(?:www\.)?(?:z-lib\.[a-z0-9]+|singlelogin\.[a-z0-9]+|1lib\.[a-z0-9]+|z-library\.[a-z0-9]+)",
        html,
        re.IGNORECASE
    ))
    results["zlib"] = sorted([url.rstrip('/') for url in zlib_matches])

    return results


def check_url_live(url, timeout=8):
    """
    Snelle validatiecheck of de URL reageert.
    Gebruikt GET (met stream) i.p.v. HEAD, omdat Cloudflare en DDoS-Guard
    HEAD-requests vaak botweg weigeren (status 400/403).
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Lees een klein beetje data om de verbinding te bevestigen
            resp.read(512)
            return resp.status < 500
        # Status 403 / 503 / 513 (WAF challenge), 429 (rate-limit), 401 (auth), 405 (method)
        # of redirects (301, 302, 307, 308) betekenen dat de server actief is en reageert.
        return e.code in [301, 302, 307, 308, 401, 403, 405, 429, 503, 513]
    except Exception:
        return False


def update_mirrors_json(active_aa, active_libgen, active_zlib, config_dir=None):
    """Werkt config/plugins/mirrors.json direct bij met behoud van bestaande opties."""
    mirrors_json_path = get_mirrors_json_path(config_dir)
    if not os.path.exists(mirrors_json_path):
        print(f"[!] {mirrors_json_path} niet gevonden. Wordt nieuw aangemaakt.")
        current_data = {
            "AA_BASE_URL": "auto",
            "AA_MIRROR_URLS": [],
            "LIBGEN_MIRROR_URLS": [],
            "ZLIB_MIRROR_URLS": [],
            "WELIB_MIRROR_URLS": ["https://welib.org"]
        }
    else:
        backup_path = f"{mirrors_json_path}.bak"
        shutil.copy2(mirrors_json_path, backup_path)
        with open(mirrors_json_path, "r", encoding="utf-8") as f:
            try:
                current_data = json.load(f)
            except json.JSONDecodeError:
                current_data = {}

    changed = False

    if active_aa and current_data.get("AA_MIRROR_URLS") != active_aa:
        current_data["AA_MIRROR_URLS"] = active_aa
        changed = True

    if active_libgen and current_data.get("LIBGEN_MIRROR_URLS") != active_libgen:
        current_data["LIBGEN_MIRROR_URLS"] = active_libgen
        changed = True

    if active_zlib and current_data.get("ZLIB_MIRROR_URLS") != active_zlib:
        current_data["ZLIB_MIRROR_URLS"] = active_zlib
        changed = True

    # Zorg dat welib aanwezig is als fallback
    if not current_data.get("WELIB_MIRROR_URLS"):
        current_data["WELIB_MIRROR_URLS"] = ["https://welib.org"]
        changed = True

    if changed:
        os.makedirs(os.path.dirname(mirrors_json_path), exist_ok=True)
        with open(mirrors_json_path, "w", encoding="utf-8") as f:
            json.dump(current_data, f, indent=2)
        print(f"[✓] {mirrors_json_path} succesvol bijgewerkt.")
    else:
        print(f"[*] Geen wijzigingen nodig in {mirrors_json_path}.")

    return changed


def update_env_file(active_aa, active_libgen, active_zlib):
    """Werkt de actieve mirrors bij in .env zonder bestaande variabelen te wissen."""
    existing_lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    target_keys = {"AA_BASE_URL", "AA_MIRROR_URLS", "LIBGEN_MIRROR_URLS", "ZLIB_MIRROR_URLS"}
    filtered_lines = [
        line for line in existing_lines
        if not any(line.strip().startswith(f"{k}=") for k in target_keys)
    ]

    new_entries = [
        f"# Mirrors bijgewerkt op: {datetime.now().isoformat()}",
        "AA_BASE_URL=auto",
    ]
    if active_aa:
        new_entries.append(f"AA_MIRROR_URLS={','.join(active_aa)}")
    if active_libgen:
        new_entries.append(f"LIBGEN_MIRROR_URLS={','.join(active_libgen)}")
    if active_zlib:
        new_entries.append(f"ZLIB_MIRROR_URLS={','.join(active_zlib)}")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(filtered_lines + new_entries) + "\n")
    print(f"[✓] {ENV_FILE} bijgewerkt.")


def get_container_user(target):
    """Bepaalt de UID:GID van de eigenaar van /config (bijv. 1001:1001 of 1000:1000)."""
    try:
        res = subprocess.run(
            ["docker", "exec", target, "stat", "-c", "%u:%g", "/config"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "shelfmark"


def get_docker_cmd(service="shelfmark", container=None):
    """Bepaalt het juiste Docker-commando voor interactie met de container."""
    target = container or service
    user = get_container_user(target)
    if container:
        return ["docker", "exec", "-w", "/tmp", "-u", user, container]

    try:
        res = subprocess.run(
            ["docker", "compose", "ps", "-q", service],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            return ["docker", "compose", "exec", "-T", "-w", "/tmp", "-u", user, service]
    except Exception:
        pass

    return ["docker", "exec", "-w", "/tmp", "-u", user, service]



def restart_container(service="shelfmark", container=None):
    """Herstart de Shelfmark container via Docker Compose of direct Docker."""
    print(f"[*] Herstarten van Shelfmark ({container or service})...")
    try:
        if container:
            subprocess.run(["docker", "restart", container], check=True)
        else:
            subprocess.run(
                ["docker", "compose", "restart", service],
                cwd=PROJECT_ROOT,
                check=True
            )
        print("[✓] Shelfmark succesvol herstart.")
        print("[*] 12 seconden wachten tot applicatie gestart is...")
        time.sleep(12)
    except subprocess.CalledProcessError as e:
        print(f"[!] Fout bij herstarten van container: {e}")


def prewarm_mirrors(service="shelfmark", container=None):
    """
    Verzamelt en vernieuwt proactief cookies voor alle geconfigureerde AA en Z-Library mirrors.
    Draait een eenmalige lichte zoekopdracht/fetch in de container per mirror.
    Als de cookies al geldig zijn duurt dit <1s; als ze ontbreken of verlopen zijn
    wordt de CDP bypasser automatisch aangeroepen en worden verse cookies opgeslagen in clearance_cookies.json.
    """
    print("[*] Proactieve cookie pre-warming starten voor alle actieve mirrors...")
    docker_base = get_docker_cmd(service=service, container=container)
    python_snippet = (
        "import time\n"
        "from shelfmark.download.http import html_get_page\n"
        "from shelfmark.download import network\n"
        "from shelfmark.core.mirrors import get_zlib_mirrors\n"
        "from shelfmark.bypass.internal_bypasser import get_bypassed_page\n"
        "urls = network.get_available_aa_urls()\n"
        "print(f'[*] AA mirrors in scope: {urls}')\n"
        "for url in urls:\n"
        "    print(f'[*] Pre-warming AA: {url}...')\n"
        "    try:\n"
        "        html = html_get_page(f'{url}/search?q=warmup', allow_bypasser_fallback=True)\n"
        "        print(f'[✓] {url} is gereed ({len(html)} bytes)')\n"
        "    except Exception as e:\n"
        "        print(f'[!] {url} mislukt: {e}')\n"
        "    time.sleep(2)\n"
        "zlib_urls = get_zlib_mirrors()\n"
        "print(f'[*] Z-Library mirrors in scope: {zlib_urls}')\n"
        "for url in zlib_urls:\n"
        "    print(f'[*] Pre-warming Z-Library: {url}...')\n"
        "    try:\n"
        "        html = get_bypassed_page(f'{url}/')\n"
        "        print(f'[✓] {url} is gereed ({len(html)} bytes)')\n"
        "    except Exception as e:\n"
        "        print(f'[!] {url} mislukt: {e}')\n"
        "    time.sleep(2)\n"
    )

    cmd = docker_base + ["python3", "-c", python_snippet]
    try:
        res = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=300
        )
        if res.stdout:
            print(res.stdout.strip())
        if res.returncode == 0:
            print("[✓] Pre-warming succesvol voltooid.")
        else:
            print(f"[!] Pre-warming gaf exit code {res.returncode}: {res.stderr.strip()}")
    except subprocess.TimeoutExpired:
        print("[!] Pre-warming time-out na 300s.")
    except Exception as e:
        print(f"[!] Fout bij uitvoeren van pre-warming: {e}")


def main():
    parser = argparse.ArgumentParser(description="Update Shelfmark mirrors from Open-SLUM.")
    parser.add_argument("--service", default="shelfmark", help="Docker compose service name (default: shelfmark)")
    parser.add_argument("--container", default=None, help="Direct Docker container name if not using compose")
    parser.add_argument("--config-dir", default=None, help="Path to config directory containing plugins/mirrors.json")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart container on mirror changes")
    parser.add_argument("--no-prewarm", action="store_true", help="Skip cookie pre-warming")
    parser.add_argument("--prewarm-only", action="store_true", help="Only run cookie pre-warming without checking Open-SLUM")
    args = parser.parse_args()

    if args.prewarm_only:
        prewarm_mirrors(service=args.service, container=args.container)
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Mirrors ophalen van Open-SLUM...")
    html = fetch_open_slum_html()
    if not html:
        print("[!] Kon Open-SLUM niet bereiken. Huidige configuratie blijft behouden.")
        if not args.no_prewarm:
            prewarm_mirrors(service=args.service, container=args.container)
        sys.exit(0)

    parsed = parse_open_slum_mirrors(html)
    print(f"[*] Gevonden op Open-SLUM: {len(parsed['aa'])} AA, {len(parsed['libgen'])} LibGen, {len(parsed['zlib'])} Z-Lib")

    # Filter/verifieer bereikbaarheid
    verified_aa = [u for u in parsed["aa"] if check_url_live(u)] or parsed["aa"]
    verified_libgen = [u for u in parsed["libgen"] if check_url_live(u)] or parsed["libgen"]
    verified_zlib = [u for u in parsed["zlib"] if check_url_live(u)] or parsed["zlib"]

    print(f"[✓] Geverifieerd: {len(verified_aa)} AA ({', '.join(verified_aa)}), {len(verified_libgen)} LibGen, {len(verified_zlib)} Z-Lib")

    if not verified_aa and not verified_libgen and not verified_zlib:
        print("[!] Geen werkende mirrors gevonden. Geen wijzigingen doorgevoerd.")
        return

    json_changed = update_mirrors_json(verified_aa, verified_libgen, verified_zlib, config_dir=args.config_dir)
    update_env_file(verified_aa, verified_libgen, verified_zlib)

    if json_changed and not args.no_restart:
        restart_container(service=args.service, container=args.container)
    else:
        print("[*] Container herstart overgeslagen.")

    if not args.no_prewarm:
        prewarm_mirrors(service=args.service, container=args.container)


if __name__ == "__main__":
    main()
