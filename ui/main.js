const { app, BrowserWindow, session } = require('electron');
const path = require('path');

const createWindow = () => {
  // Conservative Content-Security-Policy: allow the local backend, Google
  // Fonts (for Inter), and nothing else.  Inline styles are needed by the
  // renderer for dynamic element construction.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; " +
          "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* http://localhost:* ws://localhost:*; " +
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
          "font-src 'self' https://fonts.gstatic.com; " +
          "script-src 'self'; " +
          "img-src 'self' data:;"
        ],
      },
    });
  });

  // Platform-appropriate icon
  let iconPath;
  if (process.platform === 'darwin') {
    iconPath = path.join(__dirname, '..', 'assets', 'logo.icns');
  } else if (process.platform === 'win32') {
    iconPath = path.join(__dirname, '..', 'assets', 'logo.ico');
  } else {
    iconPath = path.join(__dirname, '..', 'assets', 'logo.png');
  }

  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: 'EdgePilot Console',
    icon: iconPath,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }
};

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
