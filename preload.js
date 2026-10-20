// The renderer's only door to the main process: a small, explicit API.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cmu', {
  call: (method, params) => ipcRenderer.invoke('backend:call', method, params),
  on: (channel, callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on(channel, handler);
    return () => ipcRenderer.removeListener(channel, handler);
  },
  settings: {
    get: () => ipcRenderer.invoke('settings:get'),
    set: (patch) => ipcRenderer.invoke('settings:set', patch),
  },
  dialog: {
    open: (options) => ipcRenderer.invoke('dialog:open', options),
    save: (options) => ipcRenderer.invoke('dialog:save', options),
  },
  fs: {
    read: (file) => ipcRenderer.invoke('fs:read', file),
    write: (file, text) => ipcRenderer.invoke('fs:write', file, text),
    writeBase64: (file, data) => ipcRenderer.invoke('fs:writeBase64', file, data),
    readDataUrl: (file) => ipcRenderer.invoke('fs:readDataUrl', file),
    watch: (file) => ipcRenderer.invoke('fs:watch', file),
  },
  open: (target) => ipcRenderer.invoke('shell:open', target),
  win: {
    minimize: () => ipcRenderer.send('win:minimize'),
    maximize: () => ipcRenderer.send('win:maximize'),
    close: () => ipcRenderer.send('win:close'),
  },
  ready: () => ipcRenderer.send('app:ready'),
});
