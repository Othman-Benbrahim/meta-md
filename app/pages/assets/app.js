/**
 * MD-RAG (version locale) - JS de la Pages.
 *
 * Poste les actions sur les endpoints /api/* du server.py local. Aucun
 * appel externe.
 */
'use strict';

/* ============ Jobs longue duree (polling + barre de progression) ============ */

async function apiGetJob(jobId) {
    const resp = await fetch(`/api/corpus/jobs/${encodeURIComponent(jobId)}`);
    return await resp.json();
}

/**
 * Poll un job jusqu'a state=done|error, en appelant onProgress a chaque tick.
 * onProgress({processed, total, current, state, result, error, ratio}).
 * Retourne l'objet job final (state = done ou error).
 */
async function pollJob(jobId, onProgress, intervalMs = 800) {
    while (true) {
        let data;
        try {
            data = await apiGetJob(jobId);
        } catch (e) {
            if (onProgress) onProgress({ state: 'error', error: String(e) });
            return { state: 'error', error: String(e) };
        }
        if (!data || !data.ok) {
            if (onProgress) onProgress({ state: 'error', error: (data && data.error) || 'job introuvable' });
            return { state: 'error', error: (data && data.error) || 'job introuvable' };
        }
        const job = data.job || {};
        const total = job.total || 0;
        const processed = job.processed || 0;
        const ratio = total > 0 ? processed / total : 0;
        if (onProgress) onProgress({ ...job, ratio });
        if (job.state === 'done' || job.state === 'error') return job;
        await new Promise(r => setTimeout(r, intervalMs));
    }
}

function renderProgressBar(container, initialLabel) {
    // Rend une barre de progression + une ligne de statut dans container.
    // Retourne des helpers { update({processed, total, current}), setState(state, text), remove() }.
    container.innerHTML = `
        <div class="progress-wrap">
            <div class="progress-track"><div class="progress-fill" style="width:0%;"></div></div>
            <div class="progress-meta">
                <span class="progress-label">${escapeHtml(initialLabel || 'Démarrage…')}</span>
                <span class="progress-count muted small"></span>
            </div>
            <div class="progress-current muted small"></div>
        </div>
    `;
    const fill = container.querySelector('.progress-fill');
    const label = container.querySelector('.progress-label');
    const count = container.querySelector('.progress-count');
    const current = container.querySelector('.progress-current');
    return {
        update({ processed, total, current: currentName, ratio }) {
            const r = ratio != null ? ratio : (total ? (processed || 0) / total : 0);
            fill.style.width = Math.round(r * 100) + '%';
            if (total) count.textContent = `${processed || 0} / ${total}`;
            if (currentName) current.textContent = currentName;
        },
        setState(state, text) {
            container.classList.remove('is-done', 'is-error');
            if (state === 'done') container.classList.add('is-done');
            if (state === 'error') container.classList.add('is-error');
            if (text) label.textContent = text;
        },
        remove() { container.innerHTML = ''; },
    };
}

/* ============ MESSAGES + SPINNER ============ */

/**
 * Affiche un message. Il est place au plus pres de l'action : une page qui
 * declare `#message-block-local` (au contact de sa zone de travail) le
 * reçoit ; sinon on retombe sur le bandeau `#message-block`, et on l'amene
 * dans le champ de vision plutot que de le laisser hors ecran.
 */
function showMessage(type, title, textOrHtml, {isHtml = false, step = null} = {}) {
    // `step` designe la section concernee (`#msg-<step>`) : c'est le plus
    // precis. Sinon le bandeau local de la page, sinon celui du haut.
    const scoped = step ? document.getElementById(`msg-${step}`) : null;
    const local = scoped || document.getElementById('message-block-local');
    const block = local || document.getElementById('message-block');
    if (!block) return;
    // Les bandeaux de section sont exclusifs : on masque les autres pour ne
    // pas laisser deux messages contradictoires a l'ecran.
    document.querySelectorAll('[id^="msg-"]').forEach(el => {
        if (el !== block) el.classList.add('hidden');
    });
    if (!local) {
        // Le bandeau vit en haut de page : sans ce recentrage, un message
        // declenche depuis le bas de la page passe inaperçu.
        requestAnimationFrame(() => {
            block.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    }
    block.classList.remove('warning-block', 'success-block');
    block.classList.add(type === 'error' ? 'warning-block' : 'success-block');
    block.classList.remove('hidden');
    // Recherche dans le bloc retenu : `document.querySelector` prendrait
    // toujours le premier bandeau de la page, donc celui du haut.
    const titleEl = block.querySelector('[data-bind="message-title"]');
    const el = block.querySelector('[data-bind="message-text"]');
    if (!titleEl || !el) return;
    titleEl.textContent = title;
    if (isHtml) el.innerHTML = textOrHtml;
    else el.textContent = textOrHtml;
    if (type === 'error') console.error(`[${title}]`, textOrHtml);
}

function showLoading(title, text) {
    showMessage('success', title, `<span class="spinner"></span>${escapeHtml(text)}`, {isHtml: true});
}

function hideMessage() {
    const block = document.getElementById('message-block');
    if (block) block.classList.add('hidden');
}

function setBtnLoading(btn, isLoading) {
    if (!btn) return;
    if (isLoading) {
        if (!btn.dataset._html) btn.dataset._html = btn.innerHTML;
        // Anneau CSS plutot que `fa-circle-notch` : le glyphe n'est pas
        // centre dans sa case, il decrit donc un petit cercle en tournant.
        // Une bordure ronde tourne sur elle-meme, par construction.
        btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
        btn.classList.add('is-loading');
        btn.disabled = true;
    } else {
        if (btn.dataset._html) {
            btn.innerHTML = btn.dataset._html;
            delete btn.dataset._html;
        }
        btn.classList.remove('is-loading');
        btn.disabled = false;
    }
}

/** Marque du pluriel français : rien jusqu'à 1 inclus (« 0 document »,
 *  « 1 document »), « s » au-delà. */
function plur(n) {
    return Number(n) > 1 ? 's' : '';
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => (
        {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
    ));
}

function humanBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return `${n} B`;
    for (const unit of ['KB', 'MB', 'GB']) {
        n /= 1024;
        if (n < 1024) return n < 10 ? `${n.toFixed(2)} ${unit}` : `${n.toFixed(1)} ${unit}`;
    }
    return `${n.toFixed(1)} TB`;
}

function fmtDate(v) {
    if (!v) return '-';
    const n = Number(v);
    if (!isNaN(n) && n > 0) {
        const d = new Date(n * 1000);
        return d.toISOString().slice(0, 16).replace('T', ' ');
    }
    return String(v);
}

/* ============ API ============ */

function renderStepPillDone(n, done, label) {
    return `<span class="rag-pill ${done ? 'done' : 'todo'}" title="${escapeHtml(label)}">${n}</span>`;
}

async function loadRagTopics() {
    const container = document.getElementById('rag-topics-container');
    if (!container) return;
    try {
        const resp = await fetch('/api/corpus/topics');
        const data = await resp.json();
        const topics = data.topics || [];
        // Liste vide : rien a dire. Le bouton « Créer un nouveau corpus »
        // est juste au-dessus et se suffit.
        if (!topics.length) {
            container.innerHTML = '';
            return;
        }
        const rows = topics.map(t => {
            const url = `corpus.html?corpus=${encodeURIComponent(t.name)}`;
            const formats = (t.sources.formats || []).join(', ');
            const sourcesLabel = t.sources.exists
                ? `${t.sources.count} source${t.sources.count > 1 ? 's' : ''}${formats ? ' ' + formats.toUpperCase() : ''}`
                : `1-Sources/ absent`;
            const activityLabel = t.last_activity ? `activité ${t.last_activity}` : 'jamais utilisé';
            // Les cinq pastilles suivent les cinq etapes de la page du corpus :
            // meme ordre, meme sens, pour qu'un coup d'oeil a la liste dise ou
            // en est chaque corpus sans avoir a l'ouvrir. Le schema passe
            // devant la conversion, et le remplissage n'est plus une etape :
            // il conditionne la conversion, qui n'est finie que sans trous.
            const convert = t.steps && t.steps.convert || {};
            const fill = t.steps && t.steps.fill || {};
            const validation = t.validation || {};
            const nbSources = t.sources ? t.sources.count : 0;
            const nbActifs = convert.docs_with_active || 0;
            const aRemplir = fill.docs_incomplete || 0;
            const schemaDone = !!(t.schema && t.schema.locked);
            const convertDone = nbActifs > 0 && nbActifs >= nbSources && aRemplir === 0;
            const valides = validation.both_valid || 0;
            const revisionDone = nbActifs > 0 && valides >= nbActifs;
            return `
                <a class="rag-row" href="${url}">
                    <div class="rag-row-main">
                        <div class="rag-row-title">${escapeHtml(t.name)}</div>
                        <div class="rag-row-meta">
                            <i class="fa-solid fa-arrow-right rag-row-icon" aria-hidden="true"></i>
                            <span class="rag-row-path">data/CORPUS/${escapeHtml(t.name)}/</span>
                            <span class="rag-row-sep">·</span>
                            <span>${escapeHtml(sourcesLabel)}</span>
                            <span class="rag-row-sep">·</span>
                            <span>${escapeHtml(activityLabel)}</span>
                        </div>
                    </div>
                    <div class="rag-row-steps">
                        ${renderStepPillDone(1, nbSources > 0, `Source : ${nbSources} document${plur(nbSources)}`)}
                        ${renderStepPillDone(2, schemaDone, schemaDone ? 'Schéma validé et verrouillé' : 'Schéma non verrouillé')}
                        ${renderStepPillDone(3, convertDone, aRemplir > 0
                            ? `Conversion : ${aRemplir} document${plur(aRemplir)} avec des champs à remplir`
                            : `Conversion : ${nbActifs}/${nbSources} converti${plur(nbActifs)}`)}
                        ${renderStepPillDone(4, revisionDone, `Validation : ${valides}/${nbActifs} document${plur(nbActifs)} relu${plur(valides)}`)}
                        ${renderStepPillDone(5, revisionDone && convertDone, 'Markdown prêts à emporter')}
                    </div>
                    <span class="rag-row-open">Ouvrir <i class="fa-solid fa-arrow-right" aria-hidden="true"></i></span>
                </a>`;
        }).join('');
        container.innerHTML = `<div class="rag-list">${rows}</div>`;
    } catch (e) {
        container.innerHTML = `<p class="muted small">Erreur de chargement des corpus : ${escapeHtml(String(e))}</p>`;
    }
}


/**
 * Creation d'un corpus depuis la liste.
 *
 * Un corpus est un dossier : le serveur le cree vide, et on va aussitot sur
 * sa page, ou l'etape 1 attend les documents. Rien n'est depose ici.
 */
function initCreateCorpus() {
    const btn = document.getElementById('btn-create-corpus');
    const champ = document.getElementById('new-corpus-name');
    const statut = document.getElementById('new-corpus-status');
    const apercu = document.getElementById('new-corpus-slug');
    if (!btn || !champ) return;

    // Meme regle que le serveur, qui reste seul juge : ici c'est pour
    // montrer, a la frappe, ce que deviendra le nom saisi.
    const slug = nom => nom.toLowerCase()
        .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');

    const montrerSlug = () => {
        if (!apercu) return;
        const s = slug(champ.value || '');
        apercu.textContent = s ? `data/CORPUS/${s}/` : 'data/CORPUS/…/';
    };
    champ.addEventListener('input', montrerSlug);
    montrerSlug();

    // La source decide de l'etape 1 et de l'etape 3 du corpus. `pdf` par
    // defaut : c'est le seul cas qui existait, et le seul qui ne demande rien.
    const sourceChoisie = () => {
        const coche = document.querySelector('input[name="corpus-source"]:checked');
        return coche ? coche.value : 'pdf';
    };

    const creer = async () => {
        const nom = (champ.value || '').trim();
        if (!nom) {
            statut.textContent = 'Indiquez un nom.';
            statut.classList.add('error');
            champ.focus();
            return;
        }
        statut.classList.remove('error');
        setBtnLoading(btn, true);
        try {
            const resp = await fetch('/api/corpus/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ corpus: nom, source: sourceChoisie() }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.ok) {
                statut.textContent = data.error || `HTTP ${resp.status}`;
                statut.classList.add('error');
                return;
            }
            // Le serveur renvoie le slug retenu : on ouvre celui-la.
            window.location.href = `corpus.html?corpus=${encodeURIComponent(data.corpus || nom)}`;
        } catch (e) {
            statut.textContent = String(e);
            statut.classList.add('error');
        } finally {
            setBtnLoading(btn, false);
        }
    };

    btn.addEventListener('click', creer);
    champ.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); creer(); } });
}

function initRagTopicsPage() {
    if (!document.getElementById('rag-topics-container')) return;
    initCreateCorpus();
    loadRagTopics();
}

/* ============ RAG theme page ============ */

// Noms des moteurs de conversion tels qu'ils s'affichent. Les cles sont
// celles de `SUPPORTED_ENGINES` dans app/rag_convert.py ; `vision` est
// l'ancien nom d'`albert_vision`, encore present dans des sidecars.
const ENGINE_LABELS = {
    pymupdf: 'pymupdf4llm',
    albert_vision: 'Albert Vision',
    vision: 'Albert Vision',
    mistral_document_ai: 'Mistral Document AI',
    albert_ocr: 'Albert OCR',
    // Le libelle affiche ; l'identifiant, lui, reste `albert_vision_figures`
    // — il est ecrit dans les sidecars deja produits, sous `active_engine`.
    albert_vision_figures: 'Albert Vision',
};

// Depuis 2026-08-22 l'interface ne propose plus de choisir : toute conversion
// lancee depuis l'etape 3 part sur ce moteur. Les autres restent supportes
// cote serveur — les conversions deja produites avec eux s'affichent toujours,
// et un appel direct a /api/corpus/document/convert peut encore les demander.
const MOTEUR_CONVERSION = 'albert_vision_figures';

function getQueryParam(name) {
    const p = new URLSearchParams(window.location.search);
    return p.get(name) || '';
}

function renderSourcesInventory(ragName, sources, frozen) {
    const listEl = document.getElementById('sources-inventory-list');
    const countEl = document.getElementById('sources-inventory-count');
    const toolbar = document.getElementById('sources-inventory-toolbar');
    const allCb = document.getElementById('sources-inventory-all');
    const filterInput = document.getElementById('sources-inventory-filter');
    if (!listEl) return;

    const files = sources.files || [];
    const total = files.length;
    const excluded = files.filter(f => f.excluded).length;
    const active = total - excluded;
    if (countEl) {
        if (total === 0) {
            countEl.textContent = 'aucun document pour l\'instant';
        } else {
            countEl.innerHTML = `<strong>${active}</strong> à traiter · <strong>${excluded}</strong> exclu${excluded > 1 ? 's' : ''} / ${total} total · <strong>${sources.active_pages || 0}</strong> pages`;
        }
    }
    if (total === 0) {
        listEl.innerHTML = `<p class="muted small">Rien dans <code>1-Sources/</code> pour l'instant. Utilisez « Ajouter des documents » ci-dessus.</p>`;
        if (toolbar) toolbar.classList.add('hidden');
        return;
    }
    if (toolbar) toolbar.classList.remove('hidden');
    if (allCb) {
        allCb.checked = active === total;
        allCb.indeterminate = active > 0 && active < total;
        allCb.disabled = frozen;
    }

    const rows = files.map(f => {
        const size = f.size != null ? humanBytes(f.size) : '·';
        const pages = f.pages != null ? `${f.pages} p.` : '';
        const safeName = escapeHtml(f.name);
        const stateClass = f.excluded ? 'is-excluded' : '';
        // URL d'origine : lien cliquable quand elle est renseignee, maillon
        // barre et grise sinon. Elle se saisit au depot et se corrige dans
        // le viewer ; ici on ne fait que dire si elle est la.
        const url = (f.source_url || '').trim();
        const urlCell = url
            ? `<a class="source-inv-url is-set" href="${escapeHtml(url)}" target="_blank" rel="noopener"
                  title="URL d'origine : ${escapeHtml(url)}"><i class="fa-solid fa-link"></i></a>`
            : `<span class="source-inv-url" title="Pas d'URL d'origine. Elle s'ajoute dans la page de révision du document."><i class="fa-solid fa-link-slash"></i></span>`;
        const title = (f.title || '').trim();
        return `
            <li class="source-inv-row ${stateClass}" data-source-name="${safeName}">
                <div class="source-inv-main">
                    <label class="checkbox-inline source-inv-check-label" title="${f.excluded ? 'Exclu du pipeline. Cliquer pour re-inclure.' : 'À traiter. Cliquer pour exclure.'}">
                        <input type="checkbox" class="source-inv-check" data-source-name="${safeName}" ${f.excluded ? '' : 'checked'} ${frozen ? 'disabled' : ''}>
                        ${title
                            // Meme presentation qu'a l'etape 5 : le titre porte
                            // l'identite du document, le nom de fichier passe
                            // dessous. Sans titre — avant tout remplissage — le
                            // nom de fichier est tout ce qu'on a : il reste seul.
                            ? `<span class="md-inv-doc">
                                   <span class="md-inv-title">${escapeHtml(title)}</span>
                                   <span class="md-inv-file"><i class="fa-solid fa-file-pdf" aria-hidden="true"></i> ${safeName}</span>
                               </span>`
                            : `<span class="source-inv-name"><i class="fa-solid fa-file-pdf"></i> ${safeName}</span>`}
                    </label>
                    <span class="muted small source-inv-pages">${escapeHtml(pages)}</span>
                    <span class="muted small source-inv-size">${escapeHtml(size)}</span>
                    ${urlCell}
                    <button type="button" class="source-inv-edit" data-source-name="${safeName}"
                            title="Modifier le titre et l'URL d'origine" aria-expanded="false" ${frozen ? 'disabled' : ''}>
                        <i class="fa-solid fa-pen"></i>
                    </button>
                    <button type="button" class="source-inv-del" data-source-name="${safeName}"
                            title="Supprimer ce document et tout ce qui en découle" ${frozen ? 'disabled' : ''}>
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </div>
                <div class="source-inv-editor hidden" data-editor-for="${safeName}">
                    <label class="source-inv-field">
                        <span class="muted small">Titre <span class="req" title="Champ obligatoire">*</span></span>
                        <input type="text" class="source-inv-title-input" value="${escapeHtml(title)}" required
                               placeholder="Titre du document">
                    </label>
                    <label class="source-inv-field">
                        <span class="muted small">URL d'origine</span>
                        <input type="text" class="source-inv-url-input" value="${escapeHtml(url)}"
                               placeholder="https://… (facultative)">
                    </label>
                    <div class="source-inv-editor-actions">
                        <button type="button" class="btn primary btn-tiny source-inv-save" data-source-name="${safeName}"
                                title="Enregistrer" aria-label="Enregistrer">
                            <i class="fa-solid fa-check"></i>
                        </button>
                    </div>
                    <span class="muted small source-inv-save-status"></span>
                </div>
            </li>`;
    }).join('');
    listEl.innerHTML = `<ul class="sources-inv-list">${rows}</ul>`;

    // Filtre par nom (client-side, simple substring).
    if (filterInput) {
        filterInput.value = filterInput.value || '';
        const applyFilter = () => {
            const q = (filterInput.value || '').trim().toLowerCase();
            listEl.querySelectorAll('.source-inv-row').forEach(li => {
                const name = (li.dataset.sourceName || '').toLowerCase();
                li.classList.toggle('hidden', !!q && !name.includes(q));
            });
        };
        filterInput.oninput = applyFilter;
        applyFilter();
    }

    // Édition en place du titre et de l'URL d'origine : les deux seuls champs
    // qu'on peut renseigner sans avoir lu le document. Le reste des
    // métadonnées s'édite dans le viewer, après conversion.
    listEl.querySelectorAll('.source-inv-edit').forEach(btn => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.source-inv-row');
            const editor = row ? row.querySelector('.source-inv-editor') : null;
            if (!editor) return;
            const willOpen = editor.classList.contains('hidden');
            editor.classList.toggle('hidden', !willOpen);
            btn.classList.toggle('is-open', willOpen);
            btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            if (willOpen) {
                const first = editor.querySelector('input');
                if (first) first.focus();
            }
        });
    });

    listEl.querySelectorAll('.source-inv-save').forEach(btn => {
        const row = btn.closest('.source-inv-row');
        const editor = row ? row.querySelector('.source-inv-editor') : null;
        if (!editor) return;
        const titleInput = editor.querySelector('.source-inv-title-input');
        const urlInput = editor.querySelector('.source-inv-url-input');
        const statusEl = editor.querySelector('.source-inv-save-status');
        const fail = (message) => {
            if (!statusEl) return;
            statusEl.textContent = message;
            statusEl.className = 'small source-inv-save-status error';
        };
        const save = async () => {
            const source = btn.dataset.sourceName;
            const titleValue = (titleInput ? titleInput.value : '').trim();
            const urlValue = (urlInput ? urlInput.value : '').trim();
            // Le titre est obligatoire : c'est lui qui identifie le document
            // partout ensuite, un document sans titre n'est pas citable.
            if (!titleValue) {
                fail('Le titre est obligatoire.');
                if (titleInput) { titleInput.classList.add('is-missing'); titleInput.focus(); }
                return;
            }
            if (titleInput) titleInput.classList.remove('is-missing');
            // Une saisie qui n'est pas une URL http(s) n'est pas envoyée : le
            // serveur la refuserait en silence, autant le dire ici.
            if (urlValue && !/^https?:\/\//i.test(urlValue)) {
                fail("L'URL doit commencer par http:// ou https://");
                return;
            }
            setBtnLoading(btn, true);
            if (statusEl) { statusEl.textContent = ''; statusEl.className = 'muted small source-inv-save-status'; }
            try {
                const resp = await fetch('/api/corpus/source-brief', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        corpus: ragName, source,
                        title: titleValue,
                        source_url: urlValue,
                    }),
                });
                const data = await readJsonOrExplain(resp, '/api/corpus/source-brief');
                if (data.__error || !data.ok) {
                    showMessage('error', 'Enregistrement impossible',
                        data.__error || data.error || `HTTP ${resp.status}`,
                        { isHtml: !!data.__error, step: 'extraction' });
                    return;
                }
                if (typeof window.__reloadThemeState === 'function') await window.__reloadThemeState();
            } catch (e) {
                showMessage('error', 'Erreur réseau', String(e), { step: 'extraction' });
            } finally {
                setBtnLoading(btn, false);
            }
        };
        btn.addEventListener('click', save);
        // Entrée dans l'un des deux champs vaut « Enregistrer ».
        editor.querySelectorAll('input').forEach(inp => {
            inp.addEventListener('keydown', event => {
                if (event.key === 'Enter') { event.preventDefault(); save(); }
            });
        });
    });

    // Suppression d'un document. La confirmation dit ce qui part avec lui :
    // le PDF, mais aussi ses conversions, ses metadonnees et ses segments.
    listEl.querySelectorAll('.source-inv-del').forEach(btn => {
        btn.addEventListener('click', async () => {
            const source = btn.dataset.sourceName;
            const ok = confirm(
                `Supprimer "${source}" ?\n\n`
                + `Le PDF sera retiré de 1-Sources/, avec ses conversions, ses métadonnées `
                + `et ses segments. Cette action est irréversible.`);
            if (!ok) return;
            setBtnLoading(btn, true);
            try {
                const resp = await fetch(
                    `/api/corpus/source?corpus=${encodeURIComponent(ragName)}&source=${encodeURIComponent(source)}`,
                    { method: 'DELETE' });
                const data = await readJsonOrExplain(resp, '/api/corpus/source');
                if (data.__error || !data.ok) {
                    showMessage('error', 'Suppression impossible',
                        data.__error || data.error || `HTTP ${resp.status}`,
                        { isHtml: !!data.__error, step: 'extraction' });
                    return;
                }
                // Pas de message : la ligne qui disparait de la liste dit
                // deja que le document est parti. Seul l'echec merite un mot.
                if (typeof window.__reloadThemeState === 'function') await window.__reloadThemeState();
            } catch (e) {
                showMessage('error', 'Erreur réseau', String(e), { step: 'extraction' });
            } finally {
                setBtnLoading(btn, false);
            }
        });
    });

    // Toggle exclusion per row.
    listEl.querySelectorAll('.source-inv-check').forEach(cb => {
        cb.addEventListener('change', async () => {
            const source = cb.dataset.sourceName;
            const wantExcluded = !cb.checked;
            cb.disabled = true;
            try {
                const resp = await fetch('/api/corpus/source-exclude', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ corpus: ragName, source, excluded: wantExcluded }),
                });
                const data = await resp.json();
                if (!data || !data.ok) {
                    showMessage('error', 'Modification impossible',
                        (data && data.error) || `HTTP ${resp.status}`, { step: 'extraction' });
                    cb.checked = !cb.checked;  // revert
                }
            } catch (e) {
                showMessage('error', 'Erreur réseau', String(e), { step: 'extraction' });
                cb.checked = !cb.checked;
            } finally {
                cb.disabled = false;
            }
            // Refresh full state (topics + convert summary + pipeline flow).
            if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
        });
    });

    // "Tout traiter" toggle : coche/decoche toutes les cases visibles, puis
    // envoie les changements en batch (une requête par doc).
    if (allCb) {
        allCb.onchange = async () => {
            const want = allCb.checked;  // true = tout traiter (excluded=false)
            const boxes = Array.from(listEl.querySelectorAll('.source-inv-check'))
                .filter(cb => !cb.closest('.hidden'));  // limite au filtre courant
            for (const cb of boxes) {
                if (cb.checked === want) continue;
                cb.checked = want;
                try {
                    await fetch('/api/corpus/source-exclude', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ corpus: ragName, source: cb.dataset.sourceName, excluded: !want }),
                    });
                } catch (_) { /* silent */ }
            }
            if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
        };
    }
}

/* ============ Extraction (Etape 1 : lots predefinis / lot personnalise) ============ */

async function apiListLots(refresh) {
    const resp = await fetch('/api/corpus/lots' + (refresh ? '?refresh=1' : ''));
    return await resp.json();
}

async function apiPreviewLot(slug) {
    const resp = await fetch(`/api/corpus/lots/${encodeURIComponent(slug)}/preview`);
    return await resp.json();
}

let __lotsCache = null;

function initExtractionBlock(ragName) {
    // Appele au premier depliage du panneau. On en profite pour aller
    // chercher le catalogue : c'est le seul moment ou cette liste sert, et
    // le seul ou l'on accepte d'attendre pour l'avoir a jour. Avant, elle ne
    // se rafraichissait qu'au lancement du serveur et pas plus d'une fois par
    // jour — un lot publie le matin n'apparaissait que le lendemain.
    loadLotsList(ragName, true);

    // Onglets Lot prédéfini / Lot personnalisé (dans le panneau).
    const tabs = document.querySelectorAll('#extraction-block .lot-tab');
    const lotPanels = document.querySelectorAll('#extraction-block .lot-panel');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const mode = tab.dataset.lotMode;
            lotPanels.forEach(p => p.classList.toggle('hidden', p.dataset.lotPanel !== mode));
        });
    });
}

/**
 * Remplit la liste des lots.
 *
 * `refresh` va rechercher le catalogue sur la Forge avant d'afficher. C'est
 * ce que fait l'ouverture du panneau « Ajouter des documents » : le seul
 * moment ou cette liste sert, et donc le bon moment pour la mettre a jour.
 * L'attente est d'une seconde environ — seule l'arborescence du depot est
 * relue quand rien n'a change — et elle est annoncee.
 */
async function loadLotsList(ragName, refresh) {
    const container = document.getElementById('lots-list');
    if (!container) return;
    if (refresh) {
        __lotsCache = null;
        container.innerHTML = `<p class="muted small"><span class="spinner"></span> Recherche des lots disponibles…</p>`;
    }
    let result;
    try {
        result = __lotsCache || await apiListLots(refresh);
    } catch (e) {
        result = { lots: [], catalogue: { ok: false, erreur: String(e) } };
    }
    if (!__lotsCache) __lotsCache = result;
    const lots = (result && result.lots) || [];
    const cat = result && result.catalogue;
    if (!lots.length) {
        const raison = cat && !cat.ok
            ? `Le catalogue n'a pas pu être joint : vérifiez votre connexion.`
            : `Le catalogue ne propose rien pour l'instant.`;
        container.innerHTML = `<p class="muted small">Aucun lot disponible. ${raison} Vous pouvez aussi déposer vos propres lots dans le dossier <code>data/lots</code>.</p>`;
        return;
    }
    container.innerHTML = `<ul class="lots-list">${lots.map(lot => `
        <li class="lot-card" data-lot-slug="${escapeHtml(lot.slug)}">
            <div class="lot-head">
                <div class="lot-info">
                    <strong>${escapeHtml(lot.title || lot.slug)}</strong>
                    <p class="lot-file">${escapeHtml(lot.file || (lot.slug + '.json'))}${lot.origine_label ? ' · ' + escapeHtml(lot.origine_label) : ''}</p>
                </div>
                <div class="lot-head-actions">
                    <button type="button" class="btn" data-lot-preview="${escapeHtml(lot.slug)}">
                        <i class="fa-solid fa-eye"></i> Visualiser
                    </button>
                </div>
            </div>
            <div class="lot-status muted small hidden" data-lot-card-status="${escapeHtml(lot.slug)}">·</div>
            <div class="lot-preview hidden" data-lot-preview-panel="${escapeHtml(lot.slug)}"></div>
        </li>
    `).join('')}</ul>` + ((cat && !cat.ok)
        ? `<p class="muted small">Le catalogue n'a pas pu être joint : cette liste est la dernière connue.</p>`
        : '');

    container.querySelectorAll('[data-lot-preview]').forEach(btn => {
        btn.addEventListener('click', () => showLotPreview(btn.dataset.lotPreview, ragName));
    });
    // Actions déportées au niveau du bandeau : on binde ici plutôt que dans
    // le panneau preview qui devient un simple tableau read-only.
    bindLotHeadActions(container, ragName);
}

function bindLotHeadActions(container, ragName) {
    const setStatus = (slug, text, cls) => {
        const el = container.querySelector(`[data-lot-card-status="${CSS.escape(slug)}"]`);
        if (!el) return;
        el.classList.remove('hidden');
        el.textContent = text;
        el.className = 'lot-status small ' + (cls || 'muted');
    };
    container.querySelectorAll('[data-lot-download]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const slug = btn.dataset.lotDownload;
            if (!confirm('Télécharger TOUS les PDFs de ce lot dans 1-Sources/ ? Ça peut prendre plusieurs minutes. Cliquez sur « Visualiser » pour choisir un sous-ensemble.')) return;
            await runLotDownload(slug, ragName, null, btn);
        });
    });
}

async function showLotPreview(slug, ragName) {
    const panel = document.querySelector(`[data-lot-preview-panel="${CSS.escape(slug)}"]`);
    if (!panel) return;
    if (!panel.classList.contains('hidden')) {
        panel.classList.add('hidden');
        panel.innerHTML = '';
        return;
    }
    panel.classList.remove('hidden');
    panel.innerHTML = `<p class="muted small"><span class="spinner"></span>Chargement de la liste…</p>`;
    const result = await apiPreviewLot(slug);
    if (!result || !result.ok) {
        panel.innerHTML = `<p class="muted small">Erreur : ${escapeHtml((result && result.error) || 'inconnue')}</p>`;
        return;
    }
    panel.innerHTML = renderLotPreview(result.lot, slug, ragName);
    bindLotPreviewActions(panel, slug, ragName);
}

/* Libelles des colonnes connues du tableau d'un lot. Une cle absente d'ici
   s'affiche telle quelle, underscores en espaces : un lot d'un autre format
   reste lisible sans toucher au code. */
const LOT_COLUMN_LABELS = {
    identifiant: 'Identifiant',
    titre: 'Titre',
    cycle: 'Cycle',
    niveau: 'Niveau',
    voie: 'Voie',
    discipline: 'Discipline',
    texte_officiel: 'Texte officiel',
    rentree_debut: 'Rentrée début',
    rentree_fin: 'Rentrée fin',
    en_vigueur: 'En vigueur',
    source_url: 'PDF',
    lien_legifrance: 'Légifrance',
};

function lotColumnLabel(key) {
    if (LOT_COLUMN_LABELS[key]) return LOT_COLUMN_LABELS[key];
    const s = String(key).replace(/_/g, ' ');
    return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Rend une cellule du tableau d'un lot, selon ce que contient la valeur.
 *
 * Les URL deviennent une icône cliquable plutôt qu'une longue chaîne : sans
 * ça, deux colonnes d'URL rendraient le tableau illisible.
 */
function renderLotCell(key, value) {
    // Les exports data.gouv sérialisent parfois l'absence de valeur par la
    // chaîne « None » : l'afficher telle quelle ferait croire à une donnée.
    if (value === null || value === undefined || value === '' || value === 'None') {
        return '<span class="muted">·</span>';
    }
    if (typeof value === 'boolean') {
        return value
            ? '<i class="fa-solid fa-check lot-cell-yes" title="oui"></i>'
            : '<span class="muted" title="non">non</span>';
    }
    const text = String(value);
    if (/^https?:\/\//i.test(text)) {
        const icon = key === 'source_url' ? 'fa-file-pdf' : 'fa-arrow-up-right-from-square';
        return `<a href="${escapeHtml(text)}" target="_blank" rel="noopener" title="${escapeHtml(text)}"><i class="fa-solid ${icon}"></i></a>`;
    }
    return escapeHtml(text);
}

/** Classes de mise en forme d'une colonne : largeur, alignement, taille. */
function lotColumnClass(key) {
    if (key === 'titre') return 'lot-descriptif';
    if (key === 'source_url' || key === 'lien_legifrance') return 'center';
    if (key === 'texte_officiel' || key === 'identifiant') return 'small';
    if (key === 'rentree_debut' || key === 'rentree_fin' || key === 'en_vigueur') return 'right small';
    return '';
}

/* ============ HTML venu d'ailleurs ============ */

// Balises gardees telles quelles dans une valeur venue d'une source
// exterieure. Tout le reste est deballe : le texte survit, la balise non.
const HTML_BALISES_SURES = new Set([
    'P', 'BR', 'EM', 'STRONG', 'B', 'I', 'U', 'SMALL', 'SUP', 'SUB',
    'UL', 'OL', 'LI', 'DL', 'DT', 'DD', 'A', 'CODE', 'SPAN', 'BLOCKQUOTE',
]);

/**
 * Rend une chaine HTML sure a inserer.
 *
 * Une description de jeu de donnees arrive souvent en HTML : l'echapper la
 * rend illisible. Mais elle vient d'un serveur exterieur, donc on ne la
 * recopie pas telle quelle : on la reparse, on ne garde que des balises de
 * mise en forme, et on jette tous les attributs sauf un `href` http(s).
 * La CSP bloquerait deja un script en ligne ; ce filtre ne s'y fie pas.
 */
function sanitizeHtml(source) {
    const doc = new DOMParser().parseFromString(`<div>${source}</div>`, 'text/html');
    const racine = doc.body.firstElementChild;
    if (!racine) return '';
    const nettoie = noeud => {
        Array.from(noeud.children).forEach(enfant => {
            nettoie(enfant);
            if (!HTML_BALISES_SURES.has(enfant.tagName)) {
                // Deballage : les enfants deja nettoyes remontent d'un cran.
                enfant.replaceWith(...enfant.childNodes);
                return;
            }
            Array.from(enfant.attributes).forEach(attr => {
                const garde = enfant.tagName === 'A'
                    && attr.name.toLowerCase() === 'href'
                    && /^https?:\/\//i.test(attr.value);
                if (!garde) enfant.removeAttribute(attr.name);
            });
            if (enfant.tagName === 'A' && enfant.getAttribute('href')) {
                enfant.setAttribute('target', '_blank');
                enfant.setAttribute('rel', 'noopener');
            }
        });
    };
    nettoie(racine);
    return racine.innerHTML;
}

function renderLotPreview(lot, slug, ragName) {
    // La presentation est ce que le lot dit de lui-meme : on la rend telle
    // quelle, dans l'ordre choisi par son auteur.
    const presentation = (lot.presentation || []).map(l => `
        <dt>${escapeHtml(l.label || l.cle || '')}</dt>
        <dd>${sanitizeHtml(String(l.valeur ?? ''))}</dd>`).join('');
    // Colonnes donnees par le serveur : toutes les cles presentes dans les
    // entrees du lot, dans un ordre stable.
    const columns = (lot.columns && lot.columns.length)
        ? lot.columns
        : Object.keys((lot.entries || [])[0] || {});
    const head = columns.map(c => {
        const cls = lotColumnClass(c);
        return `<th${cls ? ` class="${cls}"` : ''}>${escapeHtml(lotColumnLabel(c))}</th>`;
    }).join('');
    // On rend TOUTES les entrees (checkboxes doivent couvrir tout le lot),
    // pas juste 200. Le tableau scroll verticalement.
    const rows = (lot.entries || []).map(e => {
        const cells = columns.map(c => {
            const cls = lotColumnClass(c);
            const raw = e[c];
            // Une ligne par document : les textes longs sont coupés à la
            // largeur de leur colonne et donnés en entier au survol. Sans ça
            // un descriptif ou un slug étire la ligne sur six hauteurs et le
            // tableau n'est plus parcourable.
            const isText = raw !== null && raw !== undefined && raw !== ''
                && typeof raw !== 'boolean' && !/^https?:\/\//i.test(String(raw));
            const title = isText ? ` title="${escapeHtml(String(raw))}"` : '';
            const inner = renderLotCell(c, raw);
            const body = isText ? `<span class="lot-cell-clip">${inner}</span>` : inner;
            return `<td${cls ? ` class="${cls}"` : ''}${title}>${body}</td>`;
        }).join('');
        return `
        <tr>
            <td class="lot-check-cell">
                <input type="checkbox" class="lot-entry-check" data-entry-id="${escapeHtml(e.identifiant || '')}" checked>
            </td>
            ${cells}
        </tr>`;
    }).join('');
    return `
        <div class="lot-preview-body" data-lot-slug="${escapeHtml(slug)}">
            ${presentation ? `<dl class="lot-presentation">${presentation}</dl>` : ''}
            <p class="muted small">
                <strong>${lot.count}</strong> documents · récupéré le
                <code>${escapeHtml((lot.fetched_at || '').slice(0, 10))}</code>
            </p>
            <div class="lot-preview-toolbar">
                <label class="checkbox-inline">
                    <input type="checkbox" class="lot-check-all" checked>
                    <span>Tout sélectionner</span>
                </label>
                <span class="lot-selection-count muted small">${lot.count} / ${lot.count} sélectionnés</span>
                <button type="button" class="btn primary" data-lot-download-selection="${escapeHtml(slug)}">
                    <i class="fa-solid fa-cloud-arrow-down"></i> Télécharger la sélection
                </button>
            </div>
            <div class="lot-table-wrap">
                <table class="lot-table">
                    <thead>
                        <tr>
                            <th class="lot-check-cell"></th>
                            ${head}
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>`;
}

function bindLotPreviewActions(panel, slug, ragName) {
    if (!panel) return;
    const total = panel.querySelectorAll('input.lot-entry-check').length;
    const countEl = panel.querySelector('.lot-selection-count');
    const dlBtn = panel.querySelector('[data-lot-download-selection]');
    const allCb = panel.querySelector('.lot-check-all');

    const refresh = () => {
        const checked = panel.querySelectorAll('input.lot-entry-check:checked').length;
        if (countEl) countEl.textContent = `${checked} / ${total} sélectionnés`;
        if (dlBtn) dlBtn.disabled = checked === 0;
        if (allCb) {
            allCb.checked = checked === total && total > 0;
            allCb.indeterminate = checked > 0 && checked < total;
        }
    };
    if (allCb) {
        allCb.addEventListener('change', () => {
            const want = allCb.checked;
            panel.querySelectorAll('input.lot-entry-check').forEach(cb => { cb.checked = want; });
            refresh();
        });
    }
    panel.addEventListener('change', event => {
        if (event.target && event.target.matches('input.lot-entry-check')) refresh();
    });
    if (dlBtn) {
        dlBtn.addEventListener('click', async () => {
            const checked = Array.from(panel.querySelectorAll('input.lot-entry-check:checked'));
            const entryIds = checked.map(cb => cb.dataset.entryId).filter(Boolean);
            if (!entryIds.length) return;
            if (!confirm(`Télécharger ${entryIds.length} PDF${plur(entryIds.length)} dans 1-Sources/ ?`)) return;
            await runLotDownload(slug, ragName, entryIds, dlBtn);
        });
    }
    refresh();
}

async function runLotDownload(slug, ragName, entryIds, btn) {
    // Utilise le bandeau de statut du lot en tete pour la barre de progression.
    const card = document.querySelector(`.lot-card[data-lot-slug="${CSS.escape(slug)}"]`);
    const statusEl = card ? card.querySelector('.lot-status') : null;
    setBtnLoading(btn, true);
    let pb = null;
    if (statusEl) {
        statusEl.classList.remove('hidden');
        pb = renderProgressBar(statusEl, `Démarrage du téléchargement (${entryIds ? entryIds.length : 'tout'})…`);
    }
    try {
        const resp = await fetch(`/api/corpus/lots/${encodeURIComponent(slug)}/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ corpus: ragName, entries: entryIds || undefined }),
        });
        const start = await resp.json();
        if (!start || !start.ok || !start.job_id) {
            if (pb) pb.setState('error', `✗ ${(start && start.error) || 'erreur inconnue'}`);
            return;
        }
        const finalJob = await pollJob(start.job_id, ({ processed, total, current, ratio }) => {
            if (pb) pb.update({ processed, total, current, ratio });
        });
        if (finalJob.state === 'done' && finalJob.result && finalJob.result.ok) {
            const r = finalJob.result;
            const listeErreurs = r.errors || [];
            const err = listeErreurs.length;
            // Un compteur seul n'apprend rien : sans le motif, personne ne
            // peut savoir si c'est le reseau, le site source ou un droit
            // d'ecriture. Le premier motif suffit a orienter.
            let detail = '';
            if (err) {
                listeErreurs.forEach(e => console.warn('Lot :', e.url, '→', e.error));
                detail = ` — ${listeErreurs[0].error}`;
                if (err > 1) detail += ` (et ${err - 1} autre${plur(err - 1)})`;
            }
            if (pb) pb.setState(err ? 'error' : 'done', `✓ ${r.downloaded} téléchargés, ${r.skipped} sautés, ${err} erreur${plur(err)} en ${r.duration_seconds}s.${detail}`);
            // Pas de message : les PDF apparaissent dans la liste, et le
            // schema du lot se lit a l'etape 3. Seul l'echec merite un mot.
            if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
            // Le serveur vient peut-etre de poser le schema du lot : sans
            // cette relecture, l'etape 3 continuerait d'afficher l'ancien,
            // et le premier enregistrement l'ecraserait.
            if (start.schema_applied && typeof loadRagSchema === 'function') loadRagSchema(ragName);
            // Telechargement abouti : on rend la main a la liste des
            // documents. Si des entrees ont echoue, le panneau reste ouvert
            // avec le detail.
            if (!err) fermerPanneauSources();
        } else {
            if (pb) pb.setState('error', `✗ ${escapeHtml((finalJob.result && finalJob.result.error) || finalJob.error || 'erreur inconnue')}`);
        }
    } finally {
        setBtnLoading(btn, false);
    }
}

/* ============ Schema RAG (champs de metadonnees par theme) ============ */

async function apiGetRagSchema(rag) {
    const resp = await fetch(`/api/corpus/schema?corpus=${encodeURIComponent(rag)}`);
    return await resp.json();
}

async function apiSaveRagSchema(rag, schema) {
    const resp = await fetch('/api/corpus/schema', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ corpus: rag, schema }),
    });
    return await resp.json();
}

// Etat local du schema pour eviter des re-fetch permanents.
let __schemaState = { rag: null, fields: [], defaults: [], locked: false, saving: false };

// Types de champ, alignes sur FIELD_TYPES dans app/rag_schema.py. Le type
// dit ce qu'est la valeur : il pilote la consigne donnée au LLM, la forme
// écrite dans le front matter du Markdown, et le filtre qu'un visualiseur
// peut proposer sur ce champ.
const SCHEMA_TYPES = [
    { value: 'text',    label: 'Texte' },
    { value: 'keyword', label: 'Mot-clé' },
    { value: 'enum',    label: 'Liste fermée' },
    { value: 'tags',    label: 'Tags' },
    { value: 'date',    label: 'Date' },
    { value: 'number',  label: 'Nombre' },
    { value: 'bool',    label: 'Oui / non' },
    { value: 'url',     label: 'Lien' },
];
const SCHEMA_TYPE_HELP = {
    text: "Texte libre. Filtre : recherche dans le texte.",
    keyword: "Valeur courte reprise telle quelle. Filtre : liste des valeurs présentes dans le corpus.",
    enum: "Vocabulaire fermé : le LLM doit choisir une des valeurs, sinon il laisse vide. Filtre : cases à cocher.",
    tags: "Plusieurs mots-clés. Reste une liste dans le front matter. Filtre : cases à cocher multiples.",
    date: "Date. YYYY-MM-DD, YYYY-MM ou YYYY. Filtre : période.",
    number: "Nombre. Filtre : intervalle.",
    bool: "Vrai ou faux. Filtre : oui / non.",
    url: "Lien http(s). Affiché comme un lien, pas comme un filtre.",
};

// Mêmes clés que `metamd_schema.NON_FILTRABLES` : un titre a une valeur par
// document, une URL est un lien. Ni l'un ni l'autre ne réduit une liste.
const NON_FILTRABLES = ['titre', 'source_url'];

function renderSchemaEditor(container, ragName) {
    const locked = !!__schemaState.locked;
    const dis = locked ? 'disabled' : '';
    const rdOnly = locked ? 'readonly' : '';
    // Les deux premières colonnes se dimensionnent sur leur contenu le plus
    // long (bornes : ni ridiculement étroit, ni envahissant). La colonne
    // Description prend le reste, plafonnée par CSS.
    const widthCh = (values, min, max) => {
        const longest = values.reduce((n, v) => Math.max(n, String(v || '').length), 0);
        return Math.min(max, Math.max(min, longest + 3));
    };
    const keyCh = widthCh(__schemaState.fields.map(f => f.key), 12, 32);
    const labelCh = widthCh(__schemaState.fields.map(f => f.label), 12, 32);

    const rowHtml = (f, idx) => {
        // `titre` nomme le document partout ensuite, `source_url` est posee
        // par le pipeline : ces deux lignes ne se discutent pas, description
        // comprise.
        const fige = !!f.fixed || (f.key || '').trim() === 'titre';
        const figeAttr = fige ? ' readonly disabled' : ` ${rdOnly}`;
        // Une ligne figee ne montre pas de cadenas : ses champs grises le
        // disent deja, et une icone de plus n'apprend rien.
        const actions = fige ? '' : `<button type="button" class="btn ghost" data-schema-up title="Monter" ${idx === 0 || locked ? 'disabled' : ''}><i class="fa-solid fa-arrow-up"></i></button>
               <button type="button" class="btn ghost" data-schema-down title="Descendre" ${idx === __schemaState.fields.length - 1 || locked ? 'disabled' : ''}><i class="fa-solid fa-arrow-down"></i></button>
               <button type="button" class="btn ghost schema-del" data-schema-del title="Supprimer" ${dis}><i class="fa-solid fa-trash"></i></button>`;
        const keyAttr = figeAttr;
        const ftype = f.type || 'text';
        const typeSelect = `<select data-field-type ${fige ? 'disabled' : dis} title="${escapeHtml(SCHEMA_TYPE_HELP[ftype] || '')}">
            ${SCHEMA_TYPES.map(t => `<option value="${t.value}"${t.value === ftype ? ' selected' : ''}>${escapeHtml(t.label)}</option>`).join('')}
        </select>`;
        // Le vocabulaire n'a de sens que pour `enum` : la ligne d'options
        // n'apparait que la, sous le sélecteur de type.
        // `options` est une liste une fois normalisée par le serveur, mais
        // une chaîne brute tant que l'utilisateur tape.
        const optionsValue = Array.isArray(f.options) ? f.options.join(', ') : (f.options || '');
        const optionsInput = ftype === 'enum'
            ? `<input type="text" class="schema-options" data-field-options
                      value="${escapeHtml(optionsValue)}"
                      placeholder="valeurs, séparées par des virgules" ${rdOnly}>`
            : '';
        // `titre` et `source_url` ne facettent rien : la case n'apparaît même
        // pas sur leur ligne, plutôt que d'être offerte puis refusée.
        const filtrable = !NON_FILTRABLES.includes((f.key || '').trim());
        const caseFiltre = filtrable
            ? `<input type="checkbox" data-field-filtre ${f.filtre ? 'checked' : ''} ${dis}
                      title="Proposer ce champ comme filtre">`
            : '';
        return `
        <tr data-schema-idx="${idx}"${fige ? ' class="schema-row-fixed"' : ''}>
            <td class="schema-col-filtre">${caseFiltre}</td>
            <td class="schema-key-cell"><input type="text" class="schema-key" data-field="key" value="${escapeHtml(f.key || '')}" placeholder="cle_technique"${keyAttr}></td>
            <td><input type="text" data-field="label" value="${escapeHtml(f.label || '')}" placeholder="Label"${figeAttr}></td>
            <td class="schema-type-cell">${typeSelect}${optionsInput}</td>
            <td><input type="text" data-field="description" value="${escapeHtml(f.description || '')}" placeholder="Aide pour l'auto-fill"${figeAttr}></td>
            <td class="schema-actions">${actions}</td>
        </tr>
    `;
    };

    const rows = __schemaState.fields.map((f, i) => rowHtml(f, i)).join('');

    const lockedBanner = '';

    const actionsBar = locked
        ? `<div class="schema-actions-bar">
                <button type="button" class="btn primary" data-schema-unlock><i class="fa-solid fa-lock-open"></i> Re-éditer</button>
            </div>`
        : `<div class="schema-actions-bar">
                <button type="button" class="btn primary" data-schema-lock title="Verrouiller le schéma et valider l'étape 1"><i class="fa-solid fa-check"></i> Valider et verrouiller</button>
                <button type="button" class="btn" data-schema-add><i class="fa-solid fa-plus"></i> Ajouter un champ</button>
                <button type="button" class="btn ghost" data-schema-reset title="Rétablir le schéma par défaut"><i class="fa-solid fa-rotate-left"></i> Réinitialiser</button>
            </div>`;

    container.innerHTML = `
        <div class="schema-editor ${locked ? 'is-locked' : ''}">
            <div class="schema-editor-head">
                <p class="muted small">Ces champs sont proposés pour chaque document déposé. Une fois le document converti, le bouton « Remplir les champs vides » de l'étape 3 demande à l'IA de déduire ceux qui sont encore vides.</p>
                <span class="schema-save-status muted small" data-schema-status>·</span>
            </div>
            ${lockedBanner}
            <div class="schema-frame">
            <table class="schema-table">
                <colgroup>
                    <col class="schema-col-filtre">
                    <col style="width:${keyCh}ch">
                    <col style="width:${labelCh}ch">
                    <col class="schema-col-type">
                    <col class="schema-col-desc">
                    <col class="schema-col-actions">
                </colgroup>
                <thead>
                    <tr>
                        <th class="schema-col-filtre" title="Champ proposé comme filtre" aria-label="Filtre"><i class="fa-solid fa-filter" aria-hidden="true"></i></th>
                        <th>Clé technique</th>
                        <th>Label</th>
                        <th>Type</th>
                        <th>Description (guide l'auto-fill)</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            </div>
            ${actionsBar}
        </div>
    `;

    bindSchemaEditor(container, ragName);
}

function bindSchemaEditor(container, ragName) {
    let saveTimer = null;
    const status = container.querySelector('[data-schema-status]');

    const scheduleSave = () => {
        if (status) {
            status.textContent = 'modifié…';
            status.className = 'schema-save-status muted small';
        }
        clearTimeout(saveTimer);
        saveTimer = setTimeout(async () => {
            if (status) status.textContent = 'enregistrement…';
            const result = await apiSaveRagSchema(ragName, {
                locked: __schemaState.locked,
                fields: __schemaState.fields,
            });
            if (result && result.ok) {
                __schemaState.fields = result.schema.fields || [];
                __schemaState.locked = !!result.schema.locked;
                if (status) {
                    status.textContent = '✓ enregistré';
                    status.className = 'schema-save-status small saved';
                }
            } else {
                if (status) {
                    status.textContent = '✗ erreur : ' + (result && result.error ? result.error : 'inconnue');
                    status.className = 'schema-save-status small error';
                }
            }
        }, 500);
    };

    const flushSave = async () => {
        // Persistance immediate (pour lock/unlock : on veut voir l'effet tout de suite,
        // et derriere on re-rend l'editeur).
        clearTimeout(saveTimer);
        const result = await apiSaveRagSchema(ragName, {
            locked: __schemaState.locked,
            fields: __schemaState.fields,
        });
        if (result && result.ok) {
            __schemaState.fields = result.schema.fields || [];
            __schemaState.locked = !!result.schema.locked;
        }
        return result;
    };

    container.querySelectorAll('tr[data-schema-idx]').forEach(tr => {
        const idx = Number(tr.dataset.schemaIdx);
        tr.querySelectorAll('[data-field]').forEach(el => {
            el.addEventListener('input', () => {
                const key = el.dataset.field;
                __schemaState.fields[idx][key] = el.value;
                scheduleSave();
            });
        });
        // Type : un changement fait apparaître ou disparaître le champ des
        // valeurs (propre à `enum`), d'où le re-rendu.
        const typeSel = tr.querySelector('[data-field-type]');
        if (typeSel) typeSel.addEventListener('change', () => {
            const field = __schemaState.fields[idx];
            field.type = typeSel.value;
            if (field.type !== 'enum') delete field.options;
            renderSchemaEditor(container, ragName);
            scheduleSave();
        });
        // Filtre : rien n'est déduit ici ni ailleurs, cette case est le seul
        // endroit qui pose la marque. Pas de re-rendu, la case porte déjà son
        // propre état.
        const caseFiltre = tr.querySelector('[data-field-filtre]');
        if (caseFiltre) caseFiltre.addEventListener('change', () => {
            const field = __schemaState.fields[idx];
            if (caseFiltre.checked) field.filtre = true;
            else delete field.filtre;
            scheduleSave();
        });
        const optionsInput = tr.querySelector('[data-field-options]');
        if (optionsInput) optionsInput.addEventListener('input', () => {
            // Le serveur re-normalise (trim, dédoublonnage) : on lui envoie
            // la saisie brute, sinon on couperait un mot en cours de frappe.
            __schemaState.fields[idx].options = optionsInput.value;
            scheduleSave();
        });
        const up = tr.querySelector('[data-schema-up]');
        const down = tr.querySelector('[data-schema-down]');
        const del = tr.querySelector('[data-schema-del]');
        if (up) up.addEventListener('click', () => {
            if (idx === 0) return;
            const f = __schemaState.fields;
            [f[idx - 1], f[idx]] = [f[idx], f[idx - 1]];
            renderSchemaEditor(container, ragName);
            scheduleSave();
        });
        if (down) down.addEventListener('click', () => {
            const f = __schemaState.fields;
            if (idx === f.length - 1) return;
            [f[idx], f[idx + 1]] = [f[idx + 1], f[idx]];
            renderSchemaEditor(container, ragName);
            scheduleSave();
        });
        if (del) del.addEventListener('click', () => {
            __schemaState.fields.splice(idx, 1);
            renderSchemaEditor(container, ragName);
            scheduleSave();
        });
    });

    const addBtn = container.querySelector('[data-schema-add]');
    if (addBtn) addBtn.addEventListener('click', () => {
        __schemaState.fields.push({ key: '', label: '', description: '', type: 'text' });
        renderSchemaEditor(container, ragName);
        // On ne sauve pas encore : la cle est vide, ca serait rejete cote serveur.
    });

    const lockBtn = container.querySelector('[data-schema-lock]');
    if (lockBtn) lockBtn.addEventListener('click', async () => {
        // Verifie que chaque champ a au moins une cle non vide.
        const emptyKeys = __schemaState.fields.some(f => !(f.key || '').trim());
        if (emptyKeys) {
            showMessage('error', 'Schéma invalide', 'Au moins un champ n\'a pas de clé technique. Renseignez ou supprimez ces champs avant de verrouiller.', { step: 'schema' });
            return;
        }
        setBtnLoading(lockBtn, true);
        __schemaState.locked = true;
        const result = await flushSave();
        setBtnLoading(lockBtn, false);
        if (result && result.ok) {
            // Pas de message : le schema passe en lecture seule et le suivi
            // des etapes annonce la conversion. Seul l'echec merite un mot.
            renderSchemaEditor(container, ragName);
            if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
        } else {
            __schemaState.locked = false;
            showMessage('error', 'Erreur', (result && result.error) || 'Verrouillage impossible.', { step: 'schema' });
        }
    });

    const unlockBtn = container.querySelector('[data-schema-unlock]');
    if (unlockBtn) unlockBtn.addEventListener('click', async () => {
        if (!confirm('Ré-ouvrir le schéma à l\'édition ? L\'étape 1 repassera à faire tant que le schéma ne sera pas re-verrouillé.')) return;
        setBtnLoading(unlockBtn, true);
        __schemaState.locked = false;
        const result = await flushSave();
        setBtnLoading(unlockBtn, false);
        if (result && result.ok) {
            renderSchemaEditor(container, ragName);
            if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
        } else {
            __schemaState.locked = true;
            showMessage('error', 'Erreur', (result && result.error) || 'Déverrouillage impossible.', { step: 'schema' });
        }
    });


    const resetBtn = container.querySelector('[data-schema-reset]');
    if (resetBtn) resetBtn.addEventListener('click', async () => {
        if (!confirm('Rétablir le schéma par défaut ? Les champs personnalisés seront perdus.')) return;
        __schemaState.fields = JSON.parse(JSON.stringify(__schemaState.defaults));
        await apiSaveRagSchema(ragName, { fields: __schemaState.fields });
        renderSchemaEditor(container, ragName);
    });
}

async function loadRagSchema(ragName) {
    __schemaState.rag = ragName;
    const container = document.getElementById('schema-editor');
    if (!container) return;
    const result = await apiGetRagSchema(ragName);
    if (!result || !result.ok) {
        container.innerHTML = `<p class="muted small">Impossible de charger le schéma.</p>`;
        return;
    }
    __schemaState.fields = (result.schema && result.schema.fields) || [];
    __schemaState.locked = !!(result.schema && result.schema.locked);
    __schemaState.defaults = result.default_fields || [];
    renderSchemaEditor(container, ragName);
}

function computeNextStep(topic) {
    const schemaLocked = !!(topic.schema && topic.schema.locked);
    const hasSources = topic.sources && topic.sources.count > 0;
    const convert = (topic.steps && topic.steps.convert) || {};
    const fill = (topic.steps && topic.steps.fill) || {};
    const nActive = convert.docs_with_active || 0;
    const nSources = topic.sources ? topic.sources.count : 0;
    const hasAnyConversion = nActive > 0;
    const allConverted = nSources > 0 && nActive >= nSources;
    const validation = topic.validation || { total: 0, md_valid: 0, metadata_valid: 0, both_valid: 0, invalid: 0 };
    if (!hasSources) {
        return {
            step: 'extraction',
            title: 'Source',
            text: `Le dossier <code>1-Sources/</code> est vide. Ouvrez « Ajouter des documents » pour choisir un lot prédéfini (téléchargement automatique) ou déposer vos PDFs à la main.`,
        };
    }
    // Le schéma passe avant la conversion : la conversion Mistral lit ses
    // champs dans le PDF au passage, elle a donc besoin de le connaître.
    if (!schemaLocked) {
        return {
            step: 'schema',
            title: 'Arrêter le schéma',
            text: `Décidez des champs que porteront les documents de ce corpus, puis cliquez sur <strong>Valider et verrouiller</strong> pour ouvrir la conversion. La conversion lira ces champs dans les PDF au passage : soignez leur description, c'est elle qui est envoyée au modèle.`,
        };
    }
    if (!hasAnyConversion) {
        return {
            step: 'convert',
            title: 'Conversion doc par doc',
            text: `Aucune conversion active. Ouvrez la page « Conversions » pour convertir les ${nSources} document${plur(nSources)}, avec le moteur de votre choix.`,
        };
    }
    if (!allConverted) {
        return {
            step: 'convert',
            title: 'Convertir les documents restants',
            text: `<strong>${nActive}/${nSources}</strong> document${plur(nActive)} ont une conversion active. Convertissez les autres depuis la page « Conversions ».`,
        };
    }
    const aRemplir = fill.docs_incomplete || 0;
    if (aRemplir > 0) {
        const attente = fill.pending_fields || fill.empty_fields || 0;
        return {
            step: 'convert',
            title: 'Remplir les champs vides',
            text: `<strong>${attente}</strong> champ${plur(attente)} du schéma ${attente > 1 ? 'sont restés vides' : 'est resté vide'} sur ${aRemplir} document${plur(aRemplir)} : ${attente > 1 ? 'ils ont' : 'il a'} été ajouté${plur(attente)} au schéma après leur conversion. Le bouton « Remplir les champs vides », à l'étape 3, ${attente > 1 ? 'les' : 'le'} déduit du Markdown sans toucher aux valeurs déjà posées.`,
        };
    }
    const bothValid = validation.both_valid || 0;
    const total = validation.total || 0;
    if (bothValid < total) {
        return {
            step: 'revision',
            title: 'Relire et valider',
            text: `<strong>${bothValid}/${total}</strong> document${plur(bothValid)} validé${plur(bothValid)}. Relisez les autres dans le viewer : cochez le Markdown quand l'extraction est fidèle, les métadonnées quand les valeurs sont justes.`,
        };
    }
    return {
        step: 'markdown',
        title: 'Corpus prêt',
        text: `Les ${total} document${plur(total)} sont convertis, remplis et validés. Leurs Markdown, dans <code>2-Conversions/&lt;moteur&gt;/</code>, portent toutes leurs métadonnées en front matter : ils sont prêts à être repris par MD-RAG ou par un lecteur Markdown.`,
    };
}

function updatePipelineFlow(topic, nextStep) {
    const flow = document.getElementById('pipeline-flow');
    if (!flow) return;
    const hasSources = !!(topic.sources && topic.sources.count > 0);
    const schemaLocked = !!(topic.schema && topic.schema.locked);
    const convert = (topic.steps && topic.steps.convert) || {};
    const nActive = convert.docs_with_active || 0;
    const nSources = topic.sources ? topic.sources.count : 0;
    const allConverted = nSources > 0 && nActive >= nSources;
    const validation = topic.validation || {};
    const bothValid = validation.both_valid || 0;
    const fill = (topic.steps && topic.steps.fill) || {};
    const aRemplir = fill.docs_incomplete || 0;
    // Le remplissage n'est plus une etape : la conversion lit deja les champs
    // du schema dans le PDF. Il reste une action, proposee dans l'etape 3
    // quand il subsiste des trous, et tant qu'il en reste la suite attend.
    const map = {
        extraction: hasSources,
        schema: schemaLocked,
        convert: allConverted && aRemplir === 0,
        revision: nActive > 0 && bothValid >= nActive,
        markdown: allConverted && nActive > 0 && bothValid >= nActive,
    };
    // Le schéma n'attend plus les conversions, ce sont elles qui l'attendent :
    // la conversion Mistral lit ses champs dans le PDF pendant le passage.
    const gate = {
        extraction: true,
        schema: hasSources,
        convert: hasSources && schemaLocked,
        // Relire des metadonnees qu'on n'a pas fini de remplir reviendrait a
        // les relire deux fois : la validation attend qu'il ne reste rien.
        revision: nActive > 0 && aRemplir === 0,
        // Le telechargement, lui, n'attend rien : emporter un Markdown non
        // relu reste possible, un avertissement le signale.
        markdown: nActive > 0,
    };
    const computeStepState = (key) => {
        const done = map[key];
        if (done) return 'done';
        if (nextStep.step === key) return 'next';
        if (!gate[key]) return 'locked';
        return 'todo';
    };
    flow.querySelectorAll('li[data-flow-step]').forEach(li => {
        const key = li.dataset.flowStep;
        const state = computeStepState(key);
        const statusEl = li.querySelector('.pipeline-substatus') || li.querySelector('.pipeline-status');
        let statusText = '';
        let tooltip = li.querySelector('.pipeline-label') ? li.querySelector('.pipeline-label').textContent : '';
        if (key === 'convert' && aRemplir > 0) {
            statusText = `${aRemplir} à remplir`;
        } else if (key === 'convert') {
            statusText = `${nActive}/${nSources}`;
        } else if ((key === 'revision' || key === 'markdown') && nActive > 0) {
            statusText = `${bothValid}/${nActive}`;
        } else if (state === 'done') {
            statusText = 'ok';
        } else if (state === 'next') {
            statusText = 'à faire';
        } else if (state === 'locked') {
            statusText = 'verrouillée';
        } else {
            statusText = '·';
        }
        li.dataset.state = state;
        li.setAttribute('title', tooltip);
        if (statusEl) statusEl.textContent = statusText;
    });
    document.querySelectorAll('.step-num[data-step]').forEach(el => {
        const key = el.dataset.step;
        el.dataset.state = computeStepState(key);
    });
}

/** Vrai si le fichier commence par la signature `%PDF-`. */
async function isRealPdf(file) {
    if (!file || !file.size) return false;
    try {
        const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
        return head.length === 5
            && head[0] === 0x25 && head[1] === 0x50   // %P
            && head[2] === 0x44 && head[3] === 0x46   // DF
            && head[4] === 0x2d;                      // -
    } catch (_) {
        return false;
    }
}

/**
 * Dépôt de PDF depuis le navigateur, onglet « Lot personnalisé ».
 *
 * Les fichiers choisis (glisser-déposer ou sélecteur) forment une file
 * d'attente affichée en clair : une ligne par PDF, avec son URL d'origine
 * saisissable sur place. Pas de modale : on voit les fichiers et leurs URL
 * d'un seul bloc, et rien ne part tant qu'on n'a pas cliqué sur « Ajouter ».
 *
 * Les fichiers partent en base64 dans un corps JSON : le serveur est un
 * handler de la bibliothèque standard, sans parseur multipart pérenne.
 */
function bindCustomSourcesUpload(ragName, backToRag) {
    const input = document.getElementById('custom-sources-input');
    const btn = document.getElementById('btn-upload-sources');
    const status = document.getElementById('custom-upload-status');
    const drop = document.getElementById('sources-drop');
    const pick = document.getElementById('btn-pick-sources');
    const listEl = document.getElementById('custom-sources-list');
    if (!input || !btn) return;

    // File d'attente : {file, url}. Les refusés sont gardés à part pour
    // qu'on sache ce qui n'est pas parti, plutôt que de les faire
    // disparaître en silence.
    const pending = [];
    let refused = [];

    // Tous les retours s'affichent ici, dans l'étape : le bandeau global est
    // en haut de page, donc hors écran quand on agit depuis l'étape 1.
    const out = document.getElementById('custom-upload-result');
    const report = (state, html) => {
        if (out) out.innerHTML = `<div class="upload-report ${state}">${html}</div>`;
    };

    /**
     * Verrouille « Ajouter » tant qu'un titre manque.
     *
     * Appelé aussi à chaque frappe : la liste n'est pas reconstruite pendant
     * la saisie (ça ferait perdre le curseur), c'est donc ici que l'état du
     * bouton et le marquage des champs vides se mettent à jour.
     */
    const updateReady = () => {
        const missing = pending.filter(p => !(p.title || '').trim()).length;
        btn.disabled = pending.length === 0 || missing > 0;
        if (listEl) {
            listEl.querySelectorAll('.custom-source-title').forEach(inp => {
                inp.classList.toggle('is-missing', !inp.value.trim());
            });
        }
        if (!status) return;
        if (!pending.length) status.textContent = '';
        else if (missing) status.textContent = `Titre obligatoire : ${missing} à compléter.`;
        else status.textContent = `${pending.length} PDF prêt${plur(pending.length)} à ajouter.`;
        status.classList.toggle('error', missing > 0);
    };

    const render = () => {
        updateReady();
        if (!listEl) return;
        if (!pending.length && !refused.length) {
            listEl.innerHTML = '';
            return;
        }
        const rows = pending.map((item, i) => `
            <li class="custom-source-row" data-idx="${i}">
                <span class="custom-source-name" title="${escapeHtml(item.file.name)}">
                    <i class="fa-solid fa-file-pdf" aria-hidden="true"></i> ${escapeHtml(item.file.name)}
                </span>
                <span class="muted small custom-source-size">${humanBytes(item.file.size)}</span>
                <button type="button" class="btn ghost custom-source-del" data-idx="${i}" title="Retirer de la liste">
                    <i class="fa-solid fa-xmark"></i>
                </button>
                <div class="custom-source-fields">
                    <label class="custom-source-field">
                        <span class="muted small">Titre <span class="req" title="Champ obligatoire">*</span></span>
                        <input type="text" class="custom-source-title" data-idx="${i}" required
                               value="${escapeHtml(item.title || '')}" placeholder="Titre du document">
                    </label>
                    <label class="custom-source-field">
                        <span class="muted small">URL d'origine</span>
                        <input type="text" class="custom-source-url" data-idx="${i}"
                               value="${escapeHtml(item.url || '')}" placeholder="https://… (facultative)">
                    </label>
                </div>
            </li>`).join('');
        const refusedHtml = refused.length
            ? `<p class="custom-sources-refused muted small"><i class="fa-solid fa-triangle-exclamation"></i>
                 ${refused.length} fichier${plur(refused.length)} écarté${plur(refused.length)} :
                 ${refused.map(r => `<code>${escapeHtml(r.name)}</code> (${escapeHtml(r.reason)})`).join(', ')}</p>`
            : '';
        listEl.innerHTML = (pending.length
            ? `<p class="muted small">Le titre est obligatoire : il part dans les métadonnées du document et sert à le citer, il est repris tel quel sans être écrasé par le remplissage automatique. L'URL d'origine est facultative : elle voyage avec les métadonnées du document, pour pouvoir remonter à la source. Les deux restent modifiables plus tard.</p>
               <ul class="custom-sources-items">${rows}</ul>`
            : '') + refusedHtml;
        updateReady();
    };

    /** Ajoute des fichiers à la file, en écartant ce qui n'est pas un PDF. */
    const addFiles = async (files) => {
        refused = [];
        for (const file of files) {
            if (!/\.pdf$/i.test(file.name)) {
                refused.push({ name: file.name, reason: 'pas un fichier .pdf' });
                continue;
            }
            if (!await isRealPdf(file)) {
                refused.push({ name: file.name, reason: "le contenu n'est pas un PDF" });
                continue;
            }
            // Même nom et même taille : c'est le même dépôt, on ne l'empile
            // pas deux fois.
            const dup = pending.some(p => p.file.name === file.name && p.file.size === file.size);
            // Titre par défaut : le nom du fichier sans son extension, à
            // corriger sur place. Un titre vaut mieux qu'un champ vide, et
            // c'est la seule chose qu'on sache du document avant conversion.
            if (!dup) pending.push({ file, title: file.name.replace(/\.pdf$/i, ''), url: '' });
        }
        render();
    };

    if (pick) pick.addEventListener('click', () => input.click());
    if (drop) {
        drop.addEventListener('click', event => {
            // Le bouton a déjà son propre handler : sans ça, un clic dessus
            // ouvrirait deux fois le sélecteur.
            if (event.target.closest('button')) return;
            input.click();
        });
    }
    input.addEventListener('change', () => {
        addFiles(Array.from(input.files || []));
        input.value = '';  // permet de re-choisir le même fichier ensuite
    });

    // Glisser-déposer sur la zone. Le `dragover` doit être annulé, sinon le
    // navigateur refuse le drop. Les handlers posés sur la page entière
    // évitent qu'un fichier lâché à côté de la zone soit ouvert dans
    // l'onglet, ce qui ferait perdre la file d'attente.
    if (drop) {
        const stop = event => { event.preventDefault(); event.stopPropagation(); };
        ['dragenter', 'dragover'].forEach(type => {
            drop.addEventListener(type, event => { stop(event); drop.classList.add('is-over'); });
        });
        ['dragleave', 'dragend'].forEach(type => {
            drop.addEventListener(type, event => { stop(event); drop.classList.remove('is-over'); });
        });
        drop.addEventListener('drop', event => {
            stop(event);
            drop.classList.remove('is-over');
            const files = event.dataTransfer ? Array.from(event.dataTransfer.files || []) : [];
            if (files.length) addFiles(files);
        });
        ['dragover', 'drop'].forEach(type => {
            document.addEventListener(type, event => {
                if (!drop.contains(event.target)) event.preventDefault();
            });
        });
    }

    // Saisie des URL et retrait d'une ligne : délégation, la liste est
    // reconstruite à chaque changement.
    if (listEl) {
        listEl.addEventListener('input', event => {
            const field = event.target.closest('.custom-source-url, .custom-source-title');
            if (!field) return;
            const item = pending[Number(field.dataset.idx)];
            if (!item) return;
            if (field.classList.contains('custom-source-title')) {
                item.title = field.value;
                updateReady();
            } else {
                item.url = field.value;
            }
        });
        listEl.addEventListener('click', event => {
            const del = event.target.closest('.custom-source-del');
            if (!del) return;
            pending.splice(Number(del.dataset.idx), 1);
            render();
        });
    }

    render();

    btn.addEventListener('click', async () => {
        if (!pending.length) return;
        setBtnLoading(btn, true);
        if (out) out.innerHTML = '';
        if (status) status.textContent = 'Lecture des fichiers…';
        try {
            const payload = [];
            for (const item of pending) {
                // Le serveur ignore ce qui n'est pas http(s) : autant ne rien
                // envoyer plutôt que de laisser croire que c'est enregistré.
                const url = (item.url || '').trim();
                payload.push({
                    name: item.file.name,
                    data_b64: await fileToBase64(item.file),
                    title: (item.title || '').trim(),
                    source_url: /^https?:\/\//i.test(url) ? url : '',
                });
            }
            if (status) status.textContent = `Envoi de ${payload.length} fichier${plur(payload.length)}…`;
            const resp = await fetch('/api/corpus/sources/upload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ corpus: ragName, files: payload }),
            });
            const data = await readJsonOrExplain(resp, '/api/corpus/sources/upload');
            if (status) status.textContent = '';
            if (data.__error) {
                report('is-error', `<p><i class="fa-solid fa-circle-xmark"></i> ${data.__error}</p>`);
                return;
            }
            const added = (data.saved || []).length;
            const repl = (data.replaced || []).length;
            const errs = data.errors || [];
            const parts = [];
            if (added) parts.push(`<strong>${added}</strong> fichier${plur(added)} ajouté${plur(added)}`);
            if (repl) parts.push(`<strong>${repl}</strong> remplacé${plur(repl)}`);
            const causes = errs.length
                ? `<p class="upload-report-causes-title">Refusé${plur(errs.length)} :</p>
                   <ul class="upload-report-causes">${errs.map(e => `
                       <li><span class="upload-report-doc"><code>${escapeHtml(e.name || '?')}</code></span>
                       <span class="upload-report-msg">${escapeHtml(e.error || '')}</span></li>`).join('')}</ul>`
                : '';
            const state = !added && !repl ? 'is-error' : (errs.length ? 'is-partial' : 'is-ok');
            const icon = state === 'is-ok' ? 'fa-circle-check'
                : (state === 'is-partial' ? 'fa-triangle-exclamation' : 'fa-circle-xmark');
            const head = parts.length ? parts.join(', ') + '.' : 'Aucun fichier ajouté.';
            report(state, `<p><i class="fa-solid ${icon}"></i> ${head}</p>${causes}`);

            // Ce qui est parti quitte la file ; ce que le serveur a refusé y
            // reste, pour pouvoir corriger et relancer.
            const done = new Set([...(data.saved || []), ...(data.replaced || [])]);
            for (let i = pending.length - 1; i >= 0; i--) {
                if (done.has(pending[i].file.name)) pending.splice(i, 1);
            }
            refused = [];
            render();
            if (added || repl) {
                if (typeof window.__reloadThemeState === 'function') await window.__reloadThemeState();
            }
            // Dépôt entièrement réussi : on referme le panneau, la liste des
            // documents juste en dessous montre le résultat. En cas d'erreur,
            // on ne bouge pas : le compte rendu doit rester lisible et la
            // file d'attente corrigeable.
            if (state === 'is-ok') {
                fermerPanneauSources();
                if (backToRag) backToRag();
                return;
            }
        } catch (e) {
            if (status) status.textContent = '';
            report('is-error', `<p><i class="fa-solid fa-circle-xmark"></i> ${escapeHtml(String(e))}</p>`);
        } finally {
            setBtnLoading(btn, false);
        }
    });
}

/**
 * Lit une réponse JSON, ou explique pourquoi ce n'en est pas.
 *
 * Un 404 du serveur renvoie une page HTML : `resp.json()` échoue alors sur
 * « Unexpected token '<' », message qui ne dit rien de la cause réelle.
 * Retourne `{__error: "…"}` quand la lecture échoue.
 */
async function readJsonOrExplain(resp, route) {
    const text = await resp.text();
    try {
        return JSON.parse(text);
    } catch (_) {
        if (resp.status === 404) {
            return { __error: `La route <code>${escapeHtml(route)}</code> est inconnue du serveur (HTTP 404). `
                + `Cette fonction est récente : redémarrez le serveur local pour qu'il la prenne en compte.` };
        }
        return { __error: `Réponse inattendue du serveur (HTTP ${resp.status}) : `
            + escapeHtml(text.slice(0, 120)) + '…' };
    }
}

/** Lit un File en base64 (sans le préfixe `data:…;base64,`). */
function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error(`Lecture impossible : ${file.name}`));
        reader.onload = () => {
            const s = String(reader.result || '');
            const comma = s.indexOf(',');
            resolve(comma === -1 ? s : s.slice(comma + 1));
        };
        reader.readAsDataURL(file);
    });
}

/**
 * Bloc « Ajouter des documents » de l'etape 1, replie par defaut.
 *
 * Il vit dans la page du corpus : la liste des documents est juste en
 * dessous, on voit donc arriver ce qu'on ajoute sans changer de page. Les
 * lots ne sont charges qu'au premier depliage, pas a chaque affichage du
 * corpus.
 */
/**
 * Referme le panneau « Ajouter des documents ».
 *
 * Appele quand un ajout a abouti : les documents sont desormais dans la
 * liste juste en dessous, le panneau n'a plus rien a montrer et masquerait
 * le resultat. En cas d'erreur on ne le ferme pas, le compte rendu doit
 * rester lisible.
 */
function fermerPanneauSources() {
    const panneau = document.getElementById('add-sources-panel');
    const bouton = document.getElementById('btn-open-sources');
    if (!panneau || panneau.classList.contains('hidden')) return;
    panneau.classList.add('hidden');
    if (bouton) bouton.setAttribute('aria-expanded', 'false');
}

function initAddSourcesPanel(rag) {
    const panneau = document.getElementById('add-sources-panel');
    const bouton = document.getElementById('btn-open-sources');
    if (!panneau || !bouton) return;

    // Le chemin est connu : on l'ecrit en toutes lettres plutot que de
    // laisser l'utilisateur remplacer un `<corpus>` par le nom du sien.
    const chemin = document.getElementById('sources-path');
    if (chemin) chemin.textContent = `data/CORPUS/${rag}/1-Sources/`;

    // Cette fonction est rappelee a chaque rafraichissement de l'etat du
    // corpus, donc apres chaque ajout. Sans ce garde-fou, un deuxieme
    // ecouteur s'ajoutait au bouton : le clic basculait deux fois et le
    // panneau ne se refermait plus.
    if (bouton.dataset.lie) return;
    bouton.dataset.lie = '1';

    let monte = false;
    bouton.addEventListener('click', () => {
        const ouvert = !panneau.classList.toggle('hidden');
        bouton.setAttribute('aria-expanded', String(ouvert));
        if (ouvert && !monte) {
            monte = true;
            bindCustomSourcesUpload(rag, () => {
                if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
            });
            initExtractionBlock(rag);
        }
    });
}


/**
 * Liste des Markdown produits, sous l'etape 5.
 *
 * Un document converti donne un `.md` par moteur essaye, mais un seul fait
 * reference : c'est celui-la qu'on montre, avec son moteur et sa date. Les
 * documents encore sans conversion apparaissent aussi, pour qu'on voie ce
 * qui reste a faire.
 */
async function renderMdInventory(ragName) {
    const liste = document.getElementById('md-inventory-list');
    const compte = document.getElementById('md-inventory-count');
    if (!liste) return;
    let docs = [];
    try {
        // `orphans=1` : l'étape 5 liste ce qui est téléchargeable, donc les
        // Markdown dont la source a disparu de `1-Sources/` aussi. Les autres
        // pages s'en passent — elles travaillent sur des sources.
        const resp = await fetch(`/api/corpus/documents?corpus=${encodeURIComponent(ragName)}&orphans=1`);
        const data = await resp.json();
        docs = (data && data.documents) || [];
    } catch (e) {
        liste.innerHTML = `<p class="muted small">Liste indisponible : ${escapeHtml(String(e))}</p>`;
        return;
    }
    const convertis = docs.filter(d => d.active_id);
    if (compte) {
        compte.textContent = docs.length
            ? `${convertis.length}/${docs.length} document${plur(docs.length)} converti${plur(convertis.length)}`
            : 'aucun document';
    }
    if (!docs.length) {
        liste.innerHTML = `<p class="muted small">Aucun document dans <code>1-Sources/</code>.</p>`;
        return;
    }
    liste.innerHTML = `<ul class="sources-inv-list">${docs.map(d => {
        const active = (d.conversions || []).find(c => c.id === d.active_id);
        const titre = (d.title || '').trim() || d.stem;
        const etat = active
            ? `<span class="muted small md-inv-engine">${escapeHtml(ENGINE_LABELS[active.engine] || active.engine)}</span>
               <span class="muted small md-inv-date">${escapeHtml(active.ts || '')}</span>
               <span class="muted small md-inv-size">${humanBytes(active.size)}</span>`
            : `<span class="muted small md-inv-todo md-inv-engine">pas de conversion</span>
               <span class="md-inv-date"></span><span class="md-inv-size"></span>`;
        // Le Markdown s'ouvre sous sa ligne : on reste dans la liste, et on
        // peut comparer deux documents sans jongler avec des onglets.
        const lien = active && d.active_md_url
            ? `<button type="button" class="source-inv-url is-set md-inv-open" data-md-url="${escapeHtml(d.active_md_url)}"
                       aria-expanded="false" title="Voir le Markdown"><i class="fa-solid fa-file-lines"></i></button>`
            : `<span class="source-inv-url" title="Aucun Markdown actif"><i class="fa-solid fa-file-circle-xmark"></i></span>`;
        return `
            <li class="source-inv-row md-inv-row">
                <div class="source-inv-main">
                    <input type="checkbox" class="md-inv-check" data-stem="${escapeHtml(d.stem)}"
                           ${active ? 'checked' : 'disabled'} title="${active ? 'Inclure dans le ZIP' : 'Pas de Markdown a telecharger'}">
                    <span class="md-inv-doc">
                        <span class="md-inv-title">${escapeHtml(titre)}</span>
                        <span class="md-inv-file"><i class="fa-solid fa-file-lines" aria-hidden="true"></i> ${escapeHtml(d.stem)}.md${
                            // Le Markdown part quand même : on dit seulement
                            // que l'archive n'aura pas la pièce d'origine.
                            d.sans_source ? ' <span class="muted">— sans document d\'origine</span>' : ''
                        }</span>
                    </span>
                    ${etat}
                    ${lien}
                </div>
                <div class="md-inv-preview hidden"></div>
            </li>`;
    }).join('')}</ul>`;
    majMdZip();
}

/**
 * Ouvre ou referme l'aperçu du Markdown sous sa ligne.
 *
 * Le contenu est chargé au premier dépliage et gardé ensuite : rouvrir la
 * même ligne ne redemande rien au serveur. Le texte est montré tel quel,
 * front matter compris — c'est le fichier produit qu'on vient vérifier, pas
 * son rendu.
 */
async function basculerApercuMd(bouton) {
    const ligne = bouton.closest('.md-inv-row');
    const zone = ligne && ligne.querySelector('.md-inv-preview');
    if (!zone) return;
    const ouvert = zone.classList.toggle('hidden');
    bouton.setAttribute('aria-expanded', String(!ouvert));
    bouton.classList.toggle('is-open', !ouvert);
    if (ouvert || zone.dataset.charge) return;
    zone.innerHTML = '<p class="muted small"><span class="spinner"></span>Chargement…</p>';
    try {
        const resp = await fetch(bouton.dataset.mdUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const texte = await resp.text();
        zone.innerHTML = `<pre class="md-inv-code">${escapeHtml(texte)}</pre>`;
        zone.dataset.charge = '1';
    } catch (e) {
        zone.innerHTML = `<p class="muted small error">Lecture impossible : ${escapeHtml(String(e))}</p>`;
    }
}

/** Etat du bouton ZIP : il suit le nombre de Markdown coches. */
function majMdZip() {
    const btn = document.getElementById('btn-md-zip');
    if (!btn) return;
    const cases = Array.from(document.querySelectorAll('.md-inv-check:not(:disabled)'));
    const n = cases.filter(cb => cb.checked).length;
    const toutes = document.getElementById('md-inv-all');
    if (toutes) {
        toutes.checked = cases.length > 0 && n === cases.length;
        toutes.indeterminate = n > 0 && n < cases.length;
        toutes.disabled = cases.length === 0;
    }
    // Le ZIP n'attend plus la validation : un avertissement au-dessus de la
    // liste dit si tout n'a pas été relu, et l'on décide.
    btn.disabled = n === 0;
    btn.title = 'Le Markdown et son PDF pour chaque document coché, sous le même nom.';
    btn.innerHTML = `<i class="fa-solid fa-file-zipper"></i> Télécharger le ZIP${n ? ` (${n})` : ''}`;
}

/**
 * Telechargement des documents coches, en une archive.
 *
 * Chaque document y figure deux fois : sa conversion active et le PDF dont
 * elle vient, sous le meme nom. Le serveur assemble le ZIP : lui seul sait
 * ou vivent ces deux fichiers, et le navigateur n'a pas acces au disque.
 */
async function telechargerMdZip(ragName, btn) {
    const stems = Array.from(document.querySelectorAll('.md-inv-check:checked'))
        .map(cb => cb.dataset.stem);
    if (!stems.length) return;
    setBtnLoading(btn, true);
    try {
        const resp = await fetch('/api/corpus/md/zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ corpus: ragName, stems }),
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            showMessage('error', 'Archive impossible', data.error || `HTTP ${resp.status}`);
            return;
        }
        const url = URL.createObjectURL(await resp.blob());
        const lien = document.createElement('a');
        lien.href = url;
        lien.download = `${ragName}-documents.zip`;
        lien.click();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
        showMessage('error', 'Erreur réseau', String(e));
    } finally {
        setBtnLoading(btn, false);
        majMdZip();
    }
}

/** Etat d'un corpus : sources, schema, conversions. */
/**
 * Ouvre ou ferme le bouton d'une étape.
 *
 * Un lien fermé reste visible et gardé : il dit qu'il existe, pourquoi il
 * n'est pas encore accessible, et ne mène nulle part tant qu'il l'est. Le
 * `href` est neutralisé pour que le clavier et le clic milieu suivent la
 * même règle que le clic.
 */
function marquerEtapeFermee(lien, fermee, raison) {
    if (!lien) return;
    if (fermee && !lien.dataset.href) lien.dataset.href = lien.getAttribute('href') || '';
    lien.classList.toggle('is-disabled', fermee);
    lien.setAttribute('aria-disabled', String(fermee));
    lien.title = fermee ? (raison || '') : '';
    if (fermee) {
        lien.setAttribute('href', '#');
    } else if (lien.dataset.href !== undefined) {
        delete lien.dataset.href;
    }
}

// Dépôt du projet, pour le bloc « forge » du bas de rail.
const DEPOT_APP = 'https://github.com/Othman-Benbrahim/meta-md';

// Le bloc « forge » du bas de rail, dans la presentation d'EduMD : un libelle
// en petites capitales, puis trois liens gris clair. Ils s'ouvrent dans un
// onglet a part — on ne quitte pas son travail pour lire une licence.
const LIENS_FORGE = [
    { icone: 'fa-code-pull-request', url: DEPOT_APP, titre: 'Code source du projet' },
    { icone: 'fa-tag', url: DEPOT_APP + '/-/issues/new', titre: 'Ouvrir un ticket' },
    { icone: 'fa-scale-balanced', url: 'https://www.gnu.org/licenses/agpl-3.0.html',
      titre: 'Licence GNU AGPL v3' },
];

/** Pose le bloc « forge » en bas de la colonne de gauche. */
function monterBlocForge() {
    const rail = document.getElementById('app-header');
    if (!rail || rail.dataset.forge) return;
    rail.dataset.forge = '1';
    const bloc = document.createElement('div');
    bloc.className = 'rail-project-block';
    bloc.innerHTML =
        '<p class="rail-forge-label">FORGE DES COMMUNS NUMÉRIQUES ÉDUCATIFS</p>'
        + '<p class="rail-project-links">'
        + LIENS_FORGE.map(l =>
            `<a class="rail-project-icon" href="${l.url}"
                target="_blank" rel="noopener noreferrer"
                title="${escapeHtml(l.titre)}" aria-label="${escapeHtml(l.titre)}">
                <i class="fa-solid ${l.icone}" aria-hidden="true"></i>
             </a>`).join('')
        + '</p>';
    rail.appendChild(bloc);
}

/**
 * Complète la colonne de gauche : ce que fait META-MD, puis ses accès.
 *
 * Montée en JavaScript plutôt que recopiée dans chaque page : elle est la
 * même partout, et six copies divergeraient à la première retouche.
 */
function monterRail() {
    const rail = document.getElementById('app-header');
    if (!rail || rail.dataset.monte) return;
    rail.dataset.monte = '1';

    const presentation = document.createElement('p');
    presentation.className = 'rail-presentation';
    // Les deux noms sont poses en logotype. Leur `alt` porte le nom : si
    // l'image manque, la phrase se lit encore, et un lecteur d'ecran l'enonce
    // comme un mot.
    const logo = (fichier, nom) =>
        `<img class="rail-logo" src="assets/${fichier}" alt="${nom}">`;
    presentation.innerHTML = logo('meta-md.png', 'META-MD')
        + " produit des Markdown auto-suffisants : chaque fichier "
        + "porte ses métadonnées dans son front matter. Ils s'ouvrent tels quels dans un "
        + "lecteur Markdown, et se reprennent dans " + logo('md-rag.png', 'MD-RAG')
        + " pour être découpés "
        + "et envoyés vers une collection. Tout tourne en local : sources, Markdown et "
        + "métadonnées restent sur votre machine.";
    rail.appendChild(presentation);

    const actions = document.createElement('p');
    actions.className = 'rail-actions';
    // L'engrenage y reste seul : les liens du projet vivent desormais dans
    // le bloc « forge » du bas de rail.
    actions.innerHTML =
        `<button type="button" class="rail-icone" id="btn-config"
                 title="Configuration" aria-label="Configuration">
            <i class="fa-solid fa-gear" aria-hidden="true"></i>
         </button>`;
    rail.appendChild(actions);
}

async function monterVersions() {
    const rail = document.getElementById('app-header');
    if (!rail) return;
    try {
        const response = await fetch('/api/version');
        if (!response.ok) return;
        const info = await response.json();
        const versions = document.createElement('p');
        versions.className = 'rail-versions';
        // Le numero vit dans son propre `span`, et l'icone a cote : les
        // messages qui remplacent le texte ne l'effacent plus.
        const texte = document.createElement('span');
        texte.className = 'rail-versions-texte';
        texte.textContent = `v${info.version}`;
        // Vérification désactivée : le numéro s'affiche seul, sans flèche ni
        // clic. Laisser l'affordance en place ferait attendre une réponse
        // qu'aucune requête ne va chercher.
        if (info.update_check === false) {
            versions.appendChild(texte);
            versions.title = "Vérification des mises à jour désactivée dans cette version.";
            rail.appendChild(versions);
            return;
        }
        // Cliquable : sans cela, une version publiee dans la journee reste
        // invisible jusqu'au lendemain, et relancer n'y change rien puisque
        // le cache vit sur le disque. Encore faut-il que cela se voie —
        // personne ne clique sur un numero de version. La fleche circulaire
        // le dit ; l'attente, elle, est portee par le spinner de la ligne
        // du dessous.
        const icone = document.createElement('i');
        icone.className = 'fa-solid fa-arrows-rotate rail-versions-refresh';
        icone.setAttribute('aria-hidden', 'true');
        // L'etat de la verification vit sous le numero, sur sa propre ligne
        // centree, et ne prend plus sa place : le numero est ce qu'on vient
        // lire, il n'a pas a disparaitre pendant qu'on verifie. Vide, la
        // ligne ne se voit pas.
        const etat = document.createElement('span');
        etat.className = 'rail-versions-etat';
        versions.append(texte, ' ', icone, etat);
        versions.title = 'Cliquer pour vérifier les mises à jour maintenant';
        versions.setAttribute('role', 'button');
        versions.setAttribute('tabindex', '0');
        versions.style.cursor = 'pointer';
        let effacement = null;
        const direEtat = (html, duree) => {
            clearTimeout(effacement);
            etat.innerHTML = html;
            if (duree) effacement = setTimeout(() => { etat.textContent = ''; }, duree);
        };
        const lancer = async () => {
            // Une verification en cours ne se relance pas : deux clics
            // rapides afficheraient deux spinners pour une seule attente.
            if (versions.classList.contains('is-verification')) return;
            versions.classList.add('is-verification');
            // Le spinner de la maison, celui de toutes les attentes de
            // l'application, pose devant son libelle comme partout ailleurs.
            direEtat('<span class="spinner" aria-hidden="true"></span>vérification…');
            const resultat = await verifierMiseAJour({ force: true });
            versions.classList.remove('is-verification');
            if (resultat === 'a-jour') {
                direEtat('à jour', 4000);
            } else if (resultat === 'injoignable') {
                direEtat('Forge injoignable', 6000);
            } else {
                direEtat('', 0);
            }
        };
        versions.addEventListener('click', lancer);
        versions.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); lancer(); }
        });
        rail.appendChild(versions);
    } catch (_) {
        // L'information de version ne doit jamais gener le demarrage.
    }
}

const REPORT_JOURS = 7;

/** « Plus tard » veut dire plus tard, pas « plus jamais ». */
function miseAJourReportee(version) {
    try {
        const brut = localStorage.getItem('metamd-update-reportee');
        if (!brut) return false;
        const report = JSON.parse(brut);
        return report.version === version && Date.now() < report.jusqu;
    } catch (_) {
        return false;
    }
}

function reporterMiseAJour(version) {
    localStorage.setItem('metamd-update-reportee', JSON.stringify({
        version,
        jusqu: Date.now() + REPORT_JOURS * 24 * 60 * 60 * 1000,
    }));
}

/* ============================================================
 * Taches longues : ne pas quitter a l'aveugle, et savoir revenir
 * ============================================================
 *
 * Une conversion tourne cote serveur : quitter la page ne l'interrompt pas.
 * Mais l'identifiant du job ne vivait que dans cette page, et aucune route ne
 * listait les taches en cours — en revenant, on ne voyait plus rien tourner,
 * et on relancait. Deux threads ecrivaient alors le meme fichier.
 */

const LIBELLES_TACHE = {
    'convert-doc': 'Conversion',
    'fill': 'Remplissage des métadonnées',
    'lot-download': 'Téléchargement d\'un lot',
    'depot-fetch': 'Récupération du dépôt',
    'depot-import': 'Import du dépôt',
    'update-install': 'Installation de la mise à jour',
};

/** Ce que dit un `kind` de job, en clair. */
function libelleTache(kind) {
    const [tete, ...reste] = String(kind || '').split(':');
    const nom = LIBELLES_TACHE[tete] || tete || 'Tâche';
    return reste.length ? `${nom} — ${reste[0]}` : nom;
}

/** Taches actives vues par le serveur, ou [] s'il ne répond pas. */
async function tachesEnCours() {
    try {
        const resp = await fetch('/api/corpus/jobs');
        if (!resp.ok) return [];
        const data = await resp.json();
        return (data && data.jobs) || [];
    } catch (_) {
        return [];
    }
}

/**
 * Avertit avant de quitter tant qu'une tache tourne.
 *
 * Le navigateur impose son propre texte : `returnValue` ne sert qu'a declencher
 * la demande. On ne l'arme que si une tache est effectivement en vol, sinon la
 * confirmation devient un reflexe qu'on clique sans lire.
 */
function garderContreLaFermeture() {
    let actives = 0;
    const majSurveillance = async () => { actives = (await tachesEnCours()).length; };
    majSurveillance();
    setInterval(majSurveillance, 5000);
    window.addEventListener('beforeunload', event => {
        if (!actives) return;
        event.preventDefault();
        event.returnValue = '';
    });
}

/**
 * Rebranche l'affichage sur une tache deja en cours, au chargement.
 *
 * Sans cela, recharger la page pendant la conversion d'un document de 63 pages
 * laissait vingt-cinq minutes sans le moindre retour.
 */
async function reprendreLesTaches() {
    const rail = document.getElementById('app-header');
    const taches = await tachesEnCours();
    if (!taches.length || !rail) return;

    const boite = document.createElement('aside');
    boite.className = 'update-notice tache-en-cours';
    boite.setAttribute('role', 'status');
    boite.setAttribute('aria-live', 'polite');
    const titre = document.createElement('strong');
    titre.textContent = taches.length > 1
        ? `${taches.length} tâches en cours` : 'Tâche en cours';
    boite.appendChild(titre);

    const lignes = taches.map(job => {
        const p = document.createElement('p');
        p.textContent = libelleTache(job.kind);
        boite.appendChild(p);
        return [job, p];
    });
    rail.appendChild(boite);

    // On suit chaque tache jusqu'a sa fin, puis on efface la boite.
    await Promise.all(lignes.map(([job, ligne]) =>
        pollJob(job.id, ({ processed, total, current }) => {
            const part = total ? ` ${Math.round((processed / total) * 100)} %` : '';
            ligne.textContent = `${libelleTache(job.kind)}${part}`
                + (current ? ` · ${current}` : '');
        }, 1500).catch(() => null)));
    titre.textContent = 'Tâches terminées';
    setTimeout(() => boite.remove(), 4000);
}

/** Affiche une nouvelle version sans jamais interrompre le travail local. */
async function verifierMiseAJour(options) {
    const force = Boolean(options && options.force);
    try {
        const resp = await fetch(force ? '/api/update?force=1' : '/api/update');
        if (!resp.ok) return 'injoignable';
        const info = await resp.json();
        if (!info.ok) return 'injoignable';
        // Le serveur dit qu'il n'a rien vérifié : c'est un choix, pas une
        // panne. Aucun bandeau, et surtout pas « Forge injoignable ».
        if (info.checked === false) return 'desactivee';
        if (!info.update_available || !info.latest) return 'a-jour';
        // Une verification demandee a la main passe outre un report : c'est
        // precisement ce que l'utilisateur vient de reclamer.
        if (force && document.querySelector('.update-notice')) return 'affichee';
        // Une version signalee importante passe outre le report : c'est tout
        // l'interet du drapeau `critical`, jusqu'ici transmis mais jamais lu.
        if (!force && !info.critical && miseAJourReportee(info.latest)) return 'reportee';

        // Le socle a deja essaye cette version et elle n'a pas demarre. On la
        // propose encore — le paquet a pu etre corrige — mais en le disant.
        const echouee = Boolean(info.failed) && info.failed === info.latest;

        const notice = document.createElement('aside');
        notice.className = 'update-notice';
        notice.setAttribute('role', 'status');
        notice.setAttribute('aria-live', 'polite');

        const texte = document.createElement('div');
        const titre = document.createElement('strong');
        titre.textContent = info.critical
            ? `Mise à jour importante : ${info.latest}`
            : `Mise à jour ${info.latest}`;
        texte.appendChild(titre);
        // La CI remplit le resume avec « META-MD X.Y.Z » : sous un titre qui
        // porte deja le numero, cette ligne n'apprend rien.
        const resume = (info.summary || '').trim();
        if (resume && resume !== `META-MD ${info.latest}`) {
            const p = document.createElement('p');
            p.textContent = resume;
            texte.appendChild(p);
        }
        if (echouee) {
            const alerte = document.createElement('p');
            alerte.className = 'update-notice-error';
            alerte.textContent = "Cette version n’a pas démarré lors d’une tentative "
                + "précédente. La réinstaller remplacera les fichiers déjà déposés.";
            texte.appendChild(alerte);
        }
        notice.appendChild(texte);

        const actions = document.createElement('div');
        actions.className = 'update-notice-actions';
        // Dans une colonne de 15 rem, trois boutons de front debordent.
        // L'action principale prend la largeur, les deux autres se partagent
        // la ligne suivante. Declaree ici : « Détails » y entre plus bas.
        const secondaires = document.createElement('div');
        secondaires.className = 'update-notice-secondaire';
        if (info.release_url) {
            const lien = document.createElement('a');
            lien.className = 'btn ghost small';
            lien.href = info.release_url;
            lien.target = '_blank';
            lien.rel = 'noopener noreferrer';
            lien.textContent = 'Détails';
            secondaires.appendChild(lien);
        }
        if (info.installable) {
            const installer = document.createElement('button');
            installer.type = 'button';
            installer.className = 'btn primary small';
            installer.textContent = echouee ? 'Réinstaller' : 'Installer';
            installer.addEventListener('click', async () => {
                installer.disabled = true;
                fermer.disabled = true;
                installer.textContent = 'Préparation…';
                try {
                    const startResp = await fetch('/api/update/install', { method: 'POST' });
                    const start = await startResp.json();
                    if (!startResp.ok || !start.ok) throw new Error(start.error || 'Installation impossible');
                    const job = await pollJob(start.job_id, progress => {
                        if (progress.state === 'error') return;
                        if (progress.total) {
                            installer.textContent = `Téléchargement ${Math.round(progress.ratio * 100)} %`;
                        } else {
                            installer.textContent = 'Installation…';
                        }
                    }, 400);
                    if (job.state !== 'done') throw new Error(job.error || 'Installation impossible');
                    installer.textContent = 'Redémarrage…';
                    const restartResp = await fetch('/api/update/restart', { method: 'POST' });
                    const restart = await restartResp.json();
                    if (!restartResp.ok || !restart.ok) throw new Error(restart.error || 'Redémarrage impossible');
                    await attendreNouvelleVersion(start.version);
                } catch (error) {
                    installer.textContent = 'Réessayer';
                    installer.disabled = false;
                    fermer.disabled = false;
                    const message = notice.querySelector('.update-notice-error') || document.createElement('p');
                    message.className = 'update-notice-error';
                    message.textContent = error.message || String(error);
                    texte.appendChild(message);
                }
            });
            actions.appendChild(installer);
        }
        const fermer = document.createElement('button');
        fermer.type = 'button';
        fermer.className = 'btn ghost small';
        fermer.textContent = 'Plus tard';
        fermer.title = `Masquer cette version pendant ${REPORT_JOURS} jours`;
        fermer.addEventListener('click', () => {
            reporterMiseAJour(info.latest);
            notice.remove();
        });
        secondaires.appendChild(fermer);
        actions.appendChild(secondaires);
        notice.appendChild(actions);
        // Sous le logo, a gauche, dans le flux de la page : un bandeau
        // flottant en bas a droite se fait oublier, et masque le contenu.
        // Les pages sans en-tete (viewer, exemples) gardent le flottant.
        const rail = document.getElementById('app-header');
        if (rail) {
            rail.appendChild(notice);
        } else {
            notice.classList.add('update-notice-flottant');
            document.body.appendChild(notice);
        }
        return 'affichee';
    } catch (_) {
        // Hors ligne, manifeste absent ou serveur ancien : META-MD reste silencieux.
        return 'injoignable';
    }
}

async function attendreNouvelleVersion(version) {
    // 30 s ne suffisaient pas : la version qui demarre attend d'abord que
    // le port se libere, puis compile ses modules la premiere fois.
    const deadline = Date.now() + 90000;
    // Le premier appel peut encore atteindre l'ancien serveur : attendre
    // explicitement la version voulue avant de recharger la page.
    while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 500));
        try {
            const response = await fetch('/api/version', { cache: 'no-store' });
            if (!response.ok) continue;
            const info = await response.json();
            if (info.version === version) {
                window.location.reload();
                return;
            }
        } catch (_) {
            // Coupure attendue pendant le redémarrage.
        }
    }
    throw new Error('La nouvelle version ne répond pas encore. Elle a peut-être démarré : rechargez la page, ou relancez META-MD.');
}

/** Un lien marqué fermé ne mène nulle part. */
function bloquerLiensDesactives() {
    if (document.body.dataset.liensGardes) return;
    document.body.dataset.liensGardes = '1';
    document.addEventListener('click', event => {
        const lien = event.target.closest('a.is-disabled');
        if (lien) event.preventDefault();
    });
}

/**
 * Le remplissage des champs vides : une action, plus une étape.
 *
 * La conversion Mistral lit déjà les champs du schéma dans le PDF. Il ne
 * reste donc des trous que dans deux cas — un champ ajouté au schéma après
 * la conversion, ou un document converti par un moteur qui ne lit pas les
 * métadonnées. Le bloc n'apparaît que dans ces cas-là, à la fin de l'étape 3,
 * et tant qu'il est là les étapes suivantes attendent.
 *
 * Le décompte vient du serveur, qui compare les clés du schéma aux champs
 * non vides de chaque sidecar. On l'affiche avant de lancer quoi que ce
 * soit : un appel LLM par document se paie, autant savoir combien.
 */
function renderFillStep(ragName, topic) {
    const bloc = document.getElementById('fill-block');
    const btn = document.getElementById('btn-fill');
    const resume = document.getElementById('fill-summary');
    if (!bloc || !btn || !resume) return;

    const verrouille = !!(topic.schema && topic.schema.locked);
    const fill = (topic.steps && topic.steps.fill) || {};
    const convertis = fill.docs_converted || 0;
    const aRemplir = fill.docs_incomplete || 0;
    const attente = fill.pending_fields || 0;

    // Un Markdown converti suffit à ouvrir l'étape. La condition était
    // « il reste des trous » : elle refermait le bouton dans deux cas où on
    // le cherche pourtant — un corpus déjà complet qu'on veut repasser après
    // un changement de schéma, et un document dont la source a été renommée,
    // que le décompte ne voyait plus. Cliquer sans rien à remplir ne coûte
    // rien : le serveur écarte les champs déjà posés avant tout appel LLM.
    const aMontrer = verrouille && convertis > 0;
    bloc.classList.toggle('hidden', !aMontrer);
    if (!aMontrer) return;

    resume.innerHTML = aRemplir > 0
        ? `<strong>${attente}</strong> champ${plur(attente)} à remplir `
          + `sur <strong>${aRemplir}</strong> document${plur(aRemplir)}, soit ${aRemplir} appel${plur(aRemplir)} LLM.`
        : `Tous les champs du schéma sont renseignés sur les `
          + `<strong>${convertis}</strong> document${plur(convertis)} converti${plur(convertis)}.`;
    btn.disabled = false;

    // L'avertissement ne parle que de ce qui manque : sans trou, il n'a rien
    // à dire et laisserait croire à un problème.
    const note = document.getElementById('fill-stale-note');
    if (note) note.classList.toggle('hidden', aRemplir === 0);

    const texte = document.getElementById('fill-stale-text');
    if (texte && aRemplir > 0) {
        // `already_filled` dit seulement que le remplissage a deja tourne sur
        // ce corpus. Le message parlait de « depuis la conversion » : c'etait
        // juste tant que le moteur Document AI lisait les champs du schema
        // pendant la conversion. Plus aucun moteur ne remplit quoi que ce
        // soit, la conversion n'est donc plus le repere — le dernier
        // remplissage l'est.
        texte.innerHTML = fill.already_filled
            ? `Le schéma a changé depuis le dernier remplissage : `
              + `<strong>${attente}</strong> champ${plur(attente)} n'${attente > 1 ? 'ont' : 'a'} jamais été `
              + `renseigné${plur(attente)}. Les étapes suivantes attendent qu'ils le soient. `
              + `Les valeurs déjà en place ne seront pas retouchées.`
            : `<strong>${attente}</strong> champ${plur(attente)} du schéma `
              + `${attente > 1 ? 'restent vides' : 'reste vide'} sur ${aRemplir} document${plur(aRemplir)} `
              + `déjà converti${plur(aRemplir)}. Les étapes suivantes attendent qu'ils soient remplis. `
              + `Le remplissage les déduit du Markdown, sans toucher aux valeurs déjà posées.`;
    }

    if (!btn.dataset.lie) {
        btn.dataset.lie = '1';
        btn.addEventListener('click', () => lancerRemplissage(ragName, btn));
    }
}

/** Lance le remplissage de tout le corpus et suit le job jusqu'au bout. */
async function lancerRemplissage(ragName, btn) {
    const progres = document.getElementById('fill-progress');
    const montrer = texte => {
        if (!progres) return;
        progres.classList.remove('hidden');
        progres.innerHTML = texte;
    };
    setBtnLoading(btn, true);
    try {
        const resp = await fetch('/api/corpus/autofill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ corpus: ragName, only_missing: true }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) {
            showMessage('error', 'Remplissage impossible', (data && data.error) || `HTTP ${resp.status}`);
            return;
        }
        // Pas de spinner ici : le bouton en porte deja un pendant toute
        // l'operation, deux tourniquets cote a cote ne disent rien de plus.
        montrer(`0/${data.total} document${plur(data.total)}…`);
        const fin = await pollJob(data.job_id, p => {
            const fait = p.processed || 0;
            const total = p.total || data.total;
            montrer(`${fait}/${total} document${plur(total)}`
                + (p.current ? ` — ${escapeHtml(p.current)}` : '…'));
        });
        const r = (fin && fin.result) || {};
        if (fin && fin.state === 'error') {
            showMessage('error', 'Remplissage interrompu', fin.error || 'Erreur inconnue');
            montrer('');
            if (progres) progres.classList.add('hidden');
            return;
        }
        const erreurs = (r.errors || []).length;
        montrer(`${r.filled || 0} document${plur(r.filled || 0)} complété${plur(r.filled || 0)}, `
            + `${r.skipped || 0} sans changement`
            + (erreurs ? `, <span class="error">${erreurs} en erreur</span>` : '') + '.');
        if (typeof window.__reloadThemeState === 'function') window.__reloadThemeState();
    } catch (e) {
        showMessage('error', 'Erreur réseau', String(e));
    } finally {
        setBtnLoading(btn, false);
    }
}

async function loadThemeState(themeName) {
    window.__reloadThemeState = () => loadThemeState(themeName);
    initAddSourcesPanel(themeName);
    try {
        const resp = await fetch('/api/corpus/topics');
        const data = await resp.json();
        const topic = (data.topics || []).find(t => t.name === themeName);
        if (!topic) {
            showMessage('error', 'Thème introuvable', `Le thème n'existe pas dans data/CORPUS/.`);
            return;
        }
        // Résumé de conversion sous l'étape 3.
        const cs = document.getElementById('convert-summary');
        if (cs) {
            const convert = topic.steps.convert || {};
            const nActive = convert.docs_with_active || 0;
            const nSourcesActive = topic.sources ? (topic.sources.active_count ?? topic.sources.count) : 0;
            if (nSourcesActive === 0) {
                cs.textContent = 'Aucun PDF à traiter dans 1-Sources/.';
            } else {
                cs.innerHTML = `<strong>${nActive}/${nSourcesActive}</strong> document${plur(nActive)} converti${plur(nActive)}.`;
            }
        }
        // La conversion attend le schéma : inutile d'ouvrir une page dont
        // tous les boutons seraient grisés, on ferme l'entrée et on dit
        // pourquoi ici, sous le titre de l'étape.
        const schemaVerrouille = !!(topic.schema && topic.schema.locked);
        // Un corpus de site n'a rien a convertir : ses Markdown existent deja.
        // L'etape 3 devient un import, sur sa propre page.
        appliquerSourceDuCorpus(topic, themeName);
        const btnOpen = document.getElementById('btn-open-conversions');
        if (btnOpen) {
            btnOpen.href = topic.depuis_depot
                ? `import.html?corpus=${encodeURIComponent(themeName)}`
                : `conversions.html?corpus=${encodeURIComponent(themeName)}`;
            marquerEtapeFermee(btnOpen, !schemaVerrouille,
                "Validez et verrouillez le schéma à l'étape 2 avant de convertir.");
        }
        const noteConvert = document.getElementById('convert-locked-note');
        if (noteConvert) noteConvert.classList.toggle('hidden', schemaVerrouille);
        // Étape 4. Deux conditions, et le message dit laquelle manque :
        // « verrouillée » sans raison oblige à deviner ce qui bloque.
        const fill = (topic.steps && topic.steps.fill) || {};
        const validation = topic.validation || {};
        const totalValid = validation.total || 0;
        const okValid = validation.both_valid || 0;
        const aRemplir = fill.docs_incomplete || 0;
        const raisonRevision = totalValid === 0
            ? "La validation attend qu'au moins un document soit converti, à l'étape 3 : "
              + "il n'y a rien à relire pour l'instant."
            : (aRemplir > 0
                ? `La validation attend que les champs vides soient remplis, à l'étape 3 : `
                  + `relire des métadonnées que le modèle n'a pas encore proposées reviendrait `
                  + `à les relire deux fois.`
                : '');
        const btnRevision = document.getElementById('btn-open-revision');
        if (btnRevision) {
            btnRevision.href = `validation.html?corpus=${encodeURIComponent(themeName)}`;
            marquerEtapeFermee(btnRevision, !!raisonRevision,
                totalValid === 0
                    ? "Convertissez au moins un document à l'étape 3 avant de relire."
                    : "Remplissez les champs vides, à l'étape 3, avant de relire.");
        }
        const noteRevision = document.getElementById('revision-locked-note');
        const texteRevision = document.getElementById('revision-locked-text');
        if (noteRevision) noteRevision.classList.toggle('hidden', !raisonRevision);
        if (texteRevision && raisonRevision) texteRevision.textContent = raisonRevision;
        const rs = document.getElementById('revision-summary-line');
        if (rs) {
            rs.innerHTML = totalValid === 0
                ? 'Aucun document converti à relire.'
                : `<strong>${okValid}/${totalValid}</strong> document${plur(totalValid)} validé${plur(totalValid)} (Markdown et métadonnées).`;
        }
        // Étape 5. Un Markdown se télécharge une fois relu : sinon il emporte
        // des métadonnées que personne n'a vérifiées.
        // Le téléchargement n'est pas verrouillé : emporter un Markdown non
        // relu est un choix légitime — on relit parfois ailleurs, on exporte
        // parfois pour comparer. L'avertissement rouge dit ce qu'on emporte,
        // il ne l'interdit pas.
        const toutValide = totalValid > 0 && okValid >= totalValid;
        const noteMarkdown = document.getElementById('markdown-warning-note');
        const texteMarkdown = document.getElementById('markdown-warning-text');
        const aAvertir = totalValid > 0 && !toutValide;
        if (noteMarkdown) noteMarkdown.classList.toggle('hidden', !aAvertir);
        if (texteMarkdown && aAvertir) {
            texteMarkdown.textContent = "Toutes les conversions n'ont pas été validées.";
        }
        renderFillStep(themeName, topic);
        renderMdInventory(themeName);
        const zipBtn = document.getElementById('btn-md-zip');
        if (zipBtn && !zipBtn.dataset.lie) {
            zipBtn.dataset.lie = '1';
            zipBtn.addEventListener('click', () => telechargerMdZip(themeName, zipBtn));
            const toutes = document.getElementById('md-inv-all');
            if (toutes) toutes.addEventListener('change', () => {
                document.querySelectorAll('.md-inv-check:not(:disabled)')
                    .forEach(cb => { cb.checked = toutes.checked; });
                majMdZip();
            });
            const hote = document.getElementById('md-inventory-list');
            if (hote) {
                hote.addEventListener('change', event => {
                    if (event.target.classList.contains('md-inv-check')) majMdZip();
                });
                hote.addEventListener('click', event => {
                    const bouton = event.target.closest('.md-inv-open');
                    if (bouton) basculerApercuMd(bouton);
                });
            }
        }

        // Inventaire des sources (etape 1 : coche/decoche pour exclure).
        renderSourcesInventory(themeName, topic.sources || {}, false);

        // Prochaine étape
        const nextStep = computeNextStep(topic);
        const nextTitle = document.querySelector('[data-bind="next-step-title"]');
        const nextText = document.querySelector('[data-bind="next-step-text"]');
        if (nextTitle) nextTitle.innerHTML = 'Prochaine étape : ' + nextStep.title;
        if (nextText) nextText.innerHTML = nextStep.text;
        updatePipelineFlow(topic, nextStep);

    } catch (e) {
        showMessage('error', 'Erreur réseau', String(e));
    }
}

/**
 * Envoi vers une collection, avec compte rendu affiché sous le formulaire.
 *
 * Le bandeau global est en haut de page, donc hors écran quand on agit
 * depuis l'étape 5 : le résultat doit apparaître là où on vient de cliquer.
 */
/* ============ Page import.html (pages d'un site) ============ */

/**
 * Etape 3 d'un corpus de site : choisir les pages, les faire entrer.
 *
 * Le pendant de conversions.html, dont elle reprend la charpente — liste a
 * cocher, barre d'actions groupees, bordure verte sur ce qui est fait, fait en
 * tete. Elle ne parle en revanche ni de moteur, ni de duree, ni de quota :
 * copier un fichier n'a aucun de ces couts, et laisser ces reperes donnerait a
 * croire le contraire.
 */
async function initImportPage() {
    if (document.body.dataset.page !== 'import') return;
    const rag = getQueryParam('corpus').trim();
    const nameEl = document.getElementById('import-theme-name');
    const backLink = document.getElementById('import-back-link');
    const listEl = document.getElementById('import-list');
    const summaryEl = document.getElementById('import-summary');
    const bulkBar = document.getElementById('import-bulk-bar');
    const selectAll = document.getElementById('import-select-all');
    const onlyProposed = document.getElementById('import-only-proposed');
    const bulkSummary = document.getElementById('import-bulk-summary');
    const btnImport = document.getElementById('btn-import-selected');

    if (!rag) {
        showMessage('error', 'URL invalide', "Paramètre ?corpus=… manquant.");
        return;
    }
    if (backLink) backLink.href = `corpus.html?corpus=${encodeURIComponent(rag)}`;
    if (nameEl) nameEl.textContent = rag;

    // `schemaLocked` optimiste tant que le serveur n'a pas repondu : on ne
    // grise pas une interface sur une supposition, seulement sur un fait.
    const state = { rag, pages: [], importes: new Set(), schemaLocked: true, fiche: null };

    const majSelection = () => {
        const coches = listEl.querySelectorAll('input[data-page-select]:checked');
        const n = coches.length;
        if (bulkSummary) {
            bulkSummary.textContent = n
                ? `${n} page${plur(n)} sélectionnée${plur(n)}.`
                : 'Aucune page sélectionnée.';
        }
        if (btnImport) btnImport.disabled = !n || !state.schemaLocked;
        if (selectAll) {
            const visibles = listEl.querySelectorAll('input[data-page-select]');
            selectAll.checked = visibles.length > 0 && n === visibles.length;
            selectAll.indeterminate = n > 0 && n < visibles.length;
        }
    };

    const reload = async () => {
        try {
            const [rd, rs, rdocs] = await Promise.all([
                fetch(`/api/corpus/depot?corpus=${encodeURIComponent(rag)}`),
                fetch(`/api/corpus/schema?corpus=${encodeURIComponent(rag)}`),
                fetch(`/api/corpus/documents?corpus=${encodeURIComponent(rag)}`),
            ]);
            const depot = await rd.json();
            const schema = await rs.json();
            const docs = await rdocs.json();

            state.fiche = (depot && depot.fiche) || null;
            state.pages = (state.fiche && state.fiche.pages) || [];
            state.schemaLocked = !!(schema && schema.schema && schema.schema.locked);
            // Une page deja importee a une conversion active : c'est le meme
            // signal que la bordure verte de l'etape 3 des PDF.
            state.importes = new Set(
                ((docs && docs.documents) || [])
                    .filter(d => d.active_md_url)
                    .map(d => (d.source_name || '').replace(/\.md$/i, '')));

            afficherVerrouImport(state.schemaLocked, rag);

            if (!state.fiche) {
                listEl.innerHTML = `<p class="muted small">Aucun dépôt récupéré.
                    Revenez à l'étape 1 pour indiquer son adresse.</p>`;
                if (bulkBar) bulkBar.classList.add('hidden');
                if (summaryEl) summaryEl.innerHTML = '';
                return;
            }

            if (summaryEl) {
                const n = state.pages.length;
                const dedans = state.importes.size;
                const ecartees = state.pages.filter(p => p.raison).length;
                const partEcartee = ecartees
                    ? ` · <strong>${ecartees}</strong> sans texte à indexer`
                    : '';
                summaryEl.innerHTML = `
                    <p class="extraction-summary-line">
                        <strong>${n}</strong> fichier${plur(n)} Markdown ·
                        <strong>${dedans}</strong> déjà importé${plur(dedans)}${partEcartee}
                    </p>
                    <p class="muted small">${escapeHtml(state.fiche.resume || '')}</p>`;
            }
            if (bulkBar) bulkBar.classList.toggle('hidden', state.pages.length === 0);
            listEl.innerHTML = renderImportList(state, !!(onlyProposed && onlyProposed.checked));
            majSelection();
        } catch (e) {
            listEl.innerHTML = `<p class="muted small">Erreur réseau : ${escapeHtml(String(e))}</p>`;
        }
    };

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            const veut = selectAll.checked;
            listEl.querySelectorAll('input[data-page-select]').forEach(cb => { cb.checked = veut; });
            majSelection();
        });
    }
    if (onlyProposed) {
        onlyProposed.addEventListener('change', () => {
            listEl.innerHTML = renderImportList(state, onlyProposed.checked);
            majSelection();
        });
    }
    if (listEl) {
        listEl.addEventListener('change', event => {
            if (event.target && event.target.matches('input[data-page-select]')) majSelection();
        });
    }
    if (btnImport) {
        btnImport.addEventListener('click', async () => {
            const coches = Array.from(listEl.querySelectorAll('input[data-page-select]:checked'));
            const stems = coches.map(i => i.dataset.pageSelect);
            if (!stems.length) return;
            await lancerImport(rag, stems, state, btnImport, reload);
        });
    }

    await reload();
}

/** Bandeau affiche quand le schema n'est pas verrouille. */
function afficherVerrouImport(locked, rag) {
    const hote = document.getElementById('import-schema-lock');
    if (!hote) return;
    hote.classList.toggle('hidden', !!locked);
    if (locked) return;
    hote.innerHTML = `
        <i class="fa-solid fa-lock" aria-hidden="true"></i>
        <span>Le schéma de ce corpus n'est pas verrouillé, l'import est
        indisponible. Le front matter des documents est écrit au moment de
        l'import : il faut connaître les champs avant.</span>
        <a class="btn ghost" href="corpus.html?corpus=${encodeURIComponent(rag)}">
            <i class="fa-solid fa-arrow-left"></i> Aller au schéma
        </a>`;
}

/**
 * Liste des pages du depot.
 *
 * Les pages deja importees remontent en tete, comme les documents convertis a
 * l'etape 3 : ce qui est fait se lit d'un bloc.
 */
function renderImportList(state, seulementProposees) {
    const pages = state.pages.filter(p => !seulementProposees || p.propose);
    if (!pages.length) {
        return `<p class="muted small">${seulementProposees
            ? "Aucune page du site dans ce dépôt. Décochez « Seulement les pages du site » pour voir tous les Markdown."
            : "Aucun fichier Markdown dans ce dépôt."}</p>`;
    }
    const rang = p => (state.importes.has(p.stem) ? 0 : 1);
    const triees = pages.slice().sort((a, b) => rang(a) - rang(b));
    return `<ul class="sources-list">${triees.map(p => renderImportRow(p, state)).join('')}</ul>`;
}

function renderImportRow(page, state) {
    const deja = state.importes.has(page.stem);
    const titre = (page.titre || '').trim();
    const nav = page.dans_nav
        ? `<span class="page-nav-rank" title="Position dans la navigation du site">nav ${page.ordre}</span>`
        : `<span class="page-hors-nav" title="Absent de la navigation du site">hors nav</span>`;
    const lienSite = page.url_publique
        ? `<a class="page-lien" href="${escapeHtml(page.url_publique)}" target="_blank" rel="noreferrer" title="Voir la page en ligne"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>`
        : '';
    // Une page ecartee dit pourquoi : sans le motif, une case decochee laisse
    // croire a un oubli et on la recoche sans savoir ce qu'on importe.
    const raison = page.raison
        ? `<p class="page-raison muted small"><i class="fa-solid fa-circle-info" aria-hidden="true"></i> ${escapeHtml(page.raison)}</p>`
        : '';
    return `
    <li class="source-row ${deja ? 'is-imported' : 'is-pending-import'}${page.raison ? ' is-sans-texte' : ''}" data-stem="${escapeHtml(page.stem)}">
        <div class="source-line">
            <span class="source-check-cell">
                <input type="checkbox" class="source-check" data-page-select="${escapeHtml(page.stem)}"
                       ${page.propose ? 'checked' : ''} title="Sélectionner pour l'import">
            </span>
            <span class="source-name ${titre ? 'has-title' : ''}">
                ${titre ? `<span class="source-title">${escapeHtml(titre)}</span>` : ''}
                <span class="source-filename"><i class="fa-solid fa-file-lines"></i> ${escapeHtml(page.chemin)}</span>
            </span>
            <span class="source-meta-cell muted small">
                <span class="source-meta-line">
                    <span class="source-size">${escapeHtml(humanBytes(page.octets || 0))}</span>
                    ${nav}
                </span>
            </span>
            ${lienSite}
        </div>
        ${raison}
    </li>`;
}

/** Lance l'import et rend compte, en reprenant la barre de progression. */
async function lancerImport(rag, stems, state, bouton, reloadFn) {
    const dejaDedans = stems.filter(s => state.importes.has(s));
    let overwrite = false;
    if (dejaDedans.length) {
        // Meme garde que la reconversion : une copie de travail peut porter
        // des corrections faites dans le viewer.
        overwrite = confirm(
            `${dejaDedans.length} page${plur(dejaDedans.length)} de la sélection `
            + `${dejaDedans.length > 1 ? 'ont' : 'a'} déjà été importée${plur(dejaDedans.length)}.\n\n`
            + `Les réimporter remplacera leur copie de travail et les corrections `
            + `qu'elle contient.\n\nRemplacer ?`);
    }
    setBtnLoading(bouton, true);
    const pb = renderProgressBar(
        ensureExtractionProgressHost(document.getElementById('import-bulk-bar')),
        `Import de ${stems.length} page${plur(stems.length)}…`);
    try {
        const resp = await fetch('/api/corpus/depot/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ corpus: rag, stems, overwrite }),
        });
        const data = await resp.json();
        if (data && data.schema_locked === false) {
            pb.setState('error', '✗ schéma non verrouillé');
            showMessage('error', 'Schéma non verrouillé', data.error || '');
            return;
        }
        if (!resp.ok || !data.ok || !data.job_id) {
            pb.setState('error', `✗ ${escapeHtml((data && data.error) || 'HTTP ' + resp.status)}`);
            return;
        }
        const job = await pollJob(data.job_id, ({ processed, total, current }) => {
            pb.update({ processed, total, current });
        });
        const res = job && job.result;
        if (job.state === 'done' && res && res.ok) {
            const ignore = res.ignorees
                ? `, ${res.ignorees} inchangée${plur(res.ignorees)}` : '';
            const vides = (res.sans_texte || []).length;
            const partVide = vides
                ? `, ${vides} écartée${plur(vides)} faute de texte` : '';
            pb.setState('done', `✓ ${res.importees} page${plur(res.importees)} importée${plur(res.importees)}${ignore}${partVide}`);
            if (vides) {
                showMessage('info', 'Pages écartées',
                    `${vides} page${plur(vides)} sélectionnée${plur(vides)} n'${vides > 1 ? 'ont' : 'a'} `
                    + `aucun texte à indexer et n'${vides > 1 ? 'ont' : 'a'} pas été importée${plur(vides)} : `
                    + (res.sans_texte || []).slice(0, 8).join(', ')
                    + (vides > 8 ? '…' : ''));
            }
        } else {
            const err = (res && res.error) || job.error || 'Erreur inconnue';
            pb.setState('error', `✗ ${escapeHtml(err)}`);
            showMessage('error', 'Import impossible', err);
        }
        if (typeof reloadFn === 'function') await reloadFn();
    } catch (e) {
        pb.setState('error', `✗ ${escapeHtml(String(e))}`);
    } finally {
        setBtnLoading(bouton, false);
    }
}

/* ============ Corpus partant d'un depot (11ty, MkDocs) ============ */

/**
 * Montre l'etape 1 qui correspond a la source du corpus, et renomme l'etape 3.
 *
 * Les deux formes de l'etape 1 coexistent dans corpus.html, marquees
 * `data-source-pdf` et `data-source-depot` : les reveler ici plutot que de
 * construire la page en JS garde le HTML lisible, et l'aide contextuelle avec.
 */
function appliquerSourceDuCorpus(topic, themeName) {
    const depuisDepot = !!topic.depuis_depot;
    // Un panneau repliable garde la main sur son `hidden` : c'est son etat de
    // pliage, pas sa pertinence. Le reveler ici l'ouvrirait sans passer par
    // son bouton — or c'est le bouton qui monte son contenu au premier
    // depliage. Le panneau « Ajouter des documents » s'affichait ainsi deja
    // ouvert, mais ses lots predefinis restaient sur « Chargement… », faute
    // d'avoir ete demandes. On peut donc le cacher, jamais l'ouvrir.
    const montrer = (el, pertinent) => {
        if (pertinent && el.hasAttribute('data-replie')) return;
        el.classList.toggle('hidden', !pertinent);
    };
    document.querySelectorAll('[data-source-pdf]').forEach(el => {
        montrer(el, !depuisDepot);
    });
    document.querySelectorAll('[data-source-depot]').forEach(el => {
        montrer(el, depuisDepot);
    });
    if (!depuisDepot) return;

    remplacerLibelleEtape('#extraction-block h2', 'Source', 'Dépôt');
    remplacerLibelleEtape('#convert-block h2', 'Conversion', 'Import');
    initDepotPanel(themeName);
}

/**
 * Renomme une etape sans toucher a son numero ni a son icone : seul le noeud
 * texte du titre est remplace.
 */
function remplacerLibelleEtape(selecteur, ancien, nouveau) {
    const titre = document.querySelector(selecteur);
    if (!titre) return;
    const noeud = Array.from(titre.childNodes).find(
        n => n.nodeType === Node.TEXT_NODE && n.textContent.trim() === ancien);
    if (noeud) noeud.textContent = ' ' + nouveau;
}

function dateLisible(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('fr-FR');
}

/** Fiche du depot deja recupere, rendue sous le formulaire. */
function renderDepotFiche(fiche) {
    const hote = document.getElementById('depot-fiche');
    if (!hote) return;
    if (!fiche || !fiche.url) {
        hote.classList.add('hidden');
        hote.innerHTML = '';
        return;
    }
    const pages = fiche.pages || [];
    const proposees = pages.filter(p => p.propose).length;
    const ligneSite = fiche.site_url
        ? `<dt>Site</dt><dd><a href="${escapeHtml(fiche.site_url)}" target="_blank" rel="noreferrer">${escapeHtml(fiche.site_url)}</a></dd>`
        : '';
    hote.classList.remove('hidden');
    hote.innerHTML = `
        <dl class="depot-meta">
            <dt>Dépôt</dt><dd><a href="${escapeHtml(fiche.url)}" target="_blank" rel="noreferrer">${escapeHtml(fiche.url)}</a></dd>
            <dt>Branche</dt><dd>${escapeHtml(fiche.ref || '—')}</dd>
            ${ligneSite}
            <dt>Récupéré</dt><dd>${escapeHtml(dateLisible(fiche.recupere_le))}</dd>
            <dt>Pages</dt><dd><strong>${proposees}</strong> proposée${plur(proposees)} sur ${pages.length} fichier${plur(pages.length)} Markdown</dd>
        </dl>
        <p class="muted small depot-resume">${escapeHtml(fiche.resume || '')}</p>`;
}

/** Formulaire de recuperation d'un depot : saisie, job, fiche. */
function initDepotPanel(themeName) {
    const bloc = document.getElementById('depot-block');
    if (!bloc || bloc.dataset.monte === '1') return;
    bloc.dataset.monte = '1';

    const champUrl = document.getElementById('depot-url');
    const champRef = document.getElementById('depot-ref');
    const champSite = document.getElementById('depot-site-url');
    const bouton = document.getElementById('btn-depot-fetch');
    const statut = document.getElementById('depot-status');

    const charger = async () => {
        try {
            const resp = await fetch(`/api/corpus/depot?corpus=${encodeURIComponent(themeName)}`);
            const data = await resp.json();
            const fiche = (data && data.fiche) || null;
            renderDepotFiche(fiche);
            // Re-recuperer un depot est le cas courant : on repropose ce qui a
            // servi la derniere fois plutot qu'un formulaire vide.
            if (fiche && fiche.url) {
                if (champUrl && !champUrl.value) champUrl.value = fiche.url;
                if (champRef && !champRef.value) champRef.value = fiche.ref || '';
                if (champSite && !champSite.value) champSite.value = fiche.site_url || '';
            }
        } catch (_) { /* la fiche est un confort : son absence ne bloque rien */ }
    };

    if (bouton) {
        bouton.addEventListener('click', async () => {
            const url = ((champUrl && champUrl.value) || '').trim();
            if (!url) {
                statut.textContent = "Indiquez l'adresse du dépôt.";
                statut.classList.add('error');
                if (champUrl) champUrl.focus();
                return;
            }
            statut.classList.remove('error');
            statut.textContent = '';
            setBtnLoading(bouton, true);
            const pb = renderProgressBar(ensureExtractionProgressHost(bloc),
                `Récupération de ${url}…`);
            try {
                const resp = await fetch('/api/corpus/depot/fetch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        corpus: themeName,
                        url,
                        ref: ((champRef && champRef.value) || '').trim(),
                        site_url: ((champSite && champSite.value) || '').trim(),
                    }),
                });
                const data = await resp.json();
                if (!resp.ok || !data.ok || !data.job_id) {
                    pb.setState('error', `✗ ${escapeHtml(data.error || 'HTTP ' + resp.status)}`);
                    return;
                }
                const job = await pollJob(data.job_id, ({ processed, total, current }) => {
                    pb.update({ processed, total, current });
                });
                const res = job && job.result;
                if (job.state === 'done' && res && res.ok) {
                    const n = (res.fiche.pages || []).filter(p => p.propose).length;
                    pb.setState('done', `✓ ${n} page${plur(n)} prête${plur(n)} à importer`);
                    renderDepotFiche(res.fiche);
                    if (typeof window.__reloadThemeState === 'function') {
                        window.__reloadThemeState();
                    }
                } else {
                    const err = (res && res.error) || job.error || 'Erreur inconnue';
                    pb.setState('error', `✗ ${escapeHtml(err)}`);
                    showMessage('error', 'Récupération impossible', err);
                }
            } catch (e) {
                pb.setState('error', `✗ ${escapeHtml(String(e))}`);
            } finally {
                setBtnLoading(bouton, false);
            }
        });
    }

    charger();
}

function initRagThemePage() {
    if (document.body.dataset.page !== 'rag-theme') return;
    const theme = getQueryParam('corpus').trim();
    const nameEl = document.getElementById('theme-name');
    if (!theme) {
        if (nameEl) nameEl.textContent = '(aucun)';
        showMessage('error', 'URL invalide', "Paramètre ?corpus=... manquant.");
        return;
    }
    if (nameEl) nameEl.textContent = theme;

    loadRagSchema(theme);
    bloquerLiensDesactives();
    loadThemeState(theme);
}

/* ============ Extraction page (fichiers d'un run) ============ */

// Estimation grossiere du temps de conversion par moteur, en secondes par
// page. Utilise sur la page extraction pour donner un ordre de grandeur
// avant de lancer la conversion d'une selection.
// Refus du serveur quand on convertit avant d'avoir arrêté les champs. Le
// message dit quoi faire, pas seulement ce qui ne va pas.
const SCHEMA_AVANT_CONVERSION =
    'Le schéma de ce corpus n\'est pas verrouillé. Revenez à l\'étape 2, '
    + 'arrêtez les champs puis cliquez sur « Valider et verrouiller ». '
    + 'La conversion lit ces champs dans le PDF au passage : elle a besoin '
    + 'de les connaître avant de partir.';

const ENGINE_SECONDS_PER_PAGE = {
    pymupdf: 0.5,
    albert_vision: 8,
    vision: 8,
    // Un seul appel porte 50 pages, la ou Vision en fait un par page.
    mistral_document_ai: 1.2,
    // Meme endpoint, meme decoupe en lots : meme ordre de grandeur.
    albert_ocr: 1.2,
    // Mesure du 2026-08-29 sur programme-cycle-1-consolide-127565.pdf :
    // 1493 s pour 63 pages, soit 23,7 s par page en temps reel. Les 8 s
    // d'origine dataient d'avant la description des figures, qui allonge la
    // reponse du modele : l'estimation annoncait trois fois moins que la
    // realite, et 8 min 24 s pour une conversion qui en prend 25.
    albert_vision_figures: 24,
};

function estimateSecondsForPages(pages, engine) {
    const spp = ENGINE_SECONDS_PER_PAGE[engine] ?? ENGINE_SECONDS_PER_PAGE.albert_vision;
    return Math.max(1, Math.round(pages * spp));
}

function formatDuration(seconds) {
    if (!seconds || seconds < 1) return '<1s';
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m < 60) return s ? `${m}min ${s}s` : `${m}min`;
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return mm ? `${h}h ${mm}min` : `${h}h`;
}

/* ============ Page conversions.html (doc par doc) ============ */

/**
 * Pose (ou retire) la validation sur une liste de documents.
 *
 * Retourne les noms qui ont échoué. Les requêtes partent par paquets : un
 * thème peut porter 150 documents, et un envoi strictement séquentiel serait
 * interminable, tandis que 150 requêtes simultanées noieraient le serveur
 * local (un ThreadingHTTPServer de la stdlib).
 */
async function setValidBulk(rag, sources, next, batchSize = 8) {
    const failed = [];
    for (let i = 0; i < sources.length; i += batchSize) {
        const batch = sources.slice(i, i + batchSize);
        await Promise.all(batch.map(async source => {
            try {
                const resp = await fetch('/api/corpus/source-metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    // Écriture partielle : sans `fields`, les métadonnées du
                    // document ne sont pas touchées.
                    // `validated_in_bulk` n'est posé qu'à la validation. À
                    // l'annulation on ne l'envoie pas : le serveur l'efface
                    // de lui-même dès que la validation change.
                    body: JSON.stringify({
                        corpus: rag, source,
                        metadata: next
                            ? { md_valid: true, metadata_valid: true, validated_in_bulk: true }
                            : { md_valid: false, metadata_valid: false },
                    }),
                });
                const data = await resp.json();
                if (!data || !data.ok) failed.push(source);
            } catch (_) {
                failed.push(source);
            }
        }));
    }
    return failed;
}

async function initConversionsPage() {
    if (document.body.dataset.page !== 'conversions') return;
    const rag = getQueryParam('corpus').trim();
    const nameEl = document.getElementById('conversions-theme-name');
    const backLink = document.getElementById('conversions-back-link');
    const listEl = document.getElementById('conversions-list');
    const summaryEl = document.getElementById('conversions-summary');
    const bulkBar = document.getElementById('conversions-bulk-bar');
    const bulkSelectAll = document.getElementById('conversions-select-all');
    const bulkSummary = document.getElementById('conversions-bulk-summary');
    const bulkConvertBtn = document.getElementById('btn-convert-selected');
    // Le menu de moteur a ete retire de la barre groupee : toute conversion
    // part sur MOTEUR_CONVERSION. La recherche reste faite, et chaque usage
    // reste garde, pour que remettre le <select> dans conversions.html suffise
    // a rendre le choix.
    const bulkEngineSel = document.getElementById('bulk-engine');

    if (!rag) {
        showMessage('error', 'URL invalide', "Paramètre ?corpus=… manquant.");
        return;
    }
    if (backLink) backLink.href = `corpus.html?corpus=${encodeURIComponent(rag)}`;
    if (nameEl) nameEl.textContent = rag;

    // `schemaLocked` optimiste tant que le serveur n'a pas repondu : on ne
    // grise pas une interface sur une supposition, seulement sur un fait.
    const state = { rag, documents: [], schemaLocked: true };

    const updateBulkSummary = () => {
        if (!bulkBar) return;
        const checked = listEl.querySelectorAll('input[data-doc-select]:checked');
        const selected = Array.from(checked).map(i => i.dataset.docSelect);
        const selectedPages = selected.reduce((acc, name) => {
            const d = state.documents.find(x => x.source_name === name);
            return acc + ((d && d.pages) || 0);
        }, 0);
        const engine = bulkEngineSel ? bulkEngineSel.value : MOTEUR_CONVERSION;
        if (bulkSummary) {
            if (!selected.length) {
                bulkSummary.textContent = `Aucun document sélectionné.`;
            } else {
                // Le moteur n'est pas rappelé ici : il n'y en a plus qu'un.
                const eta = selectedPages > 0
                    ? ` · ~${formatDuration(estimateSecondsForPages(selectedPages, engine))} estimé`
                    : '';
                bulkSummary.innerHTML = `<strong>${selected.length}</strong> document${selected.length > 1 ? 's' : ''} sélectionné${selected.length > 1 ? 's' : ''}, <strong>${selectedPages}</strong> pages${eta}`;
            }
        }
        if (bulkConvertBtn) {
            bulkConvertBtn.disabled = !selected.length || !state.schemaLocked;
        }
        if (bulkEngineSel) bulkEngineSel.disabled = !state.schemaLocked;
        if (bulkSelectAll) {
            const total = state.documents.length;
            const allChecked = total > 0 && selected.length === total;
            bulkSelectAll.checked = allChecked;
            bulkSelectAll.indeterminate = !allChecked && selected.length > 0;
        }
    };

    window.__reloadConversions = () => reload();

    const reload = async () => {
        try {
            const resp = await fetch(`/api/corpus/documents?corpus=${encodeURIComponent(rag)}`);
            const data = await resp.json();
            if (!data || !data.ok) {
                listEl.innerHTML = `<p class="muted small">Impossible de charger : ${escapeHtml((data && data.error) || 'erreur inconnue')}</p>`;
                return;
            }
            state.documents = data.documents || [];
            state.schemaLocked = data.schema_locked !== false;
            const total = state.documents.length;
            if (bulkBar) bulkBar.classList.toggle('hidden', total === 0);
            if (summaryEl) summaryEl.innerHTML = renderConversionsSummary(state.documents);
            afficherVerrouSchema(state.schemaLocked, rag);
            listEl.innerHTML = renderConversionsList(state.documents,
                { rag, mode: 'convert', schemaLocked: state.schemaLocked });
            bindConversionsRows(listEl, state, reload);
            updateBulkSummary();
        } catch (e) {
            listEl.innerHTML = `<p class="muted small">Erreur réseau : ${escapeHtml(String(e))}</p>`;
        }
    };

    if (bulkSelectAll) {
        bulkSelectAll.addEventListener('change', () => {
            const want = bulkSelectAll.checked;
            listEl.querySelectorAll('input[data-doc-select]').forEach(cb => { cb.checked = want; });
            updateBulkSummary();
        });
    }
    if (listEl) {
        listEl.addEventListener('change', event => {
            if (event.target && event.target.matches('input[data-doc-select]')) {
                updateBulkSummary();
            }
        });
    }
    if (bulkEngineSel) {
        bulkEngineSel.addEventListener('change', updateBulkSummary);
    }
    if (bulkConvertBtn) {
        bulkConvertBtn.addEventListener('click', async () => {
            const checked = listEl.querySelectorAll('input[data-doc-select]:checked');
            const sources = Array.from(checked).map(i => i.dataset.docSelect);
            if (!sources.length) return;
            const engine = bulkEngineSel ? bulkEngineSel.value : MOTEUR_CONVERSION;
            await launchBulkConversions(rag, sources, engine, bulkConvertBtn, reload);
        });
    }
    await reload();
}

/**
 * Ligne de résumé, rendue au-dessus de la liste.
 *
 * Le décompte des validations n'apparaît que sur la page qui les pose :
 * la page des conversions ne parle que de conversions.
 */
function renderConversionsSummary(docs, mode = 'convert') {
    if (!docs.length) return '';
    const total = docs.length;
    const nActive = docs.filter(d => d.active_id).length;
    const bothOk = docs.filter(d => d.active_id && d.md_valid && d.metadata_valid).length;
    const valides = mode !== 'revision' ? '' :
        ` · <strong class="${bothOk === nActive && nActive > 0 ? 'ok' : ''}">${bothOk}/${nActive}</strong> validés`;
    return `
        <p class="extraction-summary-line">
            <strong>${total}</strong> document${total > 1 ? 's' : ''} · <strong>${nActive}</strong> converti${nActive > 1 ? 's' : ''}${valides}
        </p>`;
}

/**
 * Bandeau affiché quand le schéma n'est pas verrouillé.
 *
 * Il double le grisage des boutons : un bouton gris dit qu'on ne peut pas,
 * il ne dit pas pourquoi ni où aller. Le lien renvoie à l'étape qui débloque.
 */
function afficherVerrouSchema(locked, rag) {
    const hote = document.getElementById('conversions-schema-lock');
    if (!hote) return;
    hote.classList.toggle('hidden', !!locked);
    if (locked) return;
    hote.innerHTML = `
        <i class="fa-solid fa-lock" aria-hidden="true"></i>
        <span>Le schéma de ce corpus n'est pas verrouillé, la conversion est
        indisponible. Elle lit les champs du schéma dans le PDF au passage :
        elle a besoin de les connaître avant de partir.</span>
        <a class="btn ghost" href="corpus.html?corpus=${encodeURIComponent(rag)}">
            <i class="fa-solid fa-arrow-left"></i> Aller au schéma
        </a>`;
}

function renderConversionsList(docs, ctx) {
    const mode = ctx.mode || 'convert';
    // Etape 5 : un document sans conversion n'a rien a relire, et sa ligne ne
    // proposait que « Pas de conversion : rien a relire ». Il n'en a donc plus.
    // L'etape 3, elle, existe pour les convertir : elle les montre tous.
    // Le resume au-dessus continue de compter tout le corpus — c'est lui qui
    // dit combien de documents ne sont pas encore convertis.
    const visibles = mode === 'revision' ? docs.filter(d => d.active_md_url) : docs;
    if (!visibles.length) {
        return `<p class="muted small">${mode === 'revision'
            ? "Aucune conversion à relire : convertissez d'abord des documents à l'étape 3."
            : 'Aucun PDF dans 1-Sources/.'}</p>`;
    }
    const rows = ordonnerConversions(visibles, ctx)
        .map((d, idx) => renderConversionRow(d, idx, ctx)).join('');
    return `<ul class="sources-list">${rows}</ul>`;
}

/**
 * Ordre d'affichage des documents : ce qui est fait remonte en tete.
 *
 * Chaque page classe sur le signal qu'elle affiche deja, pour que le
 * regroupement et l'etat lu sur la ligne ne puissent pas se contredire :
 *
 *   etape 3, conversions  converti (`active_id`, celui de la bordure verte)
 *                         puis reste a convertir
 *   etape 5, validation   valide (les deux drapeaux, comme le rond vert)
 *                         puis a relire — les documents sans conversion sont
 *                         deja ecartes de la liste, il n'y a que deux groupes
 *
 * Le tri est stable et ne compare que ce rang : a l'interieur de chaque
 * groupe, l'ordre alphabetique rendu par le serveur est conserve. Il opere
 * sur une copie, `state.documents` gardant l'ordre du serveur.
 *
 * Consequence assumee a l'etape 5 : valider une ligne la fait remonter au
 * rechargement. C'est le prix du regroupement demande — la ligne rejoint le
 * bloc de ce qui est fait au lieu de rester au milieu de ce qui reste.
 */
function ordonnerConversions(docs, ctx) {
    const rang = (ctx.mode || 'convert') === 'revision'
        ? (d => (d.md_valid && d.metadata_valid) ? 0 : 1)
        : (d => (d.active_id ? 0 : 1));
    return docs.slice().sort((a, b) => rang(a) - rang(b));
}

/**
 * Validation en lot, réversible sans perte.
 *
 * Le drapeau `validated_in_bulk` du sidecar dit quels documents CE bouton a
 * validés : au clic suivant on ne dévalide qu'eux, un document validé à la
 * main reste validé. La mémoire vit avec la donnée, donc elle survit à la
 * fermeture du navigateur et vaut d'un poste à l'autre. Le serveur l'efface
 * dès que la validation change par un autre chemin (case du viewer, rond de
 * ligne, reconversion).
 */
function bindBulkValidate(rag, state, btn, reloadFn) {
    if (!btn) return () => {};

    const posedInBulk = () => state.documents
        .filter(d => d.validated_in_bulk && d.md_valid && d.metadata_valid)
        .map(d => d.source_name);

    /**
     * Ce que le bouton fera au prochain clic, selon l'état des documents :
     *
     *   'undo-bulk'  des documents portent le drapeau : on ne retire que ceux-là
     *   'validate'   il reste des convertis non validés : on les valide
     *   'undo-all'   tout est validé, mais rien ne porte le drapeau. Le bouton
     *                reste actif pour que la bascule ne soit jamais morte, mais
     *                il dévaliderait des documents qu'il n'a pas validés :
     *                confirmation demandée.
     *   'idle'       aucun document converti : rien à faire.
     */
    const mode = () => {
        const posed = posedInBulk();
        if (posed.length) return { mode: 'undo-bulk', targets: posed };
        const converted = state.documents.filter(d => d.active_md_url);
        const pending = converted.filter(d => !(d.md_valid && d.metadata_valid));
        if (pending.length) return { mode: 'validate', targets: pending.map(d => d.source_name) };
        if (converted.length) return { mode: 'undo-all', targets: converted.map(d => d.source_name) };
        return { mode: 'idle', targets: [] };
    };

    const refresh = () => {
        const { mode: m, targets } = mode();
        const n = targets.length;
        const on = m === 'undo-bulk' || m === 'undo-all';
        btn.classList.toggle('on', on);
        btn.classList.toggle('off', !on);
        btn.dataset.state = on ? '1' : '0';
        btn.disabled = m === 'idle';
        btn.title = {
            'undo-bulk': `Retirer la validation posée en lot (${n} document${plur(n)}). Les documents validés un par un sont conservés.`,
            'validate': `Valider les ${n} document${plur(n)} convertis qui ne le sont pas encore`,
            'undo-all': `Dévalider les ${n} document${plur(n)} convertis. Aucun n'a été validé par ce bouton : confirmation demandée.`,
            'idle': 'Aucun document converti',
        }[m];
    };

    btn.addEventListener('click', async () => {
        const { mode: m, targets } = mode();
        if (!targets.length) { refresh(); return; }
        const next = m === 'validate';
        const n = targets.length;
        // Deux modes engagent l'utilisateur au-delà d'un simple aller-retour :
        // valider sans avoir relu, et dévalider ce que ce bouton n'a pas posé.
        // Le retrait d'une validation en lot, lui, ne fait qu'annuler l'action
        // précédente : il reste immédiat.
        const question = {
            'validate':
                `Valider les ${n} document${plur(n)} convertis ?\n\n`
                + `Ils seront marqués comme relus, Markdown et métadonnées, sans avoir été `
                + `ouverts dans le viewer. `
                + `Un second clic sur ce bouton annulera cette validation.`,
            'undo-all':
                `Dévalider les ${n} document${plur(n)} convertis ?\n\n`
                + `Aucun n'a été validé par ce bouton : ils ont été validés un par un, `
                + `ou en lot avant que cette annulation existe. Ils redeviendront non validés.`,
        }[m];
        if (question && !confirm(question)) return;

        setBtnLoading(btn, true);
        const failed = await setValidBulk(rag, targets, next);
        setBtnLoading(btn, false);
        if (failed.length) {
            showMessage('error', 'Validation partielle',
                `${failed.length} document${plur(failed.length)} sur ${targets.length} n'${failed.length > 1 ? 'ont' : 'a'} pas pu être traité${plur(failed.length)}.`);
        }
        await reloadFn();
    });

    return refresh;
}

/**
 * Étape 5 : relire chaque document dans le viewer, corriger, valider.
 *
 * C'est le seul endroit où l'on pose les deux drapeaux de relecture, et la
 * page ne parle donc plus de moteurs : convertir se fait à l'étape 2.
 */
async function initRevisionPage() {
    if (document.body.dataset.page !== 'revision') return;
    const rag = getQueryParam('corpus').trim();
    const nameEl = document.getElementById('revision-theme-name');
    const backLink = document.getElementById('revision-back-link');
    const listEl = document.getElementById('revision-list');
    const summaryEl = document.getElementById('revision-summary');
    const validateHead = document.getElementById('revision-validate-head');

    if (!rag) {
        showMessage('error', 'URL invalide', "Paramètre ?corpus=… manquant.");
        return;
    }
    if (backLink) backLink.href = `corpus.html?corpus=${encodeURIComponent(rag)}`;
    if (nameEl) nameEl.textContent = rag;

    const state = { rag, documents: [] };
    const reload = async () => {
        try {
            const resp = await fetch(`/api/corpus/documents?corpus=${encodeURIComponent(rag)}`);
            const data = await resp.json();
            if (!data || !data.ok) {
                listEl.innerHTML = `<p class="muted small">Impossible de charger : ${escapeHtml((data && data.error) || 'erreur inconnue')}</p>`;
                return;
            }
            state.documents = data.documents || [];
            if (validateHead) {
                const converted = state.documents.some(d => d.active_md_url);
                validateHead.classList.toggle('hidden', !converted);
            }
            if (summaryEl) summaryEl.innerHTML = renderConversionsSummary(state.documents, 'revision');
            listEl.innerHTML = renderConversionsList(state.documents, { rag, mode: 'revision' });
            bindConversionsRows(listEl, state, reload);
            rafraichirValidationLot();
        } catch (e) {
            listEl.innerHTML = `<p class="muted small">Erreur réseau : ${escapeHtml(String(e))}</p>`;
        }
    };
    const rafraichirValidationLot = bindBulkValidate(
        rag, state, document.getElementById('btn-validate-all'), reload);
    await reload();
}

/**
 * Une ligne de document, dans deux contextes.
 *
 * `ctx.mode = 'convert'` (page conversions) : choisir un moteur et lancer.
 * `ctx.mode = 'revision'` (page révision)   : relire, corriger, valider.
 *
 * L'identité du document (titre, nom de fichier, pages, taille, moteur de
 * référence) est la même des deux côtés ; seul le bas de ligne change.
 */
function renderConversionRow(d, idx, ctx) {
    const rag = ctx.rag;
    const mode = ctx.mode || 'convert';
    // Pages et taille se lisent comme une seule information : même rendu,
    // sans encadré.
    const size = d.size != null ? humanBytes(d.size) : '·';
    const pagesInfo = d.pages != null ? `${d.pages} p.` : '';
    const safeName = escapeHtml(d.source_name);
    // Quand le titre a été trouvé (métadonnées), il devient l'identité
    // affichée ; le nom de fichier passe en second plan. Sinon, le nom de
    // fichier reste seul.
    const title = (d.title || '').trim();
    const nameCell = title
        ? `<span class="source-name has-title" title="${escapeHtml(title)}">
               <span class="source-title">${escapeHtml(title)}</span>
               <span class="source-filename"><i class="fa-solid fa-file-pdf"></i> ${safeName}</span>
           </span>`
        : `<span class="source-name" title="${safeName}"><i class="fa-solid fa-file-pdf"></i> ${safeName}</span>`;
    const hasActive = !!d.active_id;
    const bothValid = !!(d.md_valid && d.metadata_valid);
    const anyValid = !!(d.md_valid || d.metadata_valid);
    const rowState = !hasActive
        ? 'is-pending-convert'
        : (bothValid ? 'is-valid' : (anyValid ? 'is-partial' : 'is-pending'));

    // La case ne sert qu'à la conversion groupée : en révision, on valide
    // avec le rond, et le rond d'en-tête vaut pour tous.
    const checkbox = mode === 'convert'
        ? `<input type="checkbox" class="source-check" data-doc-select="${escapeHtml(d.source_name)}" title="Sélectionner pour conversion groupée">`
        : '';

    // Le serveur continue de diagnostiquer le PDF (pages avec couche texte,
    // figures) et d'en déduire un moteur conseillé. La ligne ne l'affiche
    // plus : conseiller un moteur n'a plus de sens quand l'interface n'en
    // propose qu'un. Le diagnostic reste disponible dans la réponse de
    // /api/corpus/documents pour qui veut le relire.
    const diag = d.diagnostic || null;
    const conseil = diag && diag.moteur ? diag.moteur : null;
    const conseilLigne = '';

    // Moteur de la conversion de référence, affiché sous la taille. C'est le
    // moteur qui a produit le fichier existant, pas celui qu'un nouveau clic
    // sur « Convertir » emploierait : un document converti avant le passage
    // au moteur unique garde ici le nom de son moteur d'origine.
    const activeEngine = hasActive
        ? (d.conversions.find(c => c.id === d.active_id) || {}).engine
        : MOTEUR_CONVERSION;
    const activeEngineLabel = hasActive
        ? (ENGINE_LABELS[activeEngine] || activeEngine)
        : 'pas de conversion';
    // L'estimation vaut pour MOTEUR_CONVERSION, pas pour le moteur de la
    // conversion active : c'est le temps de ce que le bouton « Convertir »
    // va réellement lancer, qui peut différer de ce qui a produit le fichier
    // affiché à côté. Sans schéma verrouillé le serveur refuserait la
    // conversion : le bouton est grisé plutôt que de laisser partir un clic
    // vers un refus.
    const verrouille = ctx.schemaLocked !== false;
    const convertAttrs = verrouille
        ? 'title="Convertir ce document"'
        : 'disabled title="Verrouillez le schéma à l\'étape 2 pour convertir"';
    const engineSel = mode !== 'convert' ? '' : `
        <button type="button" class="btn primary primary-convert" data-convert-doc="${escapeHtml(d.source_name)}" ${convertAttrs}><i class="fa-solid fa-play"></i> Convertir</button>
        <span class="doc-eta muted small" data-doc-eta="${escapeHtml(d.source_name)}" data-doc-pages="${d.pages != null ? d.pages : ''}">${escapeHtml(formatDocEta(d.pages, MOTEUR_CONVERSION))}</span>`;

    // Le choix de la conversion de référence, la comparaison des moteurs et
    // la suppression d'une conversion vivent dans le viewer, au contact du
    // contenu. Rien à afficher ici.
    const historyBlock = '';

    // Un seul indicateur d'état : la conversion est validée (Markdown ET
    // métadonnées) ou elle ne l'est pas. Même granularité que la case
    // « Conversion validée » du viewer, qui est le seul endroit où l'on agit.
    const activeMdUrl = d.active_md_url;
    const validDot = mode !== 'revision' ? '' : (!activeMdUrl
        ? `<span class="valid-dot none" title="Pas encore converti"><i class="fa-regular fa-circle"></i></span>`
        : `<button type="button" class="valid-dot ${bothValid ? 'on' : 'off'}" data-valid-toggle="${escapeHtml(d.source_name)}" data-valid-state="${bothValid ? '1' : '0'}" aria-pressed="${bothValid}" title="${bothValid ? 'Document validé. Cliquer pour le dévalider.' : 'Document non validé. Cliquer pour le valider, ou ouvrir « Réviser » pour le relire d\'abord.'}"><i class="fa-solid fa-circle-check"></i></button>`);

    const viewerUrl = d.pdf_url && activeMdUrl
        ? `viewer.html?pdf=${encodeURIComponent(d.pdf_url)}&md=${encodeURIComponent(activeMdUrl)}`
        : null;
    let primaryBtn = '';
    if (mode === 'revision' && viewerUrl) {
        if (bothValid) {
            primaryBtn = `<a class="btn ghost primary-review" href="${escapeHtml(viewerUrl)}" title="Modifier"><i class="fa-solid fa-pen"></i> Modifier</a>`;
        } else {
            primaryBtn = `<a class="btn primary primary-review" href="${escapeHtml(viewerUrl)}" title="Éditer / valider"><i class="fa-solid fa-pen-to-square"></i> Réviser</a>`;
        }
    }
    if (mode === 'revision' && !activeMdUrl) {
        primaryBtn = `<span class="muted small">Pas de conversion : rien à relire.</span>`;
    }

    // En révision, tout tient sur une ligne : le bouton qui ouvre le viewer
    // et le rond de validation se lisent ensemble. En conversion, le choix du
    // moteur occupe une seconde ligne sous le document.
    const secondLine = mode === 'convert'
        ? `<div class="doc-convert-line">
            ${engineSel}
            <span class="doc-line-spacer"></span>
            ${primaryBtn}
        </div>
        ${conseilLigne}`
        : '';
    const inlineBtn = mode === 'convert' ? '' : primaryBtn;

    // En révision, le rond ouvre la ligne : c'est l'état du document qu'on
    // parcourt du regard en descendant la liste, pas son nom de fichier.
    return `
    <li class="source-row ${rowState}" data-source="${escapeHtml(d.source_name)}" data-source-idx="${idx}">
        <div class="source-line">
            <span class="source-check-cell">${mode === 'revision' ? validDot : checkbox}</span>
            ${nameCell}
            <span class="source-meta-cell muted small">
                <span class="source-meta-line">
                    <span class="source-pages" title="Nombre de pages">${escapeHtml(pagesInfo)}</span>
                    <span class="source-size">${escapeHtml(size)}</span>
                </span>
                <span class="source-engine" title="Moteur de la conversion de référence">${escapeHtml(activeEngineLabel)}</span>
            </span>
            ${inlineBtn}
        </div>
        ${secondLine}
    </li>`;
}

function formatDocEta(pages, engine) {
    if (!pages) return '';
    return `~${formatDuration(estimateSecondsForPages(pages, engine))}`;
}

/**
 * Recalcule l'estimation de durée d'une ligne.
 *
 * La ligne ne porte plus de menu : l'estimation vaut donc pour
 * `MOTEUR_CONVERSION`, le seul que « Convertir » puisse lancer. Le menu
 * reste interrogé s'il existe, pour que la fonction continue de servir si
 * on le remet un jour.
 */
function updateDocEta(container, sourceName) {
    const sel = container.querySelector(`select[data-doc-engine="${CSS.escape(sourceName)}"]`);
    const slot = container.querySelector(`[data-doc-eta="${CSS.escape(sourceName)}"]`);
    if (!slot) return;
    const pages = Number(slot.dataset.docPages);
    const engine = sel ? sel.value : MOTEUR_CONVERSION;
    slot.textContent = formatDocEta(Number.isFinite(pages) ? pages : 0, engine);
    slot.title = pages
        ? `Estimation pour ${pages} page${plur(pages)} avec ${ENGINE_LABELS[engine] || engine}`
        : '';
}

/* ---- Découpe d'un document : aperçu + réglage (points 2 et 3) ---- */

const DOC_SEGMENT_DEFAULTS = { target_chars: 1600, overlap: 200 };

/**
 * Ouvre le dialogue "Découpe" d'un document : segmente sa conversion active
 * en mémoire côté serveur (aucun run créé, aucun fichier écrit) et affiche
 * les chunks obtenus. Les deux champs permettent d'essayer d'autres valeurs
 * puis de les enregistrer pour ce document seulement.
 */
function bindConversionsRows(container, state, reloadFn) {
    // Validation directe depuis la ligne : le rond bascule les deux drapeaux
    // du sidecar d'un coup, comme la case « Conversion validée » du viewer.
    container.querySelectorAll('[data-valid-toggle]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const source = btn.dataset.validToggle;
            const next = btn.dataset.validState !== '1';
            btn.disabled = true;
            try {
                const resp = await fetch('/api/corpus/source-metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    // Écriture partielle : sans `fields`, les métadonnées du
                    // document ne sont pas touchées.
                    body: JSON.stringify({
                        corpus: state.rag, source,
                        metadata: { md_valid: next, metadata_valid: next },
                    }),
                });
                const data = await resp.json();
                if (!data || !data.ok) {
                    showMessage('error', 'Validation impossible',
                        (data && data.error) || 'Erreur inconnue');
                }
            } catch (e) {
                showMessage('error', 'Erreur réseau', String(e));
            }
            await reloadFn();
        });
    });

    // La ligne ne porte plus de panneau dépliant : relire et corriger se fait
    // dans le viewer (« Réviser »), où l'on a le PDF en regard.
    // L'estimation de durée est posée au rendu et ne bouge plus, faute de
    // menu à écouter : ce passage ne fait que lui donner son infobulle.
    container.querySelectorAll('[data-doc-eta]').forEach(slot => {
        updateDocEta(container, slot.dataset.docEta);
    });
    container.querySelectorAll('[data-doc-engine]').forEach(sel => {
        const source = sel.dataset.docEngine;
        sel.addEventListener('change', () => updateDocEta(container, source));
    });
    container.querySelectorAll('[data-convert-doc]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const source = btn.dataset.convertDoc;
            const sel = container.querySelector(`select[data-doc-engine="${CSS.escape(source)}"]`);
            const engine = sel ? sel.value : MOTEUR_CONVERSION;
            await launchDocConvert(state.rag, source, engine, btn, reloadFn);
        });
    });
}

async function postConvert(rag, source, engine, overwrite) {
    const resp = await fetch('/api/corpus/document/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ corpus: rag, source, engine, overwrite: !!overwrite }),
    });
    return await resp.json();
}

async function launchDocConvert(rag, source, engine, buttonEl, reloadFn) {
    setBtnLoading(buttonEl, true);
    const listEl = document.getElementById('conversions-list');
    const bulkBar = document.getElementById('conversions-bulk-bar');
    const pbHost = ensureExtractionProgressHost(bulkBar || listEl);
    const engineLabel = ENGINE_LABELS[engine] || engine;
    const pb = renderProgressBar(pbHost, `Conversion de ${source} via ${engineLabel}…`);
    try {
        let start = await postConvert(rag, source, engine, false);
        if (start && start.schema_locked === false) {
            pb.setState('error', '✗ schéma non verrouillé');
            showMessage('error', 'Schéma non verrouillé', SCHEMA_AVANT_CONVERSION);
            return;
        }
        // 409 : une conversion existe déjà pour ce moteur. L'écraser détruit
        // les corrections manuelles faites dessus, donc on demande d'abord.
        if (start && start.exists) {
            const ok = confirm(`${source}\n\nUne conversion ${engineLabel} existe déjà. La relancer remplacera ce fichier et les corrections manuelles qu'il contient.\n\nContinuer ?`);
            if (!ok) {
                pb.setState('done', 'Conversion annulée');
                return;
            }
            start = await postConvert(rag, source, engine, true);
        }
        if (!start || !start.ok || !start.job_id) {
            pb.setState('error', `✗ ${(start && start.error) || 'erreur inconnue'}`);
            return;
        }
        const finalJob = await pollJob(start.job_id, ({ processed, total, current, ratio }) => {
            pb.update({ processed, total, current, ratio });
        });
        if (finalJob.state === 'done' && finalJob.result && finalJob.result.ok) {
            pb.setState('done', `✓ conversion ${finalJob.result.conversion_id} enregistrée`);
        } else {
            const err = (finalJob.result && finalJob.result.error) || finalJob.error || 'Erreur inconnue';
            pb.setState('error', `✗ ${escapeHtml(err)}`);
            showMessage('error', 'Échec conversion', err);
        }
        if (typeof reloadFn === 'function') await reloadFn();
    } catch (e) {
        pb.setState('error', `✗ ${escapeHtml(String(e))}`);
        showMessage('error', 'Erreur réseau', String(e));
    } finally {
        setBtnLoading(buttonEl, false);
    }
}

async function launchBulkConversions(rag, sources, engine, buttonEl, reloadFn) {
    if (!sources || !sources.length) return;
    setBtnLoading(buttonEl, true);
    const listEl = document.getElementById('conversions-list');
    const bulkBar = document.getElementById('conversions-bulk-bar');
    const pbHost = ensureExtractionProgressHost(bulkBar || listEl);
    const engineLabel = ENGINE_LABELS[engine] || engine;
    const pb = renderProgressBar(pbHost, `Conversion de ${sources.length} document${plur(sources.length)} via ${engineLabel}…`);
    let done = 0;
    let errors = 0;
    let skipped = 0;
    // Décision d'écrasement prise une seule fois pour tout le lot : null tant
    // qu'aucune conversion existante n'a été rencontrée.
    let overwriteAll = null;
    try {
        for (const source of sources) {
            pb.update({ processed: done, total: sources.length, current: source });
            try {
                let start = await postConvert(rag, source, engine, overwriteAll === true);
                if (start && start.schema_locked === false) {
                    pb.setState('error', '✗ schéma non verrouillé');
                    showMessage('error', 'Schéma non verrouillé', SCHEMA_AVANT_CONVERSION);
                    return;
                }
                if (start && start.exists) {
                    if (overwriteAll === null) {
                        overwriteAll = confirm(`Certains documents sélectionnés ont déjà une conversion ${engineLabel}.\n\nLa relancer remplacera ces fichiers et les corrections manuelles qu'ils contiennent.\n\nÉcraser les conversions existantes ?`);
                    }
                    if (overwriteAll) {
                        start = await postConvert(rag, source, engine, true);
                    } else {
                        skipped++;
                        done++;
                        continue;
                    }
                }
                if (start && start.ok && start.job_id) {
                    const finalJob = await pollJob(start.job_id, ({ processed, total, current, ratio }) => {
                        pb.update({ processed: done, total: sources.length, current: source });
                    });
                    if (!finalJob || finalJob.state !== 'done' || !finalJob.result || !finalJob.result.ok) {
                        errors++;
                    }
                } else {
                    errors++;
                }
            } catch (_) { errors++; }
            done++;
        }
        pb.update({ processed: done, total: sources.length, current: null });
        const skippedPart = skipped ? `, ${skipped} ignoré${plur(skipped)}` : '';
        if (errors === 0) {
            pb.setState('done', `✓ ${done - skipped}/${sources.length} converti${plur(done - skipped)}${skippedPart}`);
        } else {
            pb.setState('error', `${done - errors - skipped}/${sources.length} OK, ${errors} en erreur${skippedPart}`);
        }
        if (typeof reloadFn === 'function') await reloadFn();
    } finally {
        setBtnLoading(buttonEl, false);
    }
}

function ensureExtractionProgressHost(anchor) {
    if (!anchor) return document.body;
    let host = anchor.parentElement
        ? anchor.parentElement.querySelector(':scope > .extraction-progress-host')
        : null;
    if (!host) {
        host = document.createElement('div');
        host.className = 'extraction-progress-host convert-progress-host';
        if (anchor.parentElement) anchor.parentElement.insertBefore(host, anchor.nextSibling);
    }
    return host;
}

/**
 * Configuration : une modale ouverte par l'engrenage de la colonne de gauche.
 *
 * C'etait une page a part, atteinte depuis un accueil qui n'existe plus. Une
 * cle d'API se saisit une fois puis s'oublie : elle ne meritait pas une
 * destination, seulement un acces depuis n'importe ou.
 */
function monterModaleConfig() {
    if (document.getElementById('dlg-config')) return;
    const dlg = document.createElement('dialog');
    dlg.className = 'app-modal';
    dlg.id = 'dlg-config';
    // Meme charpente que les modales d'aide : un `header` qui porte le titre
    // et la fermeture a droite, puis `modal-body`. L'ancienne version posait
    // la croix seule en haut a GAUCHE, avant un `h2` que le style de la
    // modale ne connaissait pas.
    dlg.innerHTML = `
        <header>
            <h3>Configuration</h3>
            <button type="button" data-close-dialog aria-label="Fermer">
                <i class="fa-solid fa-xmark" aria-hidden="true"></i>
            </button>
        </header>
        <div class="modal-body">
            <form id="config-form" class="config-form">
                <label class="upload-field">
                    <span>Clé Albert (etalab)</span>
                    <input type="password" name="albert_api_key" placeholder="sk-…" autocomplete="off">
                </label>
                <p class="muted small" id="config-hint">Chargement de l'état actuel…</p>
                <p class="muted small">Écrite dans <code>data/config.json</code>, elle
                n'est jamais renvoyée au navigateur : seul le serveur la lit pour
                appeler Albert. Un champ vide laisse la clé actuelle en place.</p>
                <p class="muted small">Pour l'obtenir : <code>albert.api.etalab.gouv.fr</code>,
                connexion ProConnect, puis <em>Mes clés</em> → <em>Générer une clé</em>.</p>
                <div class="modal-actions">
                    <span class="muted small" id="config-statut"></span>
                    <button type="submit" class="btn primary">
                        <i class="fa-solid fa-floppy-disk"></i> Enregistrer
                    </button>
                </div>
            </form>
        </div>`;
    document.body.appendChild(dlg);

    const bouton = document.getElementById('btn-config');
    if (bouton) bouton.addEventListener('click', () => { rafraichirEtatCle(); dlg.showModal(); });

    dlg.querySelector('#config-form').addEventListener('submit', async event => {
        event.preventDefault();
        const champ = dlg.querySelector('[name="albert_api_key"]');
        const statut = dlg.querySelector('#config-statut');
        const cle = (champ.value || '').trim();
        if (!cle) {
            statut.textContent = 'Saisissez une clé, ou fermez la fenêtre.';
            statut.classList.add('error');
            return;
        }
        statut.classList.remove('error');
        statut.textContent = 'Enregistrement…';
        try {
            const resp = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ albert_api_key: cle }),
            });
            const data = await resp.json();
            if (data.ok) {
                champ.value = '';
                statut.textContent = '';
                await rafraichirEtatCle();
                dlg.close();
            } else {
                statut.textContent = data.error || 'Échec inconnu';
                statut.classList.add('error');
            }
        } catch (e) {
            statut.textContent = String(e);
            statut.classList.add('error');
        }
    });
}

/** Etat de la cle, relu a chaque ouverture : elle a pu changer ailleurs. */
async function rafraichirEtatCle() {
    const hint = document.getElementById('config-hint');
    if (!hint) return;
    try {
        const data = await (await fetch('/api/config')).json();
        hint.innerHTML = data.albert_api_key_set
            ? `Clé Albert <strong>configurée</strong> (${escapeHtml(data.albert_api_key_hint)}).`
            : 'Clé Albert <strong>non configurée</strong> : la conversion Vision et le '
              + 'remplissage des champs resteront indisponibles.';
    } catch (_) {
        hint.textContent = 'État de la clé indisponible.';
    }
}

/* ============ COLLECTIONS page ============ */

function initGenericDialogs() {
    document.querySelectorAll('[data-open-dialog]').forEach(btn => {
        btn.addEventListener('click', event => {
            event.preventDefault();
            const targetId = btn.dataset.openDialog;
            const dlg = document.getElementById(targetId);
            if (!dlg) return;
            if (typeof dlg.showModal === 'function') dlg.showModal();
            else dlg.setAttribute('open', 'open');
        });
    });
    document.querySelectorAll('dialog').forEach(dlg => {
        dlg.querySelectorAll('[data-close-dialog]').forEach(el => {
            el.addEventListener('click', () => {
                if (typeof dlg.close === 'function') dlg.close();
                else dlg.removeAttribute('open');
            });
        });
        dlg.addEventListener('click', event => {
            if (event.target === dlg) {
                if (typeof dlg.close === 'function') dlg.close();
                else dlg.removeAttribute('open');
            }
        });
    });
}

/* ============ INIT ============ */


document.addEventListener('DOMContentLoaded', () => {
    monterRail();
    monterBlocForge();
    monterVersions();
    verifierMiseAJour();
    reprendreLesTaches();
    garderContreLaFermeture();
    monterModaleConfig();
    initRagTopicsPage();
    initRagThemePage();
    initConversionsPage();
    initImportPage();
    initRevisionPage();
    initGenericDialogs();
});
