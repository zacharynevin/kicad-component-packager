/* STEP tessellation is local and happens off the UI thread, once per asset. */
importScripts('/occt-import-js.js');
let engine;
self.onmessage = async ({data:{id,bytes}}) => {
  try {
    engine ||= occtimportjs({locateFile: name => '/' + name});
    const result = (await engine).ReadStepFile(new Uint8Array(bytes), {linearUnit:'millimeter',linearDeflectionType:'absolute_value',linearDeflection:0.05,angularDeflection:0.3});
    if (!result.success || !result.meshes.length) throw new Error('No solid geometry found in this STEP model.');
    self.postMessage({id, meshes:result.meshes});
  } catch (error) {self.postMessage({id,error:error.message || String(error)});}
};
