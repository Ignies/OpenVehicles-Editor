// Stroked line icons, 24 px grid.
const svg = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const ICONS = {
  pencil: svg('<path d="M4 20l4.2-1 10.6-10.6a2 2 0 0 0 0-2.8l-.4-.4a2 2 0 0 0-2.8 0L5 15.8 4 20z"/><path d="M13.5 6.5l4 4"/>'),
  eraser: svg('<path d="M8.5 19.5h11"/><path d="M5 15.2l9.2-9.2a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L12 18.2a2 2 0 0 1-1.4.6H8.3a2 2 0 0 1-1.4-.6L5 16.6a1 1 0 0 1 0-1.4z"/><path d="M9.5 10.7l4.8 4.8"/>'),
  fill: svg('<path d="M5.5 11.5l6-6 7 7-6 6a1.5 1.5 0 0 1-2.1 0l-4.9-4.9a1.5 1.5 0 0 1 0-2.1z"/><path d="M8.5 8.5L5 5"/><path d="M5.6 12h13"/><path d="M20.5 15.5s-1.5 1.8-1.5 2.9a1.5 1.5 0 0 0 3 0c0-1.1-1.5-2.9-1.5-2.9z"/>'),
  line: svg('<path d="M5 19L19 5"/><circle cx="5" cy="19" r="1.4"/><circle cx="19" cy="5" r="1.4"/>'),
  rect: svg('<rect x="4.5" y="6" width="15" height="12" rx="1.5"/>'),
  ellipse: svg('<ellipse cx="12" cy="12" rx="8" ry="6"/>'),
  slice: svg('<rect x="5.5" y="5.5" width="13" height="13" rx="2" opacity=".45"/><path d="M2.5 12h19"/>'),
  brush: svg('<path d="M14.5 4.5l5 5-7.2 7.2-5-5 7.2-7.2z"/><path d="M7.3 11.7l-1.6 1.6a2.5 2.5 0 0 0 0 3.5L4.5 19.5h4.3a2.5 2.5 0 0 0 1.7-.7l1.8-1.8"/>'),
  pull: svg('<rect x="4.5" y="4.5" width="9" height="9" rx="1.5" opacity=".45"/><rect x="10.5" y="10.5" width="9" height="9" rx="1.5"/><path d="M9 9l4.5 4.5"/><path d="M13.5 10.5v3h-3"/>'),
  carry: svg('<rect x="4.5" y="4.5" width="9" height="9" rx="1.5"/><rect x="10.5" y="10.5" width="9" height="9" rx="1.5" opacity=".45"/><path d="M11 11l5 5"/><path d="M16 13v3h-3"/>'),
  onion: svg('<rect x="3.5" y="7.5" width="10" height="10" rx="2" opacity=".35"/><rect x="10.5" y="6.5" width="10" height="10" rx="2" opacity=".35"/><rect x="7" y="5" width="10" height="13" rx="2"/>'),
  depth: svg('<path d="M12 4l8 4-8 4-8-4 8-4z"/><path d="M4 12l8 4 8-4"/><path d="M4 16l8 4 8-4"/>'),
  picker: svg('<path d="M14.5 5.5l4 4"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3l-2 2-3-3 2-2z"/><path d="M14.5 7.5L6 16v3h3l8.5-8.5"/>'),
  mirror: svg('<path d="M12 3v18" stroke-dasharray="2 2.5"/><path d="M8.5 7L4 17h4.5V7z"/><path d="M15.5 7L20 17h-4.5V7z"/>'),
  eye: svg('<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.6"/>'),
  depthview: svg('<path d="M4 18h16"/><path d="M6 14h12" opacity=".7"/><path d="M8 10h8" opacity=".45"/><path d="M10 6h4" opacity=".25"/>'),
  ghost: svg('<rect x="3.5" y="7.5" width="11" height="11" rx="2" opacity=".45"/><rect x="9.5" y="4.5" width="11" height="11" rx="2"/>'),
  lock: svg('<rect x="5.5" y="10.5" width="13" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>'),
  unlock: svg('<rect x="5.5" y="10.5" width="13" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2"/>'),
  eyeoff: svg('<path d="M3 3l18 18"/><path d="M10.6 5.6A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.8 3.6"/><path d="M6.3 6.9C3.9 8.6 2.5 12 2.5 12s3.5 6.5 9.5 6.5c1.6 0 3-.4 4.2-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
  grip: svg('<circle cx="9" cy="7" r="1" fill="currentColor"/><circle cx="15" cy="7" r="1" fill="currentColor"/><circle cx="9" cy="12" r="1" fill="currentColor"/><circle cx="15" cy="12" r="1" fill="currentColor"/><circle cx="9" cy="17" r="1" fill="currentColor"/><circle cx="15" cy="17" r="1" fill="currentColor"/>'),
  copy: svg('<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>'),
  trash: svg('<path d="M5 7h14"/><path d="M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7"/><path d="M6.5 7l.8 11.2A1.5 1.5 0 0 0 8.8 19.6h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/>'),
  undo: svg('<path d="M9 14L4.5 9.5 9 5"/><path d="M4.5 9.5H14a5.5 5.5 0 0 1 0 11h-3"/>'),
  redo: svg('<path d="M15 14l4.5-4.5L15 5"/><path d="M19.5 9.5H10a5.5 5.5 0 0 0 0 11h3"/>'),
  play: svg('<path d="M7 5.5v13l11-6.5-11-6.5z" fill="currentColor" stroke="none"/>'),
  pause: svg('<rect x="6.5" y="5.5" width="3.6" height="13" rx="1" fill="currentColor" stroke="none"/><rect x="13.9" y="5.5" width="3.6" height="13" rx="1" fill="currentColor" stroke="none"/>'),
  frame: svg('<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8"/><path d="M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8"/><path d="M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16"/><path d="M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/>'),
  download: svg('<path d="M12 4v11"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M5 19.5h14"/>'),
  spark: svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z"/>'),
  write: svg('<path d="M4 20h4.5"/><path d="M8.5 20l10.3-10.3a2 2 0 0 0 0-2.8l-1.7-1.7a2 2 0 0 0-2.8 0L4 15.5V20h4.5z"/><path d="M12.5 7.5l4 4"/>'),
  folder: svg('<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v7.8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5v-10z"/>'),
  minimize: svg('<path d="M5 12h14"/>'),
  maximize: svg('<rect x="5.5" y="5.5" width="13" height="13" rx="1.5"/>'),
  restore: svg('<rect x="5" y="8.5" width="10.5" height="10.5" rx="1.5"/><path d="M8.5 8.5V6.5A1.5 1.5 0 0 1 10 5h7.5A1.5 1.5 0 0 1 19 6.5V14a1.5 1.5 0 0 1-1.5 1.5h-2"/>'),
  close: svg('<path d="M6 6l12 12"/><path d="M18 6L6 18"/>'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  crack: svg('<path d="M12 3.5l-1.6 5.2 3.4 2.1-2.6 4.3 1.4 5.4"/><path d="M10.4 8.7L6.5 7.4"/><path d="M13.8 10.8l3.9-1.6"/><path d="M11.2 15.1l-3.6 1.8"/>'),
  unlink: svg('<path d="M9.5 14.5l-1.8 1.8a3 3 0 0 1-4.2-4.2l1.8-1.8"/><path d="M14.5 9.5l1.8-1.8a3 3 0 0 1 4.2 4.2l-1.8 1.8"/><path d="M8 4v2.5"/><path d="M4 8h2.5"/><path d="M16 20v-2.5"/><path d="M20 16h-2.5"/>'),
  stage: svg('<path d="M6 4.5v10a3 3 0 0 0 3 3h9"/><path d="M15 14.5l3 3-3 3"/>'),
};

export function mountIcons(root = document) {
  for (const el of root.querySelectorAll('i[data-icon]')) {
    el.innerHTML = ICONS[el.dataset.icon] || '';
  }
}
