import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';

await mkdir('ctrl/static', { recursive: true });
await build({
  entryPoints: ['web/app.ts'],
  bundle: true,
  outfile: 'ctrl/static/app.js',
  minify: true,
  target: ['chrome120'],
  legalComments: 'none',
});
await copyFile('web/index.html', 'ctrl/static/index.html');
await copyFile('web/styles.css', 'ctrl/static/styles.css');
console.log('Built [CTRL] interface → ctrl/static');
