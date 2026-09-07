"""Lance la version active et revient au socle en cas de demarrage impossible."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

if __package__:
    from .state import read_state, selected_app, write_state
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lanceur.state import read_state, selected_app, write_state


ROOT = Path(__file__).resolve().parent.parent
# La fenetre du lanceur se ferme avec lui : quand un demarrage echoue, il ne
# reste rien a lire. Ce journal est le seul temoin d'un passage de relais rate.
JOURNAL = ROOT / "data" / "lanceur.log"
JOURNAL_MAX = 200_000
_RAPPEL_CONSOLE = None


def _ecran_indestructible() -> None:
    """Rend `print` incapable d'echouer sur un caractere.

    La console d'un Windows francais est en cp850, ou « … » n'existe pas :
    `print` y leve UnicodeEncodeError. Le message part alors en exception
    plutot qu'a l'ecran, et comme rien ne la rattrape, le lanceur meurt a
    l'instant precis ou il rendait compte. On remplace donc les caracteres
    intraduisibles plutot que de laisser lever.
    """
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _journal(message: str) -> None:
    """Ecrit dans `data/lanceur.log` PUIS a l'ecran, sans jamais faire echouer.

    L'ordre n'est pas un detail. Ce fichier existe pour survivre a la console ;
    ecrire a l'ecran d'abord le rendait dependant d'elle, et une console qui
    refuse le message emportait la trace ecrite avec elle — exactement le
    contraire de ce qu'un journal est cense faire.
    """
    horodate = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        if JOURNAL.is_file() and JOURNAL.stat().st_size > JOURNAL_MAX:
            garde = JOURNAL.read_text(encoding="utf-8", errors="replace")
            JOURNAL.write_text(garde[-JOURNAL_MAX // 2:], encoding="utf-8")
        with JOURNAL.open("a", encoding="utf-8") as flux:
            flux.write(f"{horodate} {message}\n")
    except OSError:
        pass
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001 - l'ecran n'arrete jamais le socle
        pass


STATE_FILE = ROOT / "app" / "active.json"
HEALTH_URL = "http://127.0.0.1:8118/api/version"
RESTART_EXIT_CODE = 75
_running_process: subprocess.Popen[bytes] | None = None


def _healthy(process: subprocess.Popen[bytes], expected: str | None) -> bool:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # Un serveur qui sort par RESTART_EXIT_CODE a DEMARRE : il a
            # repondu, installe une version, et demande le relais. Le compter
            # comme un echec de demarrage — ce que faisait un `return False`
            # sec — marquait la nouvelle version defaillante et revenait en
            # arriere, pour une mise a jour qui venait de reussir. La course
            # se gagne en cliquant « Installer » dans les douze secondes qui
            # suivent l'ouverture, ce qui n'a rien d'exceptionnel quand on
            # relance META-MD justement pour se mettre a jour.
            return process.returncode == RESTART_EXIT_CODE
        try:
            with urlopen(HEALTH_URL, timeout=0.5) as response:  # noqa: S310
                data = json.loads(response.read(4096).decode("utf-8"))
            if data.get("ok") and (expected is None or data.get("version") == expected):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def _start(app_dir: Path) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["METAMD_DATA_DIR"] = str(ROOT / "data")
    env["METAMD_LAUNCHER_PID"] = str(os.getpid())
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, str(app_dir / "server.py")],
        cwd=app_dir,
        env=env,
        creationflags=flags,
    )


def _stop_running_process() -> None:
    global _running_process
    process = _running_process
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _handle_stop(signum: int, _frame: object) -> None:
    # Journalise AVANT d'arreter : un lanceur qui s'en va sans rien dire est
    # indiscernable d'un lanceur qui n'a pas vu son serveur sortir, et les deux
    # appellent des corrections opposees. C'est ce silence qui a masque le
    # Ctrl+C que le serveur s'envoyait a lui-meme (voir _sonde_de_presence).
    _journal(f"*** Arret demande (signal {signum}) ; fermeture du serveur.")
    _stop_running_process()
    raise SystemExit(128 + signum)


def _disable_failed_version(version: str) -> None:
    state = read_state(STATE_FILE)
    if state.get("active") != version:
        return
    previous = state.get("previous")
    state["failed"] = version
    state["active"] = previous
    state["previous"] = None
    write_state(STATE_FILE, state)


def _run_active_version() -> int:
    global _running_process
    app_dir, version = selected_app(ROOT, STATE_FILE)
    _journal(f"Demarrage de la version {version or 'racine'} ({app_dir.name})")
    process = _start(app_dir)
    _running_process = process
    if not _healthy(process, version):
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        if version is None:
            _journal("*** META-MD n'a pas réussi à démarrer.")
            return 1
        _journal(f"*** La version {version} ne démarre pas ; retour à la précédente.")
        _disable_failed_version(version)
        fallback_dir, fallback_version = selected_app(ROOT, STATE_FILE)
        process = _start(fallback_dir)
        _running_process = process
        if not _healthy(process, fallback_version):
            process.terminate()
            _journal("*** La version de secours ne démarre pas.")
            return 1
    try:
        return process.wait()
    except KeyboardInterrupt:
        _stop_running_process()
        return 130


def _journaliser_la_fermeture() -> None:
    """Note dans le journal une fenetre fermee ou un Ctrl+C.

    Sans cela, un lanceur arrete brutalement ne laisse RIEN : le journal
    s'interrompt sur son dernier demarrage, et on ne peut pas distinguer
    « le lanceur a ete tue » de « le lanceur n'a pas vu son serveur sortir ».
    Les deux ont l'air identiques a la relecture, et ils appellent des
    corrections opposees. Windows accorde quelques secondes a ce rappel avant
    de trancher ; on n'y fait qu'une ecriture.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return

    MOTIFS = {0: "Ctrl+C", 1: "Ctrl+Pause", 2: "fenetre fermee",
              5: "session fermee", 6: "arret de Windows"}

    def rappel(evenement):
        _journal(f"*** Lanceur arrete ({MOTIFS.get(evenement, evenement)}).")
        return False   # on laisse Windows terminer normalement

    global _RAPPEL_CONSOLE
    prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    _RAPPEL_CONSOLE = prototype(rappel)
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_RAPPEL_CONSOLE, True)
    except OSError:
        pass


def main() -> int:
    _ecran_indestructible()
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    _journaliser_la_fermeture()
    while True:
        code = _run_active_version()
        _journal(f"Le serveur s'est arrête (code {code}).")
        if code != RESTART_EXIT_CODE:
            return code
        _journal("Activation de la nouvelle version de META-MD…")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        # Sans ce filet, une exception dans la boucle emportait le lanceur en
        # ne laissant qu'une trace a l'ecran — dans une fenetre qui se referme.
        _journal(f"*** Lanceur interrompu : {type(exc).__name__}: {exc}")
        raise
