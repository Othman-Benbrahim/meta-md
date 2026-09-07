/**
 * Viewer PDF / Markdown / Metadonnees.
 *
 * Query params attendus :
 *   ?pdf=/rags-static/<theme>/1-Sources/<source>
 *   &md=/rags-static/<theme>/2-Conversions/<stem>.md
 *
 * Deduit rag et source a partir des URL. L'edition ecrit dans la conversion
 * du document : elle a donc besoin de `rag` + `source` (le nom exact du
 * fichier dans 1-Sources/, extension comprise), que seule l'URL du PDF
 * fournit. Sans elle, on retombe sur un affichage read-only.
 */
'use strict';

// Le retrait d'une cloture de code englobante a rejoint `md-rendu.js` : il
// vaut pour toutes les surfaces, pas seulement pour ce visualiseur.

const FRONT_MATTER_RE = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n?/;

/** Separe le front matter YAML du corps Markdown. Robuste CRLF et
 *  aux espaces autour des delimiteurs. Retourne {meta, body}. */
function splitYamlFrontMatter(md) {
    const m = md.match(FRONT_MATTER_RE);
    if (!m) return { meta: {}, body: md };
    return { meta: parseSimpleYaml(m[1]), body: md.slice(m[0].length) };
}

/** Comme splitYamlFrontMatter, mais conserve le bloc brut tel quel.
 *
 *  L'edition ne porte que sur le corps : le front matter est produit par le
 *  serveur a partir du sidecar (champs metier) et de la conversion (cles
 *  techniques). Le renvoyer octet pour octet evite qu'un aller-retour dans
 *  l'editeur ne le reformate ou n'en perde une cle. */
function splitRawFrontMatter(md) {
    const m = (md || '').match(FRONT_MATTER_RE);
    if (!m) return { raw: '', body: md || '' };
    return { raw: m[0], body: md.slice(m[0].length) };
}

/** Parseur YAML minimal : clef: valeur a plat, gere strings quotees. */
function parseSimpleYaml(text) {
    const out = {};
    for (const line of text.split(/\r?\n/)) {
        if (!line.trim() || line.trim().startsWith('#')) continue;
        const kv = line.match(/^([A-Za-z0-9_\-]+)\s*:\s*(.*)$/);
        if (!kv) continue;
        const [, key, rawVal] = kv;
        let val = rawVal.trim();
        if (val.startsWith("'") && val.endsWith("'")) val = val.slice(1, -1);
        else if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
        else if (val.startsWith('[') && val.endsWith(']')) {
            val = val.slice(1, -1).split(',').map(s => s.trim()).filter(Boolean);
        } else if (/^-?\d+$/.test(val)) val = parseInt(val, 10);
        else if (val === 'true') val = true;
        else if (val === 'false') val = false;
        else if (val === 'null' || val === '~' || val === '') val = null;
        out[key] = val;
    }
    return out;
}

/** Rend le front matter en <dl> formatee au-dessus du corps MD.
 *  Champs techniques (prefixe `_` ou legacy) regroupes en fin. */
function renderFrontMatterHtml(meta) {
    const keys = Object.keys(meta || {});
    if (!keys.length) return '';
    // Les clés techniques écrites par la conversion sont préfixées `_`.
    const isTechnical = (k) => k.startsWith('_');
    const business = keys.filter(k => !isTechnical(k) && meta[k] != null && meta[k] !== '');
    const technical = keys.filter(k => isTechnical(k) && meta[k] != null && meta[k] !== '');
    const row = (k) => {
        let v = meta[k];
        if (Array.isArray(v)) v = v.join(', ');
        const display = typeof v === 'string' && /^https?:\/\//.test(v)
            ? `<a href="${escapeHtml(v)}" target="_blank" rel="noopener">${escapeHtml(v)}</a>`
            : escapeHtml(String(v));
        return `<dt>${escapeHtml(k)}</dt><dd>${display}</dd>`;
    };
    return `
        <div class="viewer-md-fm">
            ${business.length ? `<dl class="fm-business">${business.map(row).join('')}</dl>` : ''}
            ${technical.length ? `<dl class="fm-technical">${technical.map(row).join('')}</dl>` : ''}
        </div>`;
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => (
        {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
    ));
}

function parseUrls(pdfUrl, mdUrl) {
    // pdf : /rags-static/<theme>/1-Sources/<source>
    // md  : /rags-static/<theme>/2-Conversions/<stem>.md
    const ctx = { rag: null, source: null };
    try {
        const pdfMatch = decodeURIComponent(pdfUrl).match(/^\/rags-static\/([^/]+)\/1-Sources\/(.+)$/);
        if (pdfMatch) {
            ctx.rag = pdfMatch[1];
            ctx.source = pdfMatch[2].split('/').pop();
        }
    } catch (_) { /* ignore */ }
    try {
        const mdMatch = decodeURIComponent(mdUrl).match(/^\/rags-static\/([^/]+)\/2-Conversions\/(.+)\.md$/);
        if (mdMatch) ctx.rag = ctx.rag || mdMatch[1];
    } catch (_) { /* ignore */ }
    return ctx;
}

/* ====== Rendu MD ====== */

function renderMdRendered(md, target) {
    // Le front matter est sorti du corps et rendu en <dl> au-dessus : ce ne
    // sont pas des lignes du document, et marked les lirait comme du texte.
    // Le corps, lui, passe par le rendu commun aux trois surfaces
    // (`assets/md-rendu.js`), seule facon qu'un meme `.md` s'affiche pareil
    // dans le visualiseur de META-MD et dans la lecture d'EduMD.
    const { meta, body } = splitYamlFrontMatter(md);
    window.rendreMarkdownDans(target, body, renderFrontMatterHtml(meta));
    activerReperesDePage(target);
}

/**
 * Amene l'apercu PDF sur une page donnee.
 *
 * Modifier le `#page=` d'un PDF deja affiche ne produit rien : Chrome et
 * Edge ne lisent ce fragment qu'au chargement du document, et le navigateur
 * ne recharge pas une adresse qui ne differe que par son fragment. Il faut
 * donc relancer une vraie navigation, d'ou le remplacement de l'element.
 * Le fichier revient du cache HTTP, le saut est immediat.
 */
function ouvrirPageDuPdf(pdfUrl, page) {
    const ancien = document.getElementById('pdf-frame');
    if (!ancien) return;
    const neuf = ancien.cloneNode(false);
    neuf.src = `${pdfUrl}#page=${page}`;
    ancien.replaceWith(neuf);
}

/**
 * Rend cliquables les reperes de page du Markdown.
 *
 * La conversion note la page d'origine de chaque passage, et le PDF est
 * justement ouvert a cote : cliquer un repere y amene l'apercu. Sans PDF
 * affiche, le repere reste une simple frontiere, on ne promet pas une
 * action qui ne marcherait pas.
 */
function activerReperesDePage(target) {
    const pdfUrl = new URLSearchParams(window.location.search).get('pdf');
    if (!pdfUrl || !document.getElementById('pdf-frame')) return;
    target.querySelectorAll('.md-page-mark[data-page]').forEach(repere => {
        const page = repere.dataset.page;
        repere.classList.add('clickable');
        repere.tabIndex = 0;
        repere.setAttribute('role', 'button');
        repere.title = `Voir la page ${page} du PDF`;
        const aller = () => ouvrirPageDuPdf(pdfUrl, page);
        repere.addEventListener('click', aller);
        repere.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); aller(); }
        });
    });
}

/* ====== Metadonnees ====== */

async function loadMetadata(ctx) {
    if (!ctx.rag || !ctx.source) return null;
    try {
        const url = `/api/corpus/source-metadata?corpus=${encodeURIComponent(ctx.rag)}&source=${encodeURIComponent(ctx.source)}`;
        const resp = await fetch(url);
        return await resp.json();
    } catch (e) { return null; }
}

/**
 * D'où vient la valeur d'un champ, en un mot.
 *
 * Trois origines et non deux : « pas déduit par un modèle » recouvrait à la
 * fois la valeur portée par le jeu de données du lot et la correction faite
 * à la main. Les afficher pareil laissait croire qu'on avait corrigé des
 * champs auxquels personne n'avait touché.
 */
const SOURCES_META = {
    lot: { classe: 'lot', texte: 'source', titre: "Valeur portée par le jeu de données d'origine" },
    auto: { classe: 'auto', texte: 'LLM', titre: 'Valeur déduite par un modèle, à relire' },
    manual: { classe: 'manual', texte: 'corrigé', titre: 'Valeur saisie ou corrigée à la main' },
};

function pastilleSource(source) {
    const s = SOURCES_META[source] || SOURCES_META.manual;
    return `<span class="meta-field-source ${s.classe}" title="${escapeHtml(s.titre)}">${escapeHtml(s.texte)}</span>`;
}

/**
 * Bloc « URL d'origine ».
 *
 * `source_url` ne vient pas du schéma : elle est posée à l'étape 1 pour les
 * documents d'un lot, ou saisie au dépôt. On la présente donc à part,
 * au-dessus des champs métier, pour qu'on ne la confonde pas avec eux. Elle
 * reste modifiable même quand elle vient d'un lot : une URL officielle peut
 * être fausse ou avoir bougé. Elle est écrite dans le front matter du MD au
 * même titre que les champs métier.
 */
function renderSourceUrlBlock(meta, frozen) {
    const roAttr = frozen ? ' readonly disabled' : '';
    const url = (meta && meta.source_url) || '';
    // Icone seule : le libelle vit dans le `title` et l'`aria-label`, comme
    // pour les autres acces reduits a leur pictogramme.
    const open = url
        ? ` <a class="meta-source-url-open" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"
              title="Ouvrir la source dans un nouvel onglet" aria-label="Ouvrir la source dans un nouvel onglet"><i class="fa-solid fa-arrow-up-right-from-square" aria-hidden="true"></i></a>`
        : '';
    return `
        <div class="meta-source-url">
            <label class="meta-field">
                <span class="meta-field-label">URL d'origine${open}</span>
                <input type="text" data-meta-source-url value="${escapeHtml(url)}" placeholder="https://…"${roAttr}>
            </label>
            <p class="meta-source-url-note muted small">Lien public vers ce document. Écrit dans le front matter du Markdown, pour qu'un lecteur puisse remonter à la source.</p>
        </div>`;
}

/**
 * Champ de saisie d'une métadonnée, selon le type déclaré au schéma.
 *
 * Le type porte l'intention : une liste fermée se choisit dans un menu, un
 * booléen n'a que trois états possibles, des tags se saisissent séparés par
 * des virgules et repartent en liste. `data-meta-type` accompagne la valeur
 * jusqu'à l'enregistrement, qui la reconvertit (voir
 * `collectMetadataPayload`).
 */
function metaFieldInput(field, val, roAttr, source = 'manual') {
    const key = escapeHtml(field.key);
    const type = field.type || 'text';
    // L'origine et la valeur d'depart voyagent avec le champ : a
    // l'enregistrement, un champ qu'on n'a pas touche doit repartir avec
    // l'origine qu'il avait, et non devenir une correction (voir
    // `collectMetadataPayload`).
    const attrs = `data-meta-key="${key}" data-meta-type="${escapeHtml(type)}"`
        + ` data-meta-source="${escapeHtml(source)}" data-meta-initial="${escapeHtml(val)}"${roAttr}`;
    const placeholder = escapeHtml(field.description || '');

    if (type === 'enum' && (field.options || []).length) {
        const options = field.options.map(opt => {
            const o = String(opt);
            return `<option value="${escapeHtml(o)}"${o === val ? ' selected' : ''}>${escapeHtml(o)}</option>`;
        }).join('');
        // Une valeur hors vocabulaire (schéma modifié après coup) doit
        // rester visible plutôt que d'être remplacée en silence.
        const orphan = val && !field.options.some(o => String(o) === val)
            ? `<option value="${escapeHtml(val)}" selected>${escapeHtml(val)} (hors liste)</option>`
            : '';
        return `<select ${attrs}><option value=""></option>${options}${orphan}</select>`;
    }
    if (type === 'bool') {
        const sel = v => (val === v ? ' selected' : '');
        return `<select ${attrs}>
            <option value=""></option>
            <option value="true"${sel('true')}>oui</option>
            <option value="false"${sel('false')}>non</option>
        </select>`;
    }
    if (type === 'number') {
        return `<input type="number" step="any" ${attrs} value="${escapeHtml(val)}" placeholder="${placeholder}">`;
    }
    if (type === 'url') {
        return `<input type="url" ${attrs} value="${escapeHtml(val)}" placeholder="https://…">`;
    }
    if (type === 'date') {
        // Pas d'`input[type=date]` : les dates partielles (une année seule)
        // sont légitimes et un sélecteur de calendrier les interdirait.
        return `<input type="text" ${attrs} value="${escapeHtml(val)}" placeholder="AAAA-MM-JJ, AAAA-MM ou AAAA">`;
    }
    if (type === 'tags') {
        return `<input type="text" ${attrs} value="${escapeHtml(val)}" placeholder="séparés par des virgules">`;
    }
    if (type === 'text') {
        return `<textarea ${attrs} rows="2" placeholder="${placeholder}">${escapeHtml(val)}</textarea>`;
    }
    // keyword, et tout type inconnu.
    return `<input type="text" ${attrs} value="${escapeHtml(val)}" placeholder="${placeholder}">`;
}

function renderMetaEditor(container, meta, schema, ctx, options = {}) {
    const frozen = !!options.frozen;
    // Les champs marqués `fixed` — aujourd'hui `source_url` — ont leur propre
    // bloc au-dessus. Les laisser aussi dans la liste des champs métier les
    // afficherait deux fois, et surtout les deux saisies n'écriraient pas au
    // même endroit : le bloc pose la clé de premier niveau du sidecar, la
    // ligne de schéma l'aurait rangée dans `fields`, où elle serait ignorée.
    const fields = ((schema && schema.fields) || []).filter(f => !f.fixed);
    // Etat de la case "Document valide"
    setupValidCheckbox(meta, ctx, { frozen });
    const urlBlock = renderSourceUrlBlock(meta, frozen);
    if (!fields.length) {
        // Le schéma peut être vide sans que l'URL cesse d'être modifiable.
        container.innerHTML = `${urlBlock}<p class="muted small">Le schéma est vide. Ajoutez des champs dans la page du RAG.</p>`;
        if (!frozen) bindMetaEditor(container, {}, ctx);
        return;
    }
    const roAttr = frozen ? ' readonly disabled' : '';
    const existing = (meta && meta.fields) || {};
    const rows = fields.map(f => {
        const entry = existing[f.key];
        let val = '';
        let source = 'manual';
        if (entry && typeof entry === 'object') {
            val = entry.value == null ? '' : (Array.isArray(entry.value) ? entry.value.join(', ') : String(entry.value));
            source = entry.source || 'manual';
        }
        const badge = val ? pastilleSource(source)
            : '<span class="meta-field-source empty">vide</span>';
        return `
            <label class="meta-field">
                <span class="meta-field-label">${escapeHtml(f.label || f.key)}${badge}</span>
                ${metaFieldInput(f, val, roAttr, source)}
            </label>`;
    }).join('');
    container.innerHTML = `${urlBlock}<div class="meta-fields-grid">${rows}</div>`;

    if (!frozen) {
        bindMetaEditor(container, existing, ctx);
    }
}

/**
 * Reconvertit une saisie en valeur du type déclaré.
 *
 * Retourne `null` quand la saisie n'est pas convertible : l'appelant
 * n'envoie alors rien pour ce champ, plutôt que d'écrire une valeur fausse.
 */
function metaValueFromInput(raw, type) {
    if (type === 'tags') {
        const items = raw.split(/[,;\n]/).map(x => x.trim()).filter(Boolean);
        return items.length ? items : null;
    }
    if (type === 'bool') {
        if (raw === 'true') return true;
        if (raw === 'false') return false;
        return null;
    }
    if (type === 'number') {
        const n = Number(raw.replace(',', '.'));
        return Number.isFinite(n) ? n : null;
    }
    return raw;
}

function collectMetadataPayload(container) {
    // Recolte le contenu courant du formulaire + la case de validation pour
    // un POST /api/corpus/source-metadata. La case unique pilote les deux
    // drapeaux du sidecar : on valide (ou on invalide) le tout d'un bloc.
    const payload = { md_valid: false, metadata_valid: false, fields: {} };
    // Clé technique, hors `fields`. Vide = effacement demandé, donc envoyé.
    // Une saisie invalide, elle, n'est pas envoyée du tout : l'autosave part
    // à chaque frappe, et une URL en cours de saisie ne doit pas détruire
    // celle qui est déjà enregistrée. La clé absente laisse le sidecar intact.
    const urlInput = container.querySelector('[data-meta-source-url]');
    if (urlInput) {
        const v = (urlInput.value || '').trim();
        if (!v || /^https?:\/\//i.test(v)) payload.source_url = v;
    }
    const cbx = document.getElementById('conversion-valid-input');
    const valid = !!(cbx && cbx.checked);
    payload.md_valid = valid;
    payload.metadata_valid = valid;
    container.querySelectorAll('[data-meta-key]').forEach(el => {
        const key = el.dataset.metaKey;
        const v = (el.value || '').trim();
        if (!v) return;
        const value = metaValueFromInput(v, el.dataset.metaType);
        // Une saisie inconvertible (un nombre qui n'en est pas un) n'écrase
        // pas la valeur enregistrée : la clé absente laisse le sidecar
        // intact, l'autosave partant à chaque frappe.
        if (value === null) return;
        // Un champ dont la valeur n'a pas bougé garde son origine. Sans ça,
        // le simple fait d'enregistrer — après une correction du Markdown,
        // par exemple — repeignait tout le sidecar en « corrigé », y compris
        // les valeurs venues du lot auxquelles personne n'avait touché.
        const inchange = v === (el.dataset.metaInitial || '');
        const origine = inchange ? (el.dataset.metaSource || 'manual') : 'manual';
        payload.fields[key] = { value, source: origine };
    });
    return payload;
}

async function saveMetadata(container, ctx, status) {
    const payload = collectMetadataPayload(container);
    try {
        const resp = await fetch('/api/corpus/source-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ corpus: ctx.rag, source: ctx.source, metadata: payload }),
        });
        const data = await resp.json();
        if (data && data.ok) {
            if (status) { status.textContent = '✓ enregistré'; status.className = 'status saved'; }
        } else {
            if (status) { status.textContent = '✗ ' + (data && data.error || 'erreur'); status.className = 'status error'; }
        }
    } catch (e) {
        if (status) { status.textContent = '✗ ' + String(e); status.className = 'status error'; }
    }
}

/**
 * Case unique « Conversion validée ».
 *
 * Le sidecar garde deux drapeaux distincts (`md_valid`, `metadata_valid`) :
 * ils encodent *pourquoi* une validation a sauté — reconvertir invalide le
 * Markdown, changer le schéma invalide les métadonnées. Mais l'utilisateur
 * n'a qu'un geste à faire, donc une seule case, cochée quand les deux sont
 * vrais et qui les positionne ensemble.
 */
function setupValidCheckbox(meta, ctx, options = {}) {
    const readOnly = !!options.frozen;
    const cbx = document.getElementById('conversion-valid-input');
    const wrapper = cbx ? cbx.closest('.topbar-check') : null;
    if (!cbx || !wrapper) return;
    const isChecked = !!(meta && meta.md_valid && meta.metadata_valid);
    cbx.checked = isChecked;
    cbx.disabled = readOnly;
    wrapper.classList.toggle('checked', isChecked);
    wrapper.classList.toggle('is-disabled', readOnly);
    wrapper.title = readOnly
        ? "Cette conversion n'est pas celle de référence : validez-la après l'avoir choisie."
        : 'Markdown relu et métadonnées correctes. Requis pour segmenter ce document.';
    if (cbx.dataset._bound) return;
    cbx.dataset._bound = '1';
    cbx.addEventListener('change', async () => {
        wrapper.classList.toggle('checked', cbx.checked);
        const status = document.getElementById('viewer-status');
        if (status) { status.textContent = 'enregistrement…'; status.className = 'status'; }
        await saveMetadata(document.getElementById('meta-editor'), ctx, status);
    });
}

function bindMetaEditor(container, existingFields, ctx) {
    let saveTimer = null;
    const status = document.getElementById('viewer-status');

    const scheduleSave = () => {
        if (status) { status.textContent = 'modifié…'; status.className = 'status dirty'; }
        clearTimeout(saveTimer);
        saveTimer = setTimeout(async () => {
            if (status) status.textContent = 'enregistrement…';
            await saveMetadata(container, ctx, status);
        }, 500);
    };

    const urlInput = container.querySelector('[data-meta-source-url]');
    if (urlInput) {
        const note = container.querySelector('.meta-source-url-note');
        const noteText = note ? note.textContent : '';
        urlInput.addEventListener('input', () => {
            // Le serveur ignore silencieusement ce qui n'est pas http(s) :
            // sans ce rappel, l'utilisateur verrait sa saisie disparaitre au
            // rechargement sans comprendre pourquoi.
            const v = urlInput.value.trim();
            const bad = v && !/^https?:\/\//i.test(v);
            if (note) {
                note.textContent = bad
                    ? 'L\'adresse doit commencer par http:// ou https:// pour être enregistrée.'
                    : noteText;
                note.classList.toggle('is-invalid', !!bad);
            }
            scheduleSave();
        });
    }

    container.querySelectorAll('[data-meta-key]').forEach(el => {
        const onEdit = () => {
            // Dès qu'on tape dans un champ, sa valeur devient une correction :
            // la pastille le dit tout de suite, avant même l'enregistrement.
            const label = el.closest('.meta-field').querySelector('.meta-field-source');
            if (label) {
                const s = el.value.trim()
                    ? SOURCES_META.manual
                    : { classe: 'empty', texte: 'vide', titre: 'Aucune valeur' };
                label.className = `meta-field-source ${s.classe}`;
                label.textContent = s.texte;
                label.title = s.titre;
            }
            scheduleSave();
        };
        el.addEventListener('input', onEdit);
        // `change` en plus : les menus déroulants (liste fermée, oui/non)
        // n'émettent pas `input` sur tous les navigateurs.
        if (el.tagName === 'SELECT') el.addEventListener('change', onEdit);
    });
}

/* ====== Edition MD ====== */

/** Relit le MD sur le serveur. Retombe sur `fallback` si la relecture echoue. */
async function refetchMd(mdUrl, fallback) {
    if (!mdUrl) return fallback;
    try {
        const resp = await fetch(mdUrl, { cache: 'no-store' });
        if (!resp.ok) return fallback;
        return await resp.text();
    } catch (_) {
        return fallback;
    }
}

function setupMdEdit(ctx, initialMd, options = {}) {
    const frozen = !!options.frozen;
    const mdPane = document.getElementById('md-pane');
    const mdContent = document.getElementById('md-content');
    const hoteEditeur = document.getElementById('md-editor');
    const editorNote = document.getElementById('md-editor-note');

    // L'editeur est CodeMirror, pas une zone de texte, et pour une seule
    // raison : une image en `data:` pese jusqu'a 116 000 caracteres — un
    // quart du document pour une figure — et un `<textarea>` affiche sa
    // valeur brute, sans moyen d'en replier une portion. CodeMirror la
    // remplace a l'AFFICHAGE par un bloc insecable : le document, lui, n'est
    // jamais modifie, donc copier rend le base64 entier et supprimer
    // l'emporte entier. Voir `assets/codemirror.entree.js`.
    //
    // Il est cree au premier passage en edition, jamais avant : un document
    // qu'on ne fait que lire n'a pas a payer l'instanciation.
    let editeur = null;
    const surSaisie = () => {
        dirty = editeur.getValue() !== bodyOf(currentMd);
        if (status) {
            if (dirty) { status.textContent = 'MD modifié (non enregistré)'; status.className = 'status dirty'; }
            else { status.textContent = ''; status.className = 'status'; }
        }
    };
    const editeurPret = () => {
        if (!editeur) {
            editeur = window.MetaMDEditor.creer(hoteEditeur, '', { onChange: surSaisie });
        }
        return editeur;
    };
    const btnEdit = document.getElementById('btn-edit-md');
    const btnSave = document.getElementById('btn-save-md');
    const btnCancel = document.getElementById('btn-cancel-md');
    const status = document.getElementById('viewer-status');

    let currentMd = initialMd;
    let dirty = false;

    if (frozen) {
        btnEdit.disabled = true;
        btnEdit.title = 'Gelé : le thème est marqué comme référence. Retirez la référence pour modifier ce document.';
        return { setMd: (m) => { currentMd = m; } };
    }

    // L'editeur ne montre que le corps : le front matter est reconstruit par
    // le serveur depuis le sidecar a chaque enregistrement, le modifier ici
    // n'aurait aucun effet. Les metadonnees s'editent dans le panneau dedie.
    const bodyOf = (md) => splitRawFrontMatter(md).body;

    const enterEdit = () => {
        const ed = editeurPret();
        ed.setValue(bodyOf(currentMd));
        hoteEditeur.classList.remove('hidden');
        mdContent.classList.add('hidden');
        if (editorNote) editorNote.classList.remove('hidden');
        mdPane.classList.add('editing');
        btnEdit.classList.add('hidden');
        btnSave.classList.remove('hidden');
        btnCancel.classList.remove('hidden');
        dirty = false;
        // On ouvre l'edition la ou on lisait : au debut. Sans cela le curseur
        // reste ou le remplacement l'a laisse, en fin de document, et la vue
        // saute au bas d'un fichier qui fait des centaines de milliers de
        // caracteres.
        ed.vue.dispatch({ selection: { anchor: 0 }, scrollIntoView: true });
        // L'hote vient d'apparaitre : CodeMirror a mesure sa hauteur alors
        // qu'elle etait nulle, et son scroller ne saurait pas defiler.
        ed.vue.requestMeasure();
        ed.focus();
    };
    const exitEdit = () => {
        hoteEditeur.classList.add('hidden');
        mdContent.classList.remove('hidden');
        if (editorNote) editorNote.classList.add('hidden');
        mdPane.classList.remove('editing');
        btnEdit.classList.remove('hidden');
        btnSave.classList.add('hidden');
        btnCancel.classList.add('hidden');
    };

    if (!ctx.rag || !ctx.source) {
        // Sans rag + source, le serveur ne peut pas retrouver la conversion
        // active a reecrire : bouton edit desactive.
        btnEdit.disabled = true;
        btnEdit.title = 'Édition indisponible : URL non reconnue';
        return { setMd: (m) => { currentMd = m; } };
    }

    btnEdit.addEventListener('click', enterEdit);
    btnCancel.addEventListener('click', () => {
        if (dirty && !confirm('Annuler les modifications non enregistrées ?')) return;
        exitEdit();
    });
    btnSave.addEventListener('click', async () => {
        // On renvoie le front matter d'origine, inchangé, suivi du corps édité.
        const newMd = splitRawFrontMatter(currentMd).raw + editeurPret().getValue();
        if (status) { status.textContent = 'enregistrement…'; status.className = 'status'; }
        try {
            const resp = await fetch('/api/corpus/md-content', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    corpus: ctx.rag, source: ctx.source, content: newMd,
                }),
            });
            const data = await resp.json();
            if (data && data.ok) {
                // Le serveur renormalise le front matter depuis le sidecar
                // apres l'ecriture : on relit le fichier plutot que d'afficher
                // ce qui a ete tape, sinon une cle metier modifiee a la main
                // dans le front matter semblerait avoir ete conservee.
                currentMd = await refetchMd(options.mdUrl, newMd);
                dirty = false;
                if (status) { status.textContent = '✓ MD enregistré'; status.className = 'status saved'; }
                renderMdRendered(currentMd, mdContent);
                exitEdit();
            } else {
                if (status) { status.textContent = '✗ ' + (data && data.error || 'erreur'); status.className = 'status error'; }
            }
        } catch (e) {
            if (status) { status.textContent = '✗ ' + String(e); status.className = 'status error'; }
        }
    });

    // Bloque la fermeture d'onglet si edition non sauvee.
    window.addEventListener('beforeunload', (e) => {
        if (dirty) { e.preventDefault(); e.returnValue = ''; }
    });

    return { setMd: (m) => { currentMd = m; } };
}

/**
 * Lien vers la page publiee, en barre haute du viewer.
 *
 * Il remplace l'apercu PDF pour un document importe d'un site : `source_url`
 * pointe la page en ligne, c'est la seule chose a montrer a cote du Markdown.
 * Sans elle — un depot sans adresse de site — la barre reste telle quelle.
 */
function poserLienDuSite() {
    const barre = document.querySelector('.viewer-topbar');
    if (!barre || document.getElementById('viewer-lien-site')) return;
    const champ = document.querySelector('[data-meta-key="source_url"]');
    const url = champ && champ.value ? champ.value.trim() : '';
    if (!url) return;
    const lien = document.createElement('a');
    lien.id = 'viewer-lien-site';
    lien.className = 'viewer-lien-site';
    lien.href = url;
    lien.target = '_blank';
    lien.rel = 'noreferrer';
    lien.title = 'Ouvrir la page publiée';
    lien.innerHTML = '<i class="fa-solid fa-arrow-up-right-from-square"></i> Voir la page en ligne';
    barre.appendChild(lien);
}

/* ====== Bootstrap ====== */

(async () => {
    const params = new URLSearchParams(window.location.search);
    const pdfUrl = params.get('pdf');
    const mdUrl = params.get('md');
    const ctx = parseUrls(pdfUrl || '', mdUrl || '');

    // Bouton "Retour" : destination explicite plutot que history.back().
    // Le viewer se rejoint depuis la page de validation du corpus (etape 5) ;
    // y retourner directement est fiable, alors que l'historique depend de la
    // navigation faite dans le viewer (bascule de moteur, rechargements apres
    // action).
    const backBtn = document.getElementById('viewer-back-btn');
    if (backBtn) {
        const target = ctx.rag
            ? `validation.html?corpus=${encodeURIComponent(ctx.rag)}`
            : 'index.html';
        backBtn.title = ctx.rag ? `Retour à la validation de ${ctx.rag}` : 'Retour à l\'accueil';
        backBtn.addEventListener('click', () => { window.location.href = target; });
    }

    const frame = document.getElementById('pdf-frame');
    const mdEl = document.getElementById('md-content');
    const titleEl = document.getElementById('viewer-title');
    const metaEditor = document.getElementById('meta-editor');

    if (pdfUrl) {
        frame.src = pdfUrl;
    } else {
        // Un document venu d'un site n'a pas de PDF, et n'en aura jamais : ce
        // n'est pas une erreur a signaler mais une mise en page a changer. Le
        // volet gauche disparait au lieu d'afficher un cadre vide ou un
        // message qui laisserait croire a un oubli.
        document.body.classList.add('viewer-sans-pdf');
        poserLienDuSite();
    }

    if (!mdUrl) {
        if (titleEl) titleEl.textContent = '…';
        if (mdEl) mdEl.innerHTML = '<p class="viewer-error">Aucun MD spécifié (paramètre ?md=…).</p>';
        return;
    }

    try {
        const name = decodeURIComponent(mdUrl.split('/').pop() || '');
        if (titleEl) titleEl.textContent = name.replace(/\.md$/, '');
    } catch (_) {
        if (titleEl) titleEl.textContent = mdUrl;
    }

    let mdText = '';
    try {
        const resp = await fetch(mdUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        mdText = await resp.text();
        renderMdRendered(mdText, mdEl);
    } catch (e) {
        mdEl.innerHTML = `<p class="viewer-error">Erreur : ${e.message}</p>`;
    }

    let metaResult = null;
    if (ctx.rag && ctx.source) {
        metaResult = await loadMetadata(ctx);
    }

    // Un document n'a qu'une conversion, et c'est celle que le serveur
    // reecrit : le mode lecture seule, qui servait a regarder la conversion
    // d'un autre moteur, n'a plus d'objet.
    setupMdEdit(ctx, mdText, { frozen: false, mdUrl });

    if (metaResult && metaResult.ok) {
        renderMetaEditor(metaEditor, metaResult.metadata, metaResult.schema, ctx, { frozen: false });
    } else if (ctx.rag && ctx.source) {
        metaEditor.innerHTML = `<p class="muted small">Impossible de charger les métadonnées.</p>`;
    } else {
        metaEditor.innerHTML = `<p class="muted small">Métadonnées indisponibles : URL non reconnue.</p>`;
    }
})();

function escapeAttr(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
