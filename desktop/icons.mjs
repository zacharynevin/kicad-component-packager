import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {Resvg} from '@resvg/resvg-js';
const root = new URL('./assets/', import.meta.url);
const svg = await fs.readFile(new URL('icon.svg', root));
const png = size => new Resvg(svg,{fitTo:{mode:'width',value:size}}).render().asPng();
await fs.writeFile(new URL('icon.png', root), png(256));
const iconPng = png(256), header = Buffer.alloc(22);
header.writeUInt16LE(1,2);header.writeUInt16LE(1,4);
header.writeUInt16LE(1,10);header.writeUInt16LE(32,12);
header.writeUInt32LE(iconPng.length,14);header.writeUInt32LE(22,18);
await fs.writeFile(new URL('icon.ico', root),Buffer.concat([header,iconPng]));
const chunks = [['icp4',16],['icp5',32],['icp6',64],['ic07',128],['ic08',256],['ic09',512],['ic10',1024]].map(([type,size])=>{
  const data=png(size), chunk=Buffer.alloc(8);chunk.write(type);chunk.writeUInt32BE(data.length+8,4);return Buffer.concat([chunk,data]);
});
const icns = Buffer.alloc(8);icns.write('icns');icns.writeUInt32BE(8+chunks.reduce((n,b)=>n+b.length,0),4);
await fs.writeFile(new URL('icon.icns',root),Buffer.concat([icns,...chunks]));
console.log('Desktop icons generated:',fileURLToPath(root));
