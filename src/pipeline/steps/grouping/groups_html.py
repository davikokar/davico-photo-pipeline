"""
groups_html — generates a self-contained interactive HTML review page.

The page embeds all thumbnails as base64 and provides:
  - Visual overview of all detected groups with thumbnails
  - Drag-and-drop of brackets between groups
  - Group type editing (dropdown)
  - New group creation
  - Group deletion
  - Break a bracket into individual shots (for misdetected HDR sequences)
  - Extract a bracket to a new group inserted immediately below
  - Export to JSON (downloads a new groups_NNN.json file)

The exported JSON follows the same format as groups_io.py and can be
placed directly in the session directory to be picked up by the pipeline.
"""

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

THUMB_HEIGHT = 110   # px


# ---------------------------------------------------------------------------
# Thumbnail helper
# ---------------------------------------------------------------------------

def _b64_thumb(img_path: Path, height: int = THUMB_HEIGHT) -> str:
    """Return a base64 data-URI thumbnail, or a grey placeholder on error."""
    try:
        img = Image.open(img_path).convert("RGB")
        ratio = img.width / img.height
        img = img.resize((int(height * ratio), height), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=70)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"Thumbnail failed for {img_path.name}: {e}")
        return _placeholder_b64(height)


def _placeholder_b64(height: int) -> str:
    """1×1 grey pixel scaled up as placeholder."""
    img = Image.new("RGB", (int(height * 1.5), height), color=(45, 45, 45))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_review_html(
    groups_data: list[dict],
    input_dir: Path,
    output_path: Path,
    session_id: str,
    next_version: int,
) -> Path:
    """
    Generate a self-contained HTML review page.

    Args:
        groups_data:  List of group dicts (from groups_io.panorama_groups_to_json).
        input_dir:    Directory containing the source JPEG files.
        output_path:  Where to write the HTML file.
        session_id:   Session identifier (embedded in exported JSON).
        next_version: The version number the exported JSON should carry.

    Returns:
        Path to the written HTML file.
    """
    input_dir   = Path(input_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build thumbnail map: filename → base64 URI
    logger.info("Generating thumbnails for HTML review page...")
    all_filenames = [
        shot["filename"]
        for g in groups_data
        for b in g["brackets"]
        for shot in b["shots"]
    ]
    thumb_map = {}
    for fn in all_filenames:
        p = input_dir / fn
        thumb_map[fn] = _b64_thumb(p)
    logger.info(f"Generated {len(thumb_map)} thumbnails")

    # Embed the initial groups state and thumbnails as JSON in the HTML
    groups_json_str  = json.dumps(groups_data, ensure_ascii=False)
    thumb_map_str    = json.dumps(thumb_map, ensure_ascii=False)
    next_version_str = f"{next_version:03d}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Group Review — {session_id}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:        #0d0d0d;
    --surface:   #171717;
    --surface2:  #1f1f1f;
    --border:    #2a2a2a;
    --accent:    #e8c547;
    --accent2:   #c47a3d;
    --text:      #e0ddd8;
    --muted:     #666;
    --danger:    #c0392b;
    --success:   #27ae60;
    --mono:      'DM Mono', monospace;
    --sans:      'Syne', sans-serif;
    --radius:    6px;
  }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
  }}

  /* ── Header ── */
  header {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(13,13,13,0.92);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 14px 28px;
    display: flex; align-items: center; gap: 20px;
  }}
  header h1 {{
    font-size: 15px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--accent);
    flex: 1;
  }}
  header .session-id {{
    font-family: var(--mono); font-size: 11px; color: var(--muted);
  }}

  /* ── Toolbar ── */
  .toolbar {{
    display: flex; gap: 10px;
  }}
  button {{
    font-family: var(--mono); font-size: 12px;
    border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface2); color: var(--text);
    padding: 7px 16px; cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.04em;
  }}
  button:hover {{ border-color: var(--accent); color: var(--accent); }}
  button.primary {{
    background: var(--accent); color: #000; border-color: var(--accent);
    font-weight: 500;
  }}
  button.primary:hover {{ background: #f5d66a; border-color: #f5d66a; }}
  button.danger {{ border-color: var(--danger); color: var(--danger); }}
  button.danger:hover {{ background: var(--danger); color: #fff; }}

  /* ── Main layout ── */
  main {{
    padding: 28px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }}

  /* ── Group card ── */
  .group-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    transition: border-color 0.2s;
  }}
  .group-card.drag-over {{
    border-color: var(--accent);
    box-shadow: 0 0 0 2px rgba(232,197,71,0.25);
  }}

  .group-header {{
    display: flex; align-items: center; gap: 14px;
    padding: 12px 16px;
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
  }}
  .group-id {{
    font-family: var(--mono); font-size: 13px; font-weight: 500;
    color: var(--accent); min-width: 90px;
  }}
  .group-type-select {{
    font-family: var(--mono); font-size: 11px;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 4px 8px; cursor: pointer;
    outline: none;
  }}
  .group-type-select:focus {{ border-color: var(--accent); }}
  .group-meta {{
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    margin-left: auto;
  }}
  .btn-delete-group {{
    padding: 4px 10px; font-size: 11px;
    border-color: transparent; color: var(--muted);
  }}
  .btn-delete-group:hover {{ border-color: var(--danger); color: var(--danger); background: transparent; }}

  /* ── Brackets container ── */
  .brackets-container {{
    display: flex; flex-wrap: wrap;
    gap: 10px; padding: 14px 16px;
    min-height: 80px;
  }}

  /* ── Bracket card (draggable unit) ── */
  .bracket-card {{
    display: flex; flex-direction: column;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    cursor: grab;
    transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
    user-select: none;
    background: var(--bg);
  }}
  .bracket-card:hover {{
    border-color: #444;
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.5);
  }}
  .bracket-card.dragging {{
    opacity: 0.35;
    cursor: grabbing;
  }}

  /* ── Bracket action buttons (visible on hover, top-right corner) ── */
  .bracket-card {{
    position: relative;
  }}
  .bracket-actions {{
    display: none;
    position: absolute; top: 5px; right: 5px;
    flex-direction: row; gap: 4px;
    z-index: 10;
  }}
  .bracket-card:hover .bracket-actions {{
    display: flex;
  }}
  .btn-break,
  .btn-extract {{
    font-family: var(--mono); font-size: 10px;
    background: rgba(13,13,13,0.88);
    border-radius: 3px;
    padding: 2px 7px;
    cursor: pointer;
    letter-spacing: 0.03em;
    transition: background 0.12s, color 0.12s;
  }}
  .btn-break {{
    border: 1px solid var(--accent2);
    color: var(--accent2);
  }}
  .btn-break:hover {{
    background: var(--accent2); color: #fff;
  }}
  .btn-extract {{
    border: 1px solid #5b9bd5;
    color: #5b9bd5;
  }}
  .btn-extract:hover {{
    background: #5b9bd5; color: #fff;
  }}

  .bracket-shots {{
    display: flex;
  }}
  .shot-thumb {{
    display: flex; flex-direction: column;
    border-right: 1px solid var(--border);
    position: relative;
  }}
  .shot-thumb:last-child {{ border-right: none; }}
  .shot-thumb img {{
    display: block;
    height: {THUMB_HEIGHT}px;
    width: auto;
    pointer-events: none;
  }}
  .shot-ev {{
    position: absolute; top: 4px; left: 4px;
    font-family: var(--mono); font-size: 10px;
    background: rgba(0,0,0,0.75);
    color: var(--accent);
    padding: 2px 5px; border-radius: 3px;
    pointer-events: none;
  }}

  .bracket-footer {{
    padding: 5px 8px;
    font-family: var(--mono); font-size: 10px;
    color: var(--muted);
    border-top: 1px solid var(--border);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 280px;
  }}

  /* ── Empty drop zone hint ── */
  .drop-hint {{
    width: 100%; padding: 20px;
    text-align: center;
    font-family: var(--mono); font-size: 12px; color: var(--muted);
    border: 1px dashed var(--border);
    border-radius: 6px;
  }}

  /* ── Toast ── */
  #toast {{
    position: fixed; bottom: 28px; right: 28px;
    background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); font-family: var(--mono); font-size: 12px;
    padding: 12px 20px; border-radius: var(--radius);
    opacity: 0; transform: translateY(8px);
    transition: all 0.25s;
    pointer-events: none;
    z-index: 999;
  }}
  #toast.show {{
    opacity: 1; transform: translateY(0);
  }}
  #toast.ok {{ border-color: var(--success); color: var(--success); }}
  #toast.err {{ border-color: var(--danger); color: var(--danger); }}
</style>
</head>
<body>

<header>
  <h1>⬡ Group Review</h1>
  <span class="session-id">{session_id}</span>
  <div class="toolbar">
    <button onclick="addGroup()">+ New Group</button>
    <button class="primary" onclick="exportJSON()">↓ Export groups_{next_version_str}.json</button>
  </div>
</header>

<main id="groups-root"></main>
<div id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────

const THUMBS    = {thumb_map_str};
const SESSION   = {json.dumps(session_id)};
const INPUT_DIR = {json.dumps(str(input_dir))};
const NEXT_VER  = "{next_version_str}";

let groups = {groups_json_str};

let dragState = null;  // {{ bracketEl, sourceGroupId, bracketIndex }}


// ── Rendering ─────────────────────────────────────────────────────────────

function render() {{
  const root = document.getElementById('groups-root');
  root.innerHTML = '';
  groups.forEach((g, gi) => root.appendChild(buildGroupCard(g, gi)));
}}

function buildGroupCard(g, gi) {{
  const card = el('div', 'group-card');
  card.dataset.groupId = g.id;

  // Header
  const header = el('div', 'group-header');
  const idSpan = el('span', 'group-id');
  idSpan.textContent = g.id;
  header.appendChild(idSpan);

  const typeSelect = el('select', 'group-type-select');
  ['single','hdr','panorama','hdr+panorama'].forEach(t => {{
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = t;
    if (t === g.type) opt.selected = true;
    typeSelect.appendChild(opt);
  }});
  typeSelect.addEventListener('change', e => {{
    groups[gi].type = e.target.value;
    updateMeta(header, g);
  }});
  header.appendChild(typeSelect);

  const meta = el('span', 'group-meta');
  meta.textContent = metaText(g);
  header.appendChild(meta);

  const delBtn = el('button', 'btn-delete-group');
  delBtn.textContent = '✕ remove';
  delBtn.addEventListener('click', () => removeGroup(gi));
  header.appendChild(delBtn);
  card.appendChild(header);

  // Brackets
  const container = el('div', 'brackets-container');
  container.dataset.groupIdx = gi;

  if (g.brackets.length === 0) {{
    const hint = el('div', 'drop-hint');
    hint.textContent = 'drop brackets here';
    container.appendChild(hint);
  }} else {{
    g.brackets.forEach((b, bi) => container.appendChild(buildBracketCard(b, gi, bi)));
  }}

  // Drag-over events on the container
  container.addEventListener('dragover', e => {{
    e.preventDefault();
    card.classList.add('drag-over');
  }});
  container.addEventListener('dragleave', e => {{
    if (!card.contains(e.relatedTarget)) card.classList.remove('drag-over');
  }});
  container.addEventListener('drop', e => {{
    e.preventDefault();
    card.classList.remove('drag-over');
    if (!dragState) return;
    const targetGroupIdx = parseInt(container.dataset.groupIdx);
    moveBracket(dragState.sourceGroupIdx, dragState.bracketIdx, targetGroupIdx);
  }});

  card.appendChild(container);
  return card;
}}

function buildBracketCard(bracket, groupIdx, bracketIdx) {{
  const card = el('div', 'bracket-card');
  card.draggable = true;
  card.dataset.groupIdx   = groupIdx;
  card.dataset.bracketIdx = bracketIdx;

  const shotsRow = el('div', 'bracket-shots');
  bracket.shots.forEach(shot => {{
    const thumb = el('div', 'shot-thumb');
    const img = document.createElement('img');
    img.src = THUMBS[shot.filename] || '';
    img.title = shot.filename;
    thumb.appendChild(img);
    if (shot.ev !== null && shot.ev !== undefined) {{
      const evBadge = el('span', 'shot-ev');
      evBadge.textContent = (shot.ev >= 0 ? '+' : '') + shot.ev.toFixed(1);
      thumb.appendChild(evBadge);
    }}
    shotsRow.appendChild(thumb);
  }});
  card.appendChild(shotsRow);

  // Action buttons (break + extract) — shown on hover
  const actions = el('div', 'bracket-actions');

  // "→ group": always shown — moves this bracket into a new group inserted below
  const extractBtn = el('button', 'btn-extract');
  extractBtn.textContent = '→ group';
  extractBtn.title = 'Move to new group (inserted below)';
  extractBtn.addEventListener('click', e => {{
    e.stopPropagation();
    extractToNewGroup(groupIdx, bracketIdx);
  }});
  actions.appendChild(extractBtn);

  // "⋯ break": only on multi-shot brackets — splits into individual shots
  if (bracket.shots.length > 1) {{
    const breakBtn = el('button', 'btn-break');
    breakBtn.textContent = '⋯ break';
    breakBtn.title = 'Split into individual shots';
    breakBtn.addEventListener('click', e => {{
      e.stopPropagation();
      breakBracket(groupIdx, bracketIdx);
    }});
    actions.appendChild(breakBtn);
  }}

  card.appendChild(actions);

  const footer = el('div', 'bracket-footer');
  const names = bracket.shots.map(s => s.filename).join('  ');
  footer.textContent = names;
  footer.title = names;
  card.appendChild(footer);

  card.addEventListener('dragstart', e => {{
    dragState = {{ sourceGroupIdx: groupIdx, bracketIdx }};
    setTimeout(() => card.classList.add('dragging'), 0);
  }});
  card.addEventListener('dragend', () => {{
    card.classList.remove('dragging');
    dragState = null;
  }});

  return card;
}}

function metaText(g) {{
  const shots = g.brackets.reduce((n, b) => n + b.shots.length, 0);
  return `${{g.brackets.length}} bracket${{g.brackets.length !== 1 ? 's' : ''}} · ${{shots}} shot${{shots !== 1 ? 's' : ''}}`;
}}

function updateMeta(headerEl, g) {{
  headerEl.querySelector('.group-meta').textContent = metaText(g);
}}


// ── Actions ───────────────────────────────────────────────────────────────

function moveBracket(fromGroupIdx, bracketIdx, toGroupIdx) {{
  if (fromGroupIdx === toGroupIdx) return;
  const bracket = groups[fromGroupIdx].brackets.splice(bracketIdx, 1)[0];
  groups[toGroupIdx].brackets.push(bracket);
  // Auto-update group type based on bracket count + EV spread
  autoType(fromGroupIdx);
  autoType(toGroupIdx);
  render();
  toast('Bracket moved', 'ok');
}}

function breakBracket(groupIdx, bracketIdx) {{
  const bracket = groups[groupIdx].brackets[bracketIdx];
  if (bracket.shots.length <= 1) return;

  // Replace the bracket with N single-shot brackets, in place
  const singles = bracket.shots.map(shot => ({{ shots: [shot] }}));
  groups[groupIdx].brackets.splice(bracketIdx, 1, ...singles);

  autoType(groupIdx);
  render();
  toast(`Bracket split into ${{singles.length}} individual shots`, 'ok');
}}

function extractToNewGroup(groupIdx, bracketIdx) {{
  // Remove bracket from source group
  const bracket = groups[groupIdx].brackets.splice(bracketIdx, 1)[0];

  // Build the new group (inserted immediately after source group)
  const newGroup = {{
    id:       '__new__',   // placeholder, renumbered below
    type:     'single',
    brackets: [bracket],
  }};
  groups.splice(groupIdx + 1, 0, newGroup);

  // Update types and renumber all IDs
  autoType(groupIdx);
  autoType(groupIdx + 1);
  renumberGroups();

  render();
  toast(`Bracket extracted → ${{groups[groupIdx + 1].id}}`, 'ok');
}}

function renumberGroups() {{
  groups.forEach((g, i) => {{
    g.id = 'group_' + String(i + 1).padStart(3, '0');
  }});
}}

function autoType(groupIdx) {{
  const g = groups[groupIdx];
  if (g.brackets.length === 0) return;
  const isPano = g.brackets.length > 1;
  const isHdr  = g.brackets.some(b => {{
    const evs = b.shots.map(s => s.ev).filter(e => e !== null && e !== undefined);
    if (evs.length < 2) return false;
    return Math.max(...evs) - Math.min(...evs) >= 0.8;
  }});
  if (isPano && isHdr)  g.type = 'hdr+panorama';
  else if (isPano)      g.type = 'panorama';
  else if (isHdr)       g.type = 'hdr';
  else                  g.type = 'single';
}}

function addGroup() {{
  const newId = 'group_' + String(groups.length + 1).padStart(3, '0');
  groups.push({{ id: newId, type: 'single', brackets: [] }});
  render();
  toast('New group added', 'ok');
}}

function removeGroup(groupIdx) {{
  const g = groups[groupIdx];
  if (g.brackets.length > 0) {{
    if (!confirm(`Remove ${{g.id}} and its ${{g.brackets.length}} bracket(s)?`)) return;
  }}
  groups.splice(groupIdx, 1);
  // Renumber IDs
  groups.forEach((grp, i) => grp.id = 'group_' + String(i + 1).padStart(3, '0'));
  render();
  toast('Group removed', 'ok');
}}


// ── Export ────────────────────────────────────────────────────────────────

function exportJSON() {{
  // Remove empty groups
  const toExport = groups.filter(g => g.brackets.length > 0);
  if (toExport.length === 0) {{
    toast('No groups to export', 'err');
    return;
  }}

  const payload = {{
    version:      1,
    session_id:   SESSION,
    input_dir:    INPUT_DIR,
    generated_at: new Date().toISOString(),
    groups:       toExport,
  }};

  const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `groups_${{NEXT_VER}}.json`;
  a.click();
  URL.revokeObjectURL(url);
  toast(`Exported groups_${{NEXT_VER}}.json — place it in your session directory`, 'ok');
}}


// ── Utilities ─────────────────────────────────────────────────────────────

function el(tag, cls) {{
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}}

let toastTimer;
function toast(msg, type = '') {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show' + (type ? ' ' + type : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = '', 3200);
}}


// ── Init ──────────────────────────────────────────────────────────────────

render();
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    logger.info(f"HTML review page → {output_path.name}")
    return output_path
