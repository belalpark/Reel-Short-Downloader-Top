/* One CSV escaping policy for automatic and manual exports. */
((root) => {
  'use strict';
  function csvEscape(value) {
    let text = String(value ?? '');
    // Quoting alone does not stop spreadsheet formula execution.
    if (/^[\s\uFEFF]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function safeName(title) {
    let name = String(title || 'Series').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0,55).replace(/[.\s]+$/g, '') || 'Series';
    if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(name)) name = '_' + name;
    return name;
  }
  root.DramaExportCore = Object.freeze({csvEscape, safeName});
})(globalThis);
