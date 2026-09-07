"""Auto-remplissage du front matter d'un document via LLM texte.

A partir du contenu Markdown extrait a l'etape 1 et du schema du RAG,
demande au LLM Albert de proposer des
valeurs pour les champs du schema. Sortie JSON structuree.

Le module est independant du moteur : il travaille sur le Markdown produit,
pas sur le document d'origine. Un second moteur en beneficierait sans rien
avoir a reprendre.

Regle d'or : ne rien inventer. Si un champ n'est pas trouvable dans le
document, on renvoie null / on omet la cle. L'utilisateur completera a
la main dans l'UI.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from .api import ratelimit
from .corpus import schema

logger = logging.getLogger("atelier.autofill")

ALBERT_BASE_URL = "https://albert.api.etalab.gouv.fr"
# Modele Albert par defaut pour l'auto-fill des metadonnees. Configurable
# via `autofill_model` dans config.json. gpt-oss-120b est plus lent que
# Mistral 3.2 24B mais nettement plus fiable sur les taches d'extraction
# structuree en JSON (respect du schema, moins d'hallucinations de dates,
# meilleure gestion des null).
ALBERT_TEXT_MODEL_DEFAULT = "openai/gpt-oss-120b"

# Nombre max de caracteres envoyes au LLM par document.
# Rapport approximatif : 4 caracteres ≈ 1 token (francais).
#
# Quotas Albert tier "experimentation" :
#   gpt-oss-120b : 10 RPM, 128 000 TPM
#   Mistral Small 3.2 24B : 50 RPM, 128 000 TPM
#
# Pour gpt-oss-120b a 10 RPM max, chaque appel dispose d'un budget moyen
# de 12 800 tokens (input + output). En retirant ~1 000 tokens pour la
# sortie JSON + prompt system, l'input du document a droit a ~11 800 tokens
# soit ~47 200 caracteres. On fixe la borne a 60 000 par defaut : au-dessus
# de ce que tolere le TPM au max RPM, mais confortable en pratique parce
# que gpt-oss-120b renvoie a ~15-30 s par appel, ce qui limite naturellement
# la cadence a 2-4 RPM et laisse ~40 000-60 000 tokens de burn rate.
# Configurable dans config.json via `autofill_max_chars`.
MAX_INPUT_CHARS_DEFAULT = 60000
REQUEST_TIMEOUT = 120.0

# Budget par valeur de metadonnee, annonce au modele. Aligne sur la limite
# d'Albert (voir `upload.METADATA_MAX_CHARS`), avec une marge : un LLM
# respecte une consigne de longueur de facon approximative, et l'upload
# tronque de toute facon ce qui depasse.
MAX_METADATA_CHARS = 240

# Le resume, lui, ne voyage pas avec les extraits : il decrit le document
# et reste dans son front matter. Rien ne l'oblige donc a tenir dans le
# budget d'une metadonnee d'extrait. Trois a cinq phrases : de quoi decider
# si un document merite d'etre ouvert, sans se substituer a sa lecture.
MAX_RESUME_CHARS = 600
RESUME_KEYS = ("resume", "résumé", "summary", "abstract")


def _type_hint(field: dict[str, Any]) -> str:
    """Contrainte de forme donnee au LLM pour un champ, selon son type.

    C'est le type declare au schema qui commande, plus le nom de la cle :
    un champ `mots_cles` de type `tags` doit produire un tableau au meme
    titre que `tags`.
    """
    ftype = str(field.get("type") or schema.DEFAULT_FIELD_TYPE)
    if ftype == "tags":
        return "tableau de chaines"
    if ftype == "enum":
        options = field.get("options") or []
        if options:
            return "une seule valeur, exactement parmi : " + " | ".join(str(o) for o in options)
        return "une seule valeur courte"
    if ftype == "date":
        return "date au format YYYY-MM-DD, ou YYYY-MM, ou YYYY si seule l'annee est sure"
    if ftype == "number":
        return "nombre"
    if ftype == "bool":
        return "true ou false"
    if ftype == "url":
        return "URL commencant par http:// ou https://"
    if ftype == "keyword":
        return "valeur courte, telle qu'ecrite dans le document"
    if str(field.get("key") or "").strip().lower() in RESUME_KEYS:
        return f"texte suivi, {MAX_RESUME_CHARS} caracteres maximum"
    return f"texte, {MAX_METADATA_CHARS} caracteres maximum"


def _bloc_connu(connu: dict[str, str] | None) -> str:
    """Ce que le sidecar sait deja du document, mis en clair pour le modele.

    Un document n'arrive pas nu a cette etape : son titre, sa discipline, son
    niveau ou son texte officiel viennent souvent d'un lot, et sont donc surs.
    Les taire obligeait le modele a tout redecouvrir dans le Markdown — et sur
    un document dont la transcription est maigre, a ne rien comprendre du
    tout. Le resume, surtout, se redige mieux en sachant de quoi le document
    est cense parler.
    """
    if not connu:
        return ""
    lignes = [f"- {label} : {valeur}" for label, valeur in connu.items()]
    return (
        "Ce que l'on sait deja de ce document, et qui est etabli :\n"
        + "\n".join(lignes)
        + "\n\nSers-t'en pour comprendre de quoi parle le document, en"
          " particulier pour rediger le resume. Ne les recopie pas telles"
          " quelles dans ta reponse — on te demande ce qui manque, pas ce qui"
          " est deja la. Si l'une de ces informations contredit visiblement le"
          " contenu ci-dessous, c'est le contenu qui fait foi.\n\n"
    )


def _build_prompt(schema_fields: list[dict[str, Any]], md_excerpt: str,
                  connu: dict[str, str] | None = None) -> str:
    """Construit le prompt : instructions, ce qu'on sait deja, schema, contenu."""
    schema_lines = []
    for f in schema_fields:
        key = f.get("key", "")
        label = f.get("label", key)
        desc = f.get("description", "")
        line = f"- {key} : {label}"
        if desc:
            line += f" — {desc}"
        line += f" [{_type_hint(f)}]"
        schema_lines.append(line)
    schema_desc = "\n".join(schema_lines)

    return (
        "Tu es un extracteur de metadonnees. On te fournit le contenu Markdown d'un "
        "document et une liste de champs a renseigner. Tu dois retourner un objet "
        "JSON qui associe a chaque cle une valeur trouvee dans le document, ou null "
        "si l'information n'est pas explicitement presente. Tu n'inventes RIEN.\n"
        "\n"
        "Regles strictes :\n"
        "- Reponse : UNIQUEMENT un objet JSON, sans texte autour, sans bloc de code.\n"
        "- Cles autorisees : uniquement celles de la liste ci-dessous.\n"
        "- Forme de chaque valeur : celle indiquee entre crochets apres le champ.\n"
        "- Si un champ n'est pas mentionne dans le document : mets null.\n"
        # Les metadonnees partent dans Albert, qui borne chaque valeur a 255
        # caracteres. On donne le budget au modele pour qu'il redige une
        # phrase complete a la bonne longueur, plutot que de produire un
        # texte que l'upload devra couper au milieu.
        f"- Chaque valeur doit tenir dans le budget indique entre crochets,"
        " ponctuation comprise. Prefere une phrase complete et dense a un"
        " texte tronque.\n"
        f"- Le champ `resume` fait 3 a 5 phrases en francais, {MAX_RESUME_CHARS} caracteres au plus :"
        " ce que contient le document, pour qui il est ecrit, et ce qu'il permet de faire."
        " Il s'appuie sur le contenu ci-dessous ET sur ce que l'on sait deja du"
        " document. Il decrit le document lui-meme, jamais la forme du fichier :"
        " ne parle ni d'images, ni de code, ni de donnees encodees.\n"
        "\n"
        f"{_bloc_connu(connu)}"
        "Champs a renseigner :\n"
        f"{schema_desc}\n"
        "\n"
        "Contenu du document :\n"
        "-----DEBUT-----\n"
        f"{md_excerpt}\n"
        "-----FIN-----\n"
        "\n"
        "Reponse (objet JSON uniquement) :"
    )


# Une figure posee par le moteur : `![Figure 2.1](data:image/png;base64,…)`.
# La partie encodee pese des dizaines de milliers de caracteres, le nom en
# pese vingt.
_IMAGE_ENCODEE_RE = re.compile(r"!?\[([^\]\n]*)\]\(\s*data:[^)]*\)")


def _retirer_images_encodees(md: str) -> tuple[int, str]:
    """Remplace les images en base64 par leur seul nom.

    **Sans cela, le modele resume l'encodage au lieu du document.** Le moteur
    incorpore chaque figure en `data:` dans le Markdown ; sur un document
    illustre, ces blocs occupent jusqu'a 95 % de ce qui tient dans la fenetre
    d'entree, et le troncage a `max_input_chars` acheve de n'envoyer que cela.
    Les resumes obtenus decrivaient « une image encodee suivie d'un long texte
    aleatoire » — le fichier, jamais le document.

    Le nom de la figure est garde : il vaut mieux que rien, et la description
    detaillee que le prompt de Vision reclame vit de toute facon en citation
    dans le corps, ou elle n'est pas touchee.
    """
    nouveau, n = _IMAGE_ENCODEE_RE.subn(
        lambda m: f"[{m.group(1)}]" if m.group(1) else "", md)
    return n, nouveau


def _strip_md_front_matter(md: str) -> str:
    """Retire le front matter YAML deja present en tete du MD (produit par
    pipeline : title, source, engine, etc). Ces cles sont techniques,
    elles n'aident pas le LLM."""
    if not md.startswith("---"):
        return md
    m = re.match(r"^---\n.*?\n---\n\n?", md, re.DOTALL)
    if m:
        return md[m.end():]
    return md


def _parse_json_response(text: str) -> dict[str, Any]:
    """Extrait un objet JSON de la reponse LLM. Robuste aux bavardages."""
    t = (text or "").strip()
    # Enleve un fence de code eventuel.
    if t.startswith("```"):
        m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", t, re.DOTALL)
        if m:
            t = m.group(1).strip()
        else:
            # Fence non fermee : on prend ce qu'il y a apres la premiere ligne.
            t = t.split("\n", 1)[1] if "\n" in t else ""
            t = t.rstrip("`").strip()
    # Cherche le premier `{` et son `}` correspondant (parenthesage simple).
    start = t.find("{")
    if start == -1:
        return {}
    depth = 0
    end = -1
    for i, ch in enumerate(t[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return {}
    payload = t[start:end]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("JSON invalide dans la reponse LLM : %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def _coerce_value(val: Any, field: dict[str, Any]) -> Any:
    """Ramene une valeur du LLM a la forme declaree par le champ.

    Retourne None quand la valeur ne peut pas etre honnetement convertie :
    mieux vaut un champ vide, que l'utilisateur verra et completera, qu'une
    valeur hors vocabulaire ou une date inventee qui passerait inapercue.
    """
    ftype = str(field.get("type") or schema.DEFAULT_FIELD_TYPE)

    if ftype == "tags":
        if isinstance(val, str):
            items = [x.strip() for x in re.split(r"[,;\n]", val)]
        elif isinstance(val, (list, tuple, set)):
            items = [str(x).strip() for x in val]
        else:
            return None
        items = [x for x in items if x]
        return items or None

    if ftype == "bool":
        if isinstance(val, bool):
            return val
        text = str(val).strip().lower()
        if text in ("true", "vrai", "oui", "yes", "1"):
            return True
        if text in ("false", "faux", "non", "no", "0"):
            return False
        return None

    if ftype == "number":
        if isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            return val
        text = str(val).strip().replace(",", ".")
        try:
            return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except ValueError:
            return None

    if ftype == "date":
        text = str(val).strip()
        m = _DATE_RE.match(text)
        if not m:
            return None
        # Bornes calendaires : le LLM produit parfois des mois a 13.
        month, day = m.group(2), m.group(3)
        if month and not 1 <= int(month) <= 12:
            return None
        if day and not 1 <= int(day) <= 31:
            return None
        return text

    if ftype == "url":
        text = str(val).strip()
        return text if re.match(r"^https?://", text, re.IGNORECASE) else None

    if ftype == "enum":
        text = str(val).strip()
        if not text:
            return None
        options = [str(o) for o in (field.get("options") or [])]
        if not options:
            return text
        # Tolerance sur la casse et les accents absents : le modele rend
        # souvent « Programme » pour l'option « programme ». Hors
        # vocabulaire, on refuse plutot que de polluer une facette.
        for option in options:
            if option.lower() == text.lower():
                return option
        return None

    # text, keyword, et tout type inconnu.
    text = str(val).strip()
    return text or None


def _clean_values(raw: dict[str, Any], schema_fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Filtre et normalise les valeurs renvoyees par le LLM.

    Ne garde que les cles du schema, et ramene chaque valeur a la forme
    declaree par le `type` du champ (voir `_coerce_value`).
    """
    out: dict[str, Any] = {}
    for field in schema_fields:
        key = field.get("key")
        if not key or key not in raw:
            continue
        val = raw[key]
        if val is None:
            continue
        clean = _coerce_value(val, field)
        if clean is None or clean == "" or clean == []:
            continue
        out[key] = clean
    return out


def _call_albert(prompt: str, api_key: str, model: str | None = None) -> str:
    """Appel Albert (chat completions) avec throttling + retry 429.

    Le modele est configurable via `model` (defaut `ALBERT_TEXT_MODEL_DEFAULT`
    = gpt-oss-120b). Le throttler applique les quotas TPM/RPM du tier
    experimentation et retente automatiquement sur 429 (Retry-After ou
    backoff exponentiel).
    """
    payload = {
        "model": model or ALBERT_TEXT_MODEL_DEFAULT,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        resp = ratelimit.call_albert_chat_completions(
            client,
            f"{ALBERT_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            payload=payload,
            estimated_input_chars=len(prompt),
        )
        resp.raise_for_status()
        data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def autofill_md(
    md_content: str,
    schema_fields: list[dict[str, Any]],
    *,
    albert_key: str | None = None,
    albert_model: str | None = None,
    max_input_chars: int | None = None,
    connu: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Auto-remplit un dict de valeurs a partir du contenu Markdown.

    `albert_model` : modele Albert a utiliser (defaut : gpt-oss-120b).
    `max_input_chars` : borne d'entree LLM (defaut : MAX_INPUT_CHARS_DEFAULT).
    `connu` : ce que le sidecar sait deja du document, {libelle: valeur}. Sert
    de contexte, et non de reponse : voir `_bloc_connu`.

    Retourne un dict {cle: valeur} pour les cles du schema effectivement
    remplies. Peut etre vide si le LLM n'a rien trouve ou a echoue.
    """
    if not schema_fields:
        return {}
    if not albert_key:
        logger.info("Autofill : aucune cle Albert disponible, saut.")
        return {}
    body = _strip_md_front_matter(md_content)
    # Avant le troncage, et non apres : c'est tout l'interet. Couper d'abord
    # reviendrait a n'envoyer qu'une image encodee et a la resumer.
    n_images, body = _retirer_images_encodees(body)
    limit = max_input_chars if (max_input_chars and max_input_chars > 0) else MAX_INPUT_CHARS_DEFAULT
    if n_images:
        logger.info("Autofill : %d image(s) encodee(s) retiree(s), %d caractere(s) "
                    "de document a lire.", n_images, len(body))
    if len(body) > limit:
        body = body[:limit]
    prompt = _build_prompt(schema_fields, body, connu)

    engine_used = "albert"
    text = ""
    try:
        text = _call_albert(prompt, albert_key, model=albert_model)
    except httpx.HTTPStatusError as exc:
        logger.warning("Autofill : HTTP %s : %s",
                       exc.response.status_code, exc.response.text[:200])
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Autofill : echec appel LLM : %s", exc)
        return {}

    raw = _parse_json_response(text)
    if not raw:
        logger.info("Autofill : reponse LLM vide ou non parsable (engine=%s).", engine_used)
        return {}
    clean = _clean_values(raw, schema_fields)
    logger.info("Autofill : %d champ(s) remplis via %s.", len(clean), engine_used)
    return clean


# Une valeur de contexte plus longue que cela n'apprend plus rien au modele et
# mange le budget du document. Seul un resume deja pose peut s'en approcher.
MAX_CONNU_PAR_CHAMP = 400


def _deja_su(sidecar: dict[str, Any],
             schema_fields: list[dict[str, Any]]) -> dict[str, str]:
    """Champs du schema deja renseignes, en clair, dans l'ordre du schema.

    On passe par le libelle plutot que par la cle : « Niveau d'enseignement »
    se lit mieux que `niveau_d_enseignement`, et c'est du francais qu'on donne
    a lire au modele, pas un identifiant.

    Le schema complet est attendu ici, et non celui filtre par `only_missing` :
    ce sont justement les champs ecartes parce que deja remplis qui font le
    contexte utile.
    """
    champs = sidecar.get("fields") or {}
    connu: dict[str, str] = {}
    for f in schema_fields:
        cle = str(f.get("key") or "").strip()
        entree = champs.get(cle)
        valeur = entree.get("value") if isinstance(entree, dict) else entree
        if valeur in (None, "", []):
            continue
        if isinstance(valeur, list):
            valeur = ", ".join(str(v) for v in valeur if str(v).strip())
        texte = str(valeur).strip()
        if not texte:
            continue
        if len(texte) > MAX_CONNU_PAR_CHAMP:
            texte = texte[:MAX_CONNU_PAR_CHAMP].rstrip() + "…"
        connu[str(f.get("label") or cle)] = texte
    return connu


def autofill_one(
    md_path: Path,
    source_path: Path,
    schema_fields: list[dict[str, Any]],
    *,
    albert_key: str | None = None,
    only_missing: bool = False,
    albert_model: str | None = None,
    max_input_chars: int | None = None,
) -> dict[str, Any]:
    """Autofill LLM du sidecar d'un unique document.

    Lit le MD `md_path` (typiquement la conversion active du doc), envoie
    au LLM, merge dans le sidecar de `source_path` en preservant les
    valeurs `source: manual`. En mode only_missing, ne cible que les
    champs du schema absents du sidecar.
    """
    from .corpus import metadata  # cycle
    if not md_path.is_file():
        return {"ok": False, "error": f"MD introuvable : {md_path}"}
    if not schema_fields:
        return {"ok": False, "error": "Schema vide."}
    if not albert_key:
        return {"ok": False, "error": "Aucune clé Albert configurée."}

    existing = metadata.read_metadata(source_path)
    effective_schema = schema_fields
    if only_missing:
        existing_keys = {
            k for k, entry in (existing.get("fields") or {}).items()
            if isinstance(entry, dict) and entry.get("value") not in (None, "", [])
        }
        effective_schema = [f for f in schema_fields if f.get("key") not in existing_keys]
        if not effective_schema:
            return {"ok": True, "filled": 0, "skipped": True, "reason": "nothing-missing"}

    try:
        md_content = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    auto_values = autofill_md(
        md_content, effective_schema,
        albert_key=albert_key,
        albert_model=albert_model, max_input_chars=max_input_chars,
        connu=_deja_su(existing, schema_fields),
    )
    merged = metadata.merge_autofill(existing, auto_values or {})
    # Marquer comme soumis tout champ demande au modele, y compris ceux pour
    # lesquels il n'a rien rendu : une entree vide dit « on a essaye ». Sans
    # cette trace, un champ que le document ne contient pas (une date absente,
    # par exemple) resterait indefiniment « a remplir » et bloquerait les
    # etapes suivantes, alors qu'il ne reste qu'a le saisir a la main.
    champs = merged.setdefault("fields", {})
    for f in effective_schema:
        cle = str(f.get("key") or "").strip()
        if cle and cle not in champs:
            champs[cle] = {"value": "", "source": metadata.SOURCE_AUTO}
    try:
        metadata.write_metadata(source_path, merged)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if not auto_values:
        if source_path.parent.name == metadata.SOURCES_SUBDIR:
            from .corpus import frontmatter
            frontmatter.sync_front_matter(source_path.parent.parent, source_path)
        return {"ok": True, "filled": 0, "skipped": True, "reason": "no-values"}
    # Le MD actif doit refleter le sidecar : on y reporte les nouvelles valeurs.
    if source_path.parent.name == metadata.SOURCES_SUBDIR:
        from .corpus import frontmatter
        frontmatter.sync_front_matter(source_path.parent.parent, source_path)
    return {"ok": True, "filled": len(auto_values)}
