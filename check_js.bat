@echo off
setlocal
set NODE=C:\Program Files\nodejs\node.exe
rem 1) node --check on every static/*.js (includes static/vm_wave.js)
for %%f in (static\*.js) do "%NODE%" --check %%f
if errorlevel 1 exit /b 1
rem 2) node --check on every inline <script> block of index.html, plus a
rem    start/stop/cleanup simulation of static/vm_wave.js with a stub DOM.
"%NODE%" -e "const fs=require('fs'),cp=require('child_process');const html=fs.readFileSync('index.html','utf8');let n=0,pos=0;while(true){const si=html.indexOf('<script',pos);if(si<0)break;const gt=html.indexOf('>',si);if(gt<0)break;const attrs=html.slice(si+7,gt);const es=html.indexOf('</script>',gt+1);if(es<0){console.log('UNTERMINATED');process.exit(1);}const sc=html.slice(gt+1,es);pos=es+9;if(attrs.indexOf('src=')>=0){n++;continue;}const scs=sc.trim();if(!scs||scs.startsWith('{')||scs.indexOf(String.fromCharCode(34)+'imports'+String.fromCharCode(34))>=0){n++;continue;}const tmp='__vm_inline__.js';fs.writeFileSync(tmp,scs);try{cp.execFileSync(process.execPath,['--check',tmp],{encoding:'utf8'});}catch(e){console.log('FAIL inline block '+n);process.exit(1);}n++;}try{fs.unlinkSync(tmp);}catch(e){}console.log('INLINE_BLOCKS '+n);global.window=global;global.document={getElementById:function(id){var c={style:{},classList:{add:function(){},remove:function(){}},children:[],appendChild:function(){return null},querySelectorAll:function(){return []}};return (id==='vmWaveContainer'||id==='vmWaveBars')?c:null;},createElement:function(){return {style:{},textContent:'',id:'',appendChild:function(){}};},getElementsByTagName:function(){return [{appendChild:function(){}}];}};global.navigator={mediaDevices:{getUserMedia:function(){return Promise.reject(new Error('nomic'))}}};var vm=require('./static/vm_wave.js');vm.start('user');vm.start('ai',{play:function(){return Promise.resolve();},pause:function(){}});vm.stop();console.log('CLEANUP_OK');"
if errorlevel 1 exit /b 1
rem 3) runtime test: call silence/micStop/assistantStop with uninitialised bars
"%NODE%" -e "const fs=require('fs');const h=fs.readFileSync('index.html','utf8');const s=h.indexOf('var vmWaveform');const nl=String.fromCharCode(10);const e=h.indexOf(nl+'})();',s);let c=h.slice(s,e+5);c=c.replace('var vmWaveform','global.vmWaveform');global.window={};global.document={addEventListener:function(){},hidden:false,getElementById:function(){return null;}};global.navigator={};eval(c);try{vmWaveform.micStop();vmWaveform.assistantStop();vmWaveform.cleanup();console.log('UNINIT_OK');}catch(err){console.log('FAIL uninit: '+err.message);process.exit(1);}"
if errorlevel 1 exit /b 1
echo ALL_CHECKS_PASS
