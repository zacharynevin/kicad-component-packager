// Replace Emscripten's two generated invokers with ordinary closures.
// No string compilation is needed for the synchronous occt-import-js API.
export function cspRuntime(source) {
  const start=source.indexOf('function createJsInvoker('),end=source.indexOf('var ensureOverloadTable=',start);
  const methodStart=source.indexOf('var __emval_get_method_caller='),methodEnd=source.indexOf('var __emval_get_property=',methodStart);
  if([start,end,methodStart,methodEnd].some(i=>i<0))throw new Error('Review OCCT runtime adapters for this dependency version.');
  const craft=`function craftInvokerFunction(humanName,argTypes,classType,cppInvokerFunc,cppTargetFunc,isAsync){
    if(isAsync)throw new Error('Asynchronous embind calls are not supported by this adapter');
    if(argTypes.length<2)throwBindingError('Missing embind return/this types');
    var isMethod=argTypes[1]!==null&&classType!==null;
    var stacked=usesDestructorStack(argTypes);
    return createNamedFunction(humanName,function(...args){
      var destructors=stacked?[]:null, wired=[cppTargetFunc], converted=[];
      if(isMethod){var v=argTypes[1].toWireType(destructors,this);wired.push(v);converted.push([argTypes[1],v]);}
      for(var i=2;i<argTypes.length;i++){var v=argTypes[i].toWireType(destructors,args[i-2]);wired.push(v);converted.push([argTypes[i],v]);}
      var result=cppInvokerFunc(...wired);
      if(stacked)runDestructors(destructors);else for(var [type,value] of converted)if(type.destructorFunction!==null)type.destructorFunction(value);
      if(argTypes[0].name!=='void')return argTypes[0].fromWireType(result);
    });
  }`;
  const method=`var __emval_get_method_caller=(argCount,argTypes,kind)=>{
    var types=emval_lookupTypes(argCount,argTypes),retType=types.shift();
    return emval_addMethodCaller(function(obj,func,destructorsRef,args){
      var values=[],offset=0;
      for(var type of types){values.push(type.readValueFromPointer(args+offset));offset+=type.argPackAdvance;}
      var result=kind===1?Reflect.construct(func,values):func.call(...(kind===0?[obj]:[]),...values);
      if(!retType.isVoid)return emval_returnValue(retType,destructorsRef,result);
    });
  };`;
  return source.slice(0,start)+craft+source.slice(end,methodStart)+method+source.slice(methodEnd);
}
